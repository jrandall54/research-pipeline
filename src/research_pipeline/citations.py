"""The citation check: code (not the model) checks every cited section title.

Used by whole-paper summaries (summarize.py) and by reading a paper in parts (parts.py), so it
lives below both. See docs/guide/summaries.md.
"""

from collections.abc import Callable
from typing import TypeVar

from research_pipeline.llm import LLM
from research_pipeline.prompts import citation_feedback
from research_pipeline.summary_models import Claim, Critique, Notes, Summary

T = TypeVar("T", Summary, Critique, Notes)


def norm_title(title: str) -> str:
    return title.strip().casefold()


def claims(obj: Summary | Critique | Notes) -> list[Claim]:
    """Every claim that cites sections (a critique's questions cite nothing)."""
    if isinstance(obj, Summary):
        return [obj.problem, obj.approach, obj.results, obj.limitations]
    if isinstance(obj, Notes):
        return list(obj.claims)
    return [*obj.strengths, *obj.weaknesses]


def unknown_citations(obj: Summary | Critique | Notes, titles: list[str]) -> list[str]:
    """Cited titles that match no section (ignoring case and surrounding whitespace)."""
    known = {norm_title(t) for t in titles}
    bad: list[str] = []
    for claim in claims(obj):
        for cited in claim.sections:
            if norm_title(cited) not in known and cited not in bad:
                bad.append(cited)
    return bad


def generate_checked(  # noqa: UP047 (module-level constrained TypeVar T is shared)
    llm: LLM, system: str, user: str, schema: type[T], titles: list[str],
    on_retry: Callable[[], None] | None = None,
) -> tuple[T, list[str]]:
    """Generate, check citations, retry once with feedback; return result and unverified texts."""
    result = llm.generate(system, user, schema)
    bad = unknown_citations(result, titles)
    if bad:
        if on_retry is not None:
            on_retry()
        result = llm.generate(system, user + citation_feedback(bad, titles), schema)
    known = {norm_title(t) for t in titles}
    unverified = [
        c.text for c in claims(result) if any(norm_title(s) not in known for s in c.sections)
    ]
    return result, unverified
