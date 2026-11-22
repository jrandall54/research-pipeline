"""Summary and critique schemas, the citation check, and per-paper summarisation.

The model writes claims that cite paper sections; code (not the model) checks every cited
title against the real section titles. See docs/guide/summaries.md.
"""

import sqlite3
from collections.abc import Callable
from datetime import date
from typing import Literal, TypeVar

from pydantic import BaseModel, Field

from research_pipeline.fulltext import FullTextUnavailable, ensure_usable
from research_pipeline.llm import LLM, LlmError
from research_pipeline.models import Section, Status
from research_pipeline.sources.base import SourceError
from research_pipeline.prompts import (
    PROMPT_VERSION,
    build_critique_prompt,
    build_summary_prompt,
    citation_feedback,
    fit_to_budget,
)


class Claim(BaseModel):
    """One statement about the paper and the section titles it comes from."""

    text: str
    sections: list[str] = Field(min_length=1)


class Summary(BaseModel):
    one_line: str
    problem: Claim
    approach: Claim
    results: Claim
    limitations: Claim


class Critique(BaseModel):
    strengths: list[Claim]
    weaknesses: list[Claim]
    questions: list[str]


class PaperSummary(BaseModel):
    summary: Summary
    critique: Critique
    basis: Literal["full_text", "abstract"]
    truncated: bool
    unverified: list[str]  # claim texts whose citations could not be verified


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
) -> tuple[dict[str, int], list[str]]:
    """Summarise the papers that need it: full text (stored, else fetched), then the model.

    Works on `triaged` and `fetched` papers plus papers in *today's* digest that have no
    summary yet (they stay `in_digest`; the summary is just saved), skipping papers that
    failed 3 times. A paper with no HTML version is summarised from its abstract. A paper
    that fails (model error, network error) keeps its status, gets `error` and
    `retry_count` updated, and the others carry on. Commits per paper.
    See docs/guide/running.md.
    """
    # Imported here because db.py imports the schemas defined above.
    from research_pipeline.db import (
        clear_error,
        get_fulltext,
        papers_given_up_on,
        papers_to_summarize,
        record_failure,
        save_fulltext,
        save_summary,
        set_status,
    )

    counts = {"fetched": 0, "summarized": 0, "abstract_only": 0, "truncated": 0, "summary_failed": 0}
    errors: list[str] = []
    today = today or date.today()
    for paper in papers_given_up_on(conn, today):
        errors.append(
            f"summarize {paper['id']}: gave up after {paper['retry_count']} failed tries"
            f" (last error: {paper['error']})"
        )
    for paper in papers_to_summarize(conn, today):
        pid = paper["id"]
        listed = paper["status"] == Status.in_digest.value  # already in today's digest
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
            )
            clear_error(conn, pid)
            if listed:
                conn.commit()  # keep `in_digest`; the digest rewrite picks the summary up
            else:
                set_status(conn, pid, Status.summarized)  # commits the summary rows too
            counts["summarized"] += 1
            counts["truncated"] += int(result.truncated)
        except Exception as exc:  # noqa: BLE001 - one bad paper must not stop the rest
            counts["summary_failed"] += 1
            message = f"{type(exc).__name__}: {exc}" if not isinstance(exc, (LlmError, SourceError)) else str(exc)
            record_failure(conn, pid, message)
            errors.append(f"summarize {pid}: {' '.join(message.split())}")
    return counts, errors
