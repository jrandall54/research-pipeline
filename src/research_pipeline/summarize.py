"""Per-paper summarisation (summary and critique) and the summarize stage.

The model writes claims that cite paper sections; code (not the model) checks every cited
title against the real section titles (citations.py). See docs/guide/summaries.md.
"""

import sqlite3
import time
from collections.abc import Callable
from datetime import date
from typing import Literal

# `claims` and `unknown_citations` are re-exported: callers and tests import them from here.
from research_pipeline.citations import claims, generate_checked, unknown_citations  # noqa: F401
from research_pipeline.fulltext import FullTextUnavailable, ensure_usable
from research_pipeline.llm import LLM, LlmError, LlmLimitReached, LlmTimeout
from research_pipeline.models import Section, Status
from research_pipeline.parts import MIN_PARTS_CTX, read_in_parts, split_groups
from research_pipeline.progress import Reporter, stage
from research_pipeline.prompts import (
    PROMPT_VERSION,
    build_critique_prompt,
    build_summary_prompt,
)
from research_pipeline.pruning import fit_to_budget, partition
from research_pipeline.sources.base import SourceError
from research_pipeline.summary_models import (  # noqa: F401 (re-exported)
    Claim,
    Critique,
    Dropped,
    PaperSummary,
    Summary,
)

# The error line when the model timed out and the stage stopped (`rpipe run` then skips reruns).
TIMEOUT_STOPPED = (
    "summarize: stopped calling the model after the first timeout; remaining papers show abstracts only"
)


def _gpu_share(llm: LLM) -> float | None:
    """Share of the model on the GPU right after a summary, if the backend can say; never raises."""
    ask = getattr(llm, "gpu_share", None)
    if ask is None:
        return None
    try:
        share = ask()
    except Exception:  # noqa: BLE001 - a record of where the model sat must never fail a summary
        return None
    return share if isinstance(share, (int, float)) and not isinstance(share, bool) else None


def phase_note(name: str, pid: str) -> str:
    """Progress note for a phase `summarize_paper` reports (``notes 2/3`` → reading part 2 of 3)."""
    if name.startswith("notes "):
        part, of = name.removeprefix("notes ").split("/")
        return f"reading part {part} of {of} of {pid}"
    label = {"summary": "writing the summary for", "critique": "writing the critique for",
             "condense": "condensing notes for"}
    return f"{label.get(name, 'retrying citations for')} {pid}"


def summarize_paper(
    llm: LLM, title: str, abstract: str, sections: list[Section] | None, num_ctx: int,
    on_phase: Callable[[str], None] | None = None,
    *,
    mode: Literal["whole", "split"] = "whole",
    fallback_above: int | None = None,
) -> PaperSummary:
    """Summary and critique of one paper. ``sections=None`` means abstract only.

    ``mode="whole"`` reads the paper in one call per output (cut to fit the context if needed).
    ``mode="split"`` reads front matter and body as separate parts, then combines the notes
    (parts.py). An abstract-only paper, a paper with no paper sections left after pruning, or a
    context below `MIN_PARTS_CTX` is always read whole.
    In whole mode, a paper whose core text had to be cut is read in size-based parts instead
    (map-reduce, appendices left out) when the context it needs is above ``fallback_above``;
    ``None`` never falls back (reruns pass it: they exist to read papers whole), ``0`` always
    does. See "Reading a paper in parts" in docs/guide/summaries.md.

    ``on_phase(name)`` is told ``summary`` and ``critique``, and ``retry`` when a
    citation retry runs, just before each model call; reading in parts also tells it
    ``notes i/N`` and ``condense``.

    Raises LlmError if the model fails; a bad citation never raises, it is flagged.
    """
    basis: Literal["full_text", "abstract"] = "full_text"
    if sections is None:
        sections, basis = [Section(title="Abstract", text=abstract)], "abstract"
    in_parts_ok = basis == "full_text" and num_ctx >= MIN_PARTS_CTX

    if mode == "split" and in_parts_ok:
        groups, dropped = split_groups(sections, num_ctx)
        if any(secs for _, secs in groups):  # else nothing but non-paper text: read it whole
            return read_in_parts(llm, title, groups, num_ctx, read_mode="split",
                                 dropped=dropped, on_phase=on_phase)

    fit = fit_to_budget(sections, num_ctx)
    if (in_parts_ok and fit.body_cut and fallback_above is not None
            and fit.needed_ctx is not None and fit.needed_ctx > fallback_above):
        # No rerun can read this paper whole: read all of its core text in parts instead.
        paper, appendix, _ = partition(sections)
        if paper:
            # Left out, in paper order: non-paper text, and the appendices (core text first).
            read, is_appendix = {id(s) for s in paper}, {id(s) for s in appendix}
            dropped = [
                Dropped(title=s.title, chars=len(s.title) + len(s.text),
                        reason="appendix" if id(s) in is_appendix else "non_paper")
                for s in sections if id(s) not in read
            ]
            return read_in_parts(llm, title, [("general", paper)], num_ctx, read_mode="map_reduce",
                                 dropped=dropped, on_phase=on_phase)

    fitted = fit.sections
    titles = [s.title for s in fitted]

    def phase(name: str) -> Callable[[], None] | None:
        return (lambda: on_phase(name)) if on_phase is not None else None

    if on_phase is not None:
        on_phase("summary")
    summary, bad_s = generate_checked(
        llm, *build_summary_prompt(title, fitted, basis), Summary, titles, phase("retry")
    )
    if on_phase is not None:
        on_phase("critique")
    critique, bad_c = generate_checked(
        llm, *build_critique_prompt(title, fitted, basis), Critique, titles, phase("retry")
    )
    return PaperSummary(
        summary=summary,
        critique=critique,
        basis=basis,
        body_cut=fit.body_cut,
        needed_ctx=fit.needed_ctx,
        dropped=fit.dropped,
        unverified=bad_s + bad_c,
    )


def run_summarize(
    conn: sqlite3.Connection,
    llm: LLM,
    fetch: Callable[[str], list[Section]],
    num_ctx: int,
    today: date | None = None,
    backlog_limit: int | None = None,
    paper_ids: list[str] | None = None,
    *,
    reporter: Reporter | None = None,
    refetch: bool = False,
    mode: Literal["whole", "split"] = "whole",
    fallback_above: int | None = None,
) -> tuple[dict[str, int], list[str]]:
    """Summarise the papers that need it: full text (stored, else fetched), then the model.

    Works on `triaged` and `fetched` papers plus papers in *today's* digest that have no
    summary yet (they stay `in_digest`; the summary is just saved), skipping papers that
    failed 3 times. A paper with no HTML version is summarised from its abstract. A paper
    that fails (model error, network error) keeps its status, gets `error` and
    `retry_count` updated, and the others carry on. Commits per paper.
    ``backlog_limit=N`` also summarises up to N papers from earlier digests that have no
    summary (newest first). ``paper_ids`` instead summarises exactly those papers (any status,
    ignoring the retry cap, replacing an existing summary). `in_digest`, `skipped` and any
    other status except `triaged`/`fetched` is kept; the summary is just saved.
    Progress goes to ``reporter`` (None = silent) inside one ``summarize`` stage: a note before
    each step (fetching, writing the summary, writing the critique, retrying citations) and one
    ``step`` event per paper as it finishes (outcome ``done``, ``failed`` or ``stopped``; detail
    is the per-paper time on ``done``, else the short failure reason); the total counts every
    queued paper, backlog and explicit ids included.
    ``refetch=True`` (needs ``paper_ids``) re-downloads and re-parses each paper's full text
    and replaces the stored sections first; if the HTML is no longer usable the stored text
    is kept (else the abstract is used) and a note is added to the errors.
    ``mode`` and ``fallback_above`` are passed to `summarize_paper` for every paper; counts
    ``in_parts`` the papers that fell back to map-reduce (cut papers read in parts).
    See docs/guide/running.md.
    """
    if refetch and paper_ids is None:
        raise ValueError("refetch needs explicit paper ids")
    # Imported here because db.py imports the schemas defined above.
    from research_pipeline.db import (
        clear_error,
        get_fulltext,
        papers_by_ids,
        papers_given_up_on,
        papers_missing_summary,
        papers_to_summarize,
        record_failure,
        save_fulltext,
        save_summary,
        set_status,
    )

    counts = {"fetched": 0, "summarized": 0, "abstract_only": 0, "body_cut": 0, "summary_failed": 0,
              "in_parts": 0}
    errors: list[str] = []
    today = today or date.today()
    if paper_ids is not None:
        queue = papers_by_ids(conn, paper_ids)
    else:
        for paper in papers_given_up_on(conn, today):
            errors.append(
                f"summarize {paper['id']}: gave up after {paper['retry_count']} failed tries"
                f" (last error: {paper['error']})"
            )
        queue = papers_to_summarize(conn, today)
        if backlog_limit is not None:
            queue = queue + papers_missing_summary(conn, today, backlog_limit)
    total = len(queue)
    with stage(reporter, "summarize", total=total) as handle:
        for index, paper in enumerate(queue, start=1):
            pid = paper["id"]
            started = time.monotonic()

            def report(outcome: str, detail: str = "") -> None:
                if outcome == "done":
                    detail = f"{time.monotonic() - started:.0f}s"  # this paper's time, not the stage's  # noqa: B023
                handle.step(pid, current=index, outcome=outcome, detail=detail)  # noqa: B023 (called within the same iteration)

            # Only triaged/fetched papers move on to `summarized`; every other status stays.
            listed = paper["status"] not in (Status.triaged.value, Status.fetched.value)
            try:
                sections = get_fulltext(conn, pid)
                if refetch or sections is None:
                    handle.note(f"fetching {pid}")
                if refetch:
                    try:
                        sections = ensure_usable(fetch(pid))
                        save_fulltext(conn, pid, sections)
                    except FullTextUnavailable:
                        kept = "kept the stored full text" if sections is not None else "using the abstract"
                        errors.append(f"summarize {pid}: no usable HTML version now; {kept}")
                    except SourceError as exc:
                        if sections is None:
                            raise  # nothing stored to fall back on: a normal failure, retried later
                        errors.append(f"summarize {pid}: could not re-download ({exc}); kept the stored full text")
                elif sections is None:
                    try:
                        sections = ensure_usable(fetch(pid))
                        save_fulltext(conn, pid, sections)
                    except FullTextUnavailable:
                        sections = None
                if not listed:
                    set_status(conn, pid, Status.fetched)
                counts["fetched" if sections is not None else "abstract_only"] += 1
                def on_phase(name: str) -> None:
                    handle.note(phase_note(name, pid))  # noqa: B023

                result = summarize_paper(
                    llm, paper["title"], paper["abstract"], sections, num_ctx, on_phase=on_phase,
                    mode=mode, fallback_above=fallback_above,
                )
                save_summary(
                    conn, pid, result, llm.model_name,
                    prompt_version=PROMPT_VERSION, paper_version=paper["version"], num_ctx=llm.num_ctx,
                    backend=getattr(llm, "name", None), gpu_share=_gpu_share(llm),
                )
                clear_error(conn, pid)
                if listed:
                    conn.commit()  # keep `in_digest`; the digest rewrite picks the summary up
                else:
                    set_status(conn, pid, Status.summarized)  # commits the summary rows too
                counts["summarized"] += 1
                counts["body_cut"] += int(result.body_cut)
                counts["in_parts"] += int(result.read_mode == "map_reduce")
                report("done")
            except LlmLimitReached:
                # Not the paper's fault: no retry is burned, it stays queued for the next run.
                if paper["status"] == Status.triaged.value:
                    set_status(conn, pid, Status.triaged)  # undo the early `fetched` mark
                report("stopped")
                errors.append(
                    "summarize: summaries stopped: usage limit reached;"
                    " remaining papers wait for the next run"
                )
                break
            except Exception as exc:  # noqa: BLE001 - one bad paper must not stop the rest
                counts["summary_failed"] += 1
                message = f"{type(exc).__name__}: {exc}" if not isinstance(exc, (LlmError, SourceError)) else str(exc)
                record_failure(conn, pid, message)
                errors.append(f"summarize {pid}: {' '.join(message.split())}")
                report("failed", " ".join(message.split())[:120])
                if isinstance(exc, LlmTimeout):
                    # A hung model would make every remaining paper wait out the full timeout.
                    errors.append(TIMEOUT_STOPPED)
                    break
    return counts, errors
