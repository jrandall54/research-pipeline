"""Guided onboarding: turn a free-text description into proposed topics and seed papers.

The local model only PROPOSES. Topics are shown for per-item approval; paper titles are kept
only if they resolve to a real paper via arXiv search (model-invented titles are dropped).
See docs/guide/profile.md ("Guided suggestions").
"""

import re

from pydantic import BaseModel, Field

from research_pipeline.llm import LLM
from research_pipeline.models import PaperMeta
from research_pipeline.prompts import ONBOARDING_SYSTEM
from research_pipeline.seed_input import find_seed_candidates
from research_pipeline.sources.arxiv import ArxivSource

MIN_DESCRIPTION = 20
MIN_TITLE_OVERLAP = 0.6  # share of the proposed title's words that the arXiv title must contain


class ProposedProfile(BaseModel):
    topics: list[str] = Field(default_factory=list)
    seed_queries: list[str] = Field(default_factory=list)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.casefold()))


def _defuse(text: str) -> str:
    return re.sub(r"<(/?)(description)", r"&lt;\1\2", text, flags=re.IGNORECASE)


def propose(llm: LLM, description: str) -> ProposedProfile:
    """One model call. Raises ValueError for a description under 20 characters and LlmError
    if the model fails."""
    text = " ".join(description.split())
    if len(text) < MIN_DESCRIPTION:
        raise ValueError(f"Describe your research in at least {MIN_DESCRIPTION} characters.")
    user = f"<description>\n{_defuse(text)}\n</description>"
    result = llm.generate(ONBOARDING_SYSTEM, user, ProposedProfile)
    return ProposedProfile(
        topics=[t for t in result.topics if t.strip()][:8],
        seed_queries=[q for q in result.seed_queries if q.strip()][:10],
    )


def _best_match(query: str, candidates: list[PaperMeta]) -> PaperMeta | None:
    wanted = _words(query)
    if not wanted:
        return None
    for meta in candidates:  # arXiv's own ranking decides ties
        if len(wanted & _words(meta.title)) / len(wanted) >= MIN_TITLE_OVERLAP:
            return meta
    return None


def resolve_seeds(
    queries: list[str], arxiv: ArxivSource
) -> list[tuple[str, PaperMeta | None]]:
    """Look each proposed title up on arXiv. No close match gives None. SourceError propagates."""
    out: list[tuple[str, PaperMeta | None]] = []
    for q in queries:
        try:
            candidates = find_seed_candidates(q, arxiv)
        except ValueError:  # looked like a bad link: not a title we can search
            candidates = []
        out.append((q, _best_match(q, candidates)))
    return out
