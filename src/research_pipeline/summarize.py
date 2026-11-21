"""Summary and critique schemas, the citation check, and per-paper summarisation.

The model writes claims that cite paper sections; code (not the model) checks every cited
title against the real section titles. See docs/guide/summaries.md.
"""

from typing import Literal, TypeVar

from pydantic import BaseModel, Field

from research_pipeline.llm import LLM
from research_pipeline.models import Section
from research_pipeline.prompts import (
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
