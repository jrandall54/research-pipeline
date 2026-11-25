"""The pipeline's side of the request list: do the work the app asked for. See docs/guide/running.md.

The app (``library_api.request``) only writes `pending` rows to the ``requests`` table; this
module, run by the nightly `rpipe run` and by `rpipe requests run` under the run lock, works
through them. Today the only kind is ``summarize``: summarize one paper now, even one triage
skipped. Each request gets at most ``MAX_SUMMARY_TRIES`` tries (``requests.tries`` counts them);
a usage limit stops the drain without spending a try, a model timeout stops it after counting one.

This is a pipeline module: it must not import the app's modules (library_api, shelf, review, serve).
"""

import json
import sqlite3
from collections.abc import Callable
from typing import Literal

from research_pipeline.db import (
    MAX_SUMMARY_TRIES,
    mark_request,
    requests_by_status,
    reset_running_requests,
)
from research_pipeline.llm import LLM
from research_pipeline.models import Section
from research_pipeline.progress import ProgressEvent, Reporter, StageHandle, stage
from research_pipeline.summarize import LIMIT_STOPPED, TIMEOUT_STOPPED, run_summarize


class _NotesOnly:
    """Reporter for the inner one-paper `run_summarize`: pass its notes ("fetching…") to the
    `requests` stage and drop its own start, `[1/1]` step and done, so the screen shows one
    `[i/N]` line per request rather than a stage per request."""

    def __init__(self, handle: StageHandle) -> None:
        self._handle = handle

    def emit(self, event: ProgressEvent) -> None:
        if event.kind == "note":
            self._handle.note(event.label)


def _paper_error(pid: str, errors: list[str]) -> str:
    """The line run_summarize wrote about this paper, without its ``summarize <id>:`` prefix."""
    prefix = f"summarize {pid}: "
    for e in errors:
        if e.startswith(prefix):
            return e.removeprefix(prefix)
    return "; ".join(errors) or "not summarized"


def drain_requests(
    conn: sqlite3.Connection,
    llm: LLM,
    fetch: Callable[[str], list[Section]],
    num_ctx: int,
    *,
    reporter: Reporter | None = None,
    mode: Literal["whole", "split"] = "whole",
    fallback_above: int | None = None,
) -> tuple[dict[str, int], list[str]]:
    """Work through the pending requests, oldest first; return (counts, error lines).

    1. Requests left `running` by a run that died are put back to `pending`.
    2. Each `pending` ``summarize`` request with fewer than MAX_SUMMARY_TRIES tries is marked
       `running` (one try counted) and its paper summarized with ``run_summarize(paper_ids=[id])``
       (``mode`` and ``fallback_above`` as for the summarize stage).
    3. Summarized → `done`. Failed → back to `pending` with the error, or `failed` on the last
       try. Usage limit → back to `pending` with the try given back, and the drain stops.
       Model timeout → counted as a failure, and the drain stops (a hung model would make every
       later request wait out the timeout too).

    Counts: ``requests_done``, ``requests_failed`` (this drain) and ``requests_waiting``
    (pending rows left afterwards). Errors are run_summarize's own lines, for the health line.
    Progress: one ``requests`` stage, a step per request; nothing at all when none are queued.
    """
    reset_running_requests(conn)
    todo = [r for r in requests_by_status(conn, "pending")
            if r["kind"] == "summarize" and r["tries"] < MAX_SUMMARY_TRIES]
    counts = {"requests_done": 0, "requests_failed": 0, "requests_waiting": 0}
    errors: list[str] = []
    if todo:
        with stage(reporter, "requests", total=len(todo)) as handle:
            for i, row in enumerate(todo, start=1):
                rid = row["id"]
                pid = str(json.loads(row["payload"]).get("paper_id", ""))
                mark_request(conn, rid, "running", add_try=True)
                tries = row["tries"] + 1
                try:
                    got, errs = run_summarize(
                        conn, llm, fetch, num_ctx, paper_ids=[pid], reporter=_NotesOnly(handle),
                        mode=mode, fallback_above=fallback_above,
                    )
                except Exception as exc:  # noqa: BLE001 - e.g. the paper is gone; fail this request only
                    got, errs = {}, [f"summarize {pid}: {' '.join(str(exc).split()) or type(exc).__name__}"]
                errors += errs
                if got.get("summarized") == 1:
                    mark_request(conn, rid, "done", result={"summarized": True})
                    counts["requests_done"] += 1
                    handle.step(pid, current=i, outcome="done")
                    continue
                if LIMIT_STOPPED in errs:
                    mark_request(conn, rid, "pending", error=row["error"], undo_try=True)
                    handle.step(pid, current=i, outcome="stopped")
                    break
                message = _paper_error(pid, errs)
                last = tries >= MAX_SUMMARY_TRIES
                mark_request(conn, rid, "failed" if last else "pending", error=message)
                counts["requests_failed"] += int(last)
                handle.step(pid, current=i, outcome="failed", detail=message[:120])
                if TIMEOUT_STOPPED in errs:
                    break
    counts["requests_waiting"] = len(requests_by_status(conn, "pending"))
    return counts, errors
