"""The citation check: code (not the model) checks every cited section title.

Used by whole-paper summaries (summarize.py) and by reading a paper in parts (parts.py), so it
lives below both. See docs/guide/summaries.md.
"""

from collections.abc import Callable
from typing import TypeVar

from research_pipeline.llm import LLM
from research_pipeline.models import Section
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


def extra_claims(summary: Summary) -> list[Claim]:
    """The hook and one claim per key number: checked by extras.py, not part of `claims`."""
    out = [summary.hook] if summary.hook is not None else []
    out += [Claim(text=f"{k.value} {k.label}", sections=k.sections) for k in summary.key_numbers]
    return out


def section_texts(titles: list[str], sections: list[Section], abstract: str) -> list[str] | None:
    """The text of each cited title (matched with `norm_title`); None if any title is unknown.

    The title "Abstract" also matches the paper's abstract, even with no Abstract section.
    """
    texts: list[str] = []
    for cited in titles:
        key = norm_title(cited)
        found = [s.text for s in sections if norm_title(s.title) == key]
        if key == "abstract":
            found.append(abstract)
        if not found:
            return None
        texts.extend(found)
    return texts


def unknown_citations(
    obj: Summary | Critique | Notes, titles: list[str], *, extras: bool = False
) -> list[str]:
    """Cited titles that match no section (ignoring case and surrounding whitespace).

    ``extras=True`` also looks at a summary's hook and key numbers.
    """
    known = {norm_title(t) for t in titles}
    bad: list[str] = []
    cited_claims = claims(obj)
    if extras and isinstance(obj, Summary):
        cited_claims = [*cited_claims, *extra_claims(obj)]
    for claim in cited_claims:
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
    bad = unknown_citations(result, titles, extras=schema is Summary)
    if bad:
        if on_retry is not None:
            on_retry()
        result = llm.generate(system, user + citation_feedback(bad, titles), schema)
    known = {norm_title(t) for t in titles}
    unverified = [
        c.text for c in claims(result) if any(norm_title(s) not in known for s in c.sections)
    ]
    return result, unverified
