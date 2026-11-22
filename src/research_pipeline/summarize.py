"""Summary and critique schemas, the citation check, and per-paper summarisation.

The model writes claims that cite paper sections; code (not the model) checks every cited
title against the real section titles. See docs/guide/summaries.md.
"""

import sqlite3
from collections.abc import Callable
from datetime import date
from typing import Literal, TypeVar

from research_pipeline.fulltext import FullTextUnavailable, ensure_usable
from research_pipeline.llm import LLM, LlmError, LlmLimitReached, LlmTimeout
from research_pipeline.models import Section, Status
from research_pipeline.summary_models import Claim, Critique, PaperSummary, Summary
from research_pipeline.sources.base import SourceError
from research_pipeline.prompts import (
    PROMPT_VERSION,
    build_critique_prompt,
    build_summary_prompt,
    citation_feedback,
    fit_to_budget,
)


T = TypeVar("T", Summary, Critique)


def _norm(title: str) -> str:
    return title.strip().casefold()


def claims(obj: Summary | Critique) -> list[Claim]:
    if isinstance(obj, Summary):
        return [obj.problem, obj.approach, obj.results, obj.limitations]
    return [*obj.strengths, *obj.weaknesses]


def unknown_citations(obj: Summary | Critique, titles: list[str]) -> list[str]:
    """Cited titles that match no section (ignoring case and surrounding whitespace)."""
    known = {_norm(t) for t in titles}
    bad: list[str] = []
    for claim in claims(obj):
        for cited in claim.sections:
            if _norm(cited) not in known and cited not in bad:
                bad.append(cited)
    return bad


def _generate_checked(
    llm: LLM, system: str, user: str, schema: type[T], titles: list[str]
) -> tuple[T, list[str]]:
    """Generate, check citations, retry once with feedback; return result and unverified texts."""
    result = llm.generate(system, user, schema)
    bad = unknown_citations(result, titles)
    if bad:
        result = llm.generate(system, user + citation_feedback(bad, titles), schema)
    known = {_norm(t) for t in titles}
    unverified = [
        c.text for c in claims(result) if any(_norm(s) not in known for s in c.sections)
    ]
    return result, unverified


def summarize_paper(
    llm: LLM, title: str, abstract: str, sections: list[Section] | None, num_ctx: int
) -> PaperSummary:
    """Summary and critique of one paper. ``sections=None`` means abstract only.

    Raises LlmError if the model fails; a bad citation never raises, it is flagged.
    """
    basis: Literal["full_text", "abstract"] = "full_text"
    if sections is None:
        sections, basis = [Section(title="Abstract", text=abstract)], "abstract"
    fitted, truncated = fit_to_budget(sections, num_ctx)
    titles = [s.title for s in fitted]

    summary, bad_s = _generate_checked(
        llm, *build_summary_prompt(title, fitted, basis), Summary, titles
    )
    critique, bad_c = _generate_checked(
        llm, *build_critique_prompt(title, fitted, basis), Critique, titles
    )
    return PaperSummary(
        summary=summary,
        critique=critique,
        basis=basis,
        truncated=truncated,
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
    See docs/guide/running.md.
    """
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

    counts = {"fetched": 0, "summarized": 0, "abstract_only": 0, "truncated": 0, "summary_failed": 0}
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
    for paper in queue:
        pid = paper["id"]
        # Only triaged/fetched papers move on to `summarized`; every other status stays.
        listed = paper["status"] not in (Status.triaged.value, Status.fetched.value)
        try:
            sections = get_fulltext(conn, pid)
            if sections is None:
                try:
                    sections = ensure_usable(fetch(pid))
                    save_fulltext(conn, pid, sections)
                except FullTextUnavailable:
                    sections = None
            if not listed:
                set_status(conn, pid, Status.fetched)
            counts["fetched" if sections is not None else "abstract_only"] += 1
            result = summarize_paper(llm, paper["title"], paper["abstract"], sections, num_ctx)
            save_summary(
                conn, pid, result, llm.model_name,
                prompt_version=PROMPT_VERSION, paper_version=paper["version"], num_ctx=llm.num_ctx,
                backend=getattr(llm, "name", None),
            )
            clear_error(conn, pid)
            if listed:
                conn.commit()  # keep `in_digest`; the digest rewrite picks the summary up
            else:
                set_status(conn, pid, Status.summarized)  # commits the summary rows too
            counts["summarized"] += 1
            counts["truncated"] += int(result.truncated)
        except LlmLimitReached:
            # Not the paper's fault: no retry is burned, it stays queued for the next run.
            if paper["status"] == Status.triaged.value:
                set_status(conn, pid, Status.triaged)  # undo the early `fetched` mark
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
            if isinstance(exc, LlmTimeout):
                # A hung model would make every remaining paper wait out the full timeout.
                errors.append(
                    "summarize: stopped calling the model after the first timeout;"
                    " remaining papers show abstracts only"
                )
                break
    return counts, errors
