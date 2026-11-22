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
MIN_TITLE_OVERLAP = 0.6  # share of the proposed title's content words the arXiv title must contain
MIN_CONTENT_WORDS = 2  # a one-word "title" proves nothing; such queries are dropped
MAX_TOPIC_CHARS = 120
# Words that carry no identity: "On the Security of the Agents" must not match an unrelated
# title just because both say "on", "the" and "of".
STOPWORDS = frozenset(
    "a an and are as at be by for from in into is it its of on or that the this to with without "
    "via using toward towards over under about".split()
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class ProposedProfile(BaseModel):
    topics: list[str] = Field(default_factory=list)
    seed_queries: list[str] = Field(default_factory=list)


def _words(text: str) -> set[str]:
    """Lower-case content words: punctuation and hyphens split words, stopwords are dropped."""
    return set(re.findall(r"[a-z0-9]+", text.casefold())) - STOPWORDS


def clean_topic(text: str) -> str:
    """A proposed topic made safe to show: control characters (incl. ESC) removed, spaces
    collapsed, capped at 120 characters."""
    return " ".join(_CONTROL.sub(" ", text).split())[:MAX_TOPIC_CHARS].strip()


def check_description(description: str) -> str:
    """The description with spaces collapsed; ValueError if under 20 characters. Cheap, so
    callers run it before touching the model."""
    text = " ".join(description.split())
    if len(text) < MIN_DESCRIPTION:
        raise ValueError(f"Describe your research in at least {MIN_DESCRIPTION} characters.")
    return text


def _defuse(text: str) -> str:
    return re.sub(r"<(/?)(description)", r"&lt;\1\2", text, flags=re.IGNORECASE)


def propose(llm: LLM, description: str) -> ProposedProfile:
    """One model call. Raises ValueError for a description under 20 characters and LlmError
    if the model fails."""
    text = check_description(description)
    user = f"<description>\n{_defuse(text)}\n</description>"
    result = llm.generate(ONBOARDING_SYSTEM, user, ProposedProfile)
    return ProposedProfile(
        topics=[c for c in map(clean_topic, result.topics) if c][:8],
        seed_queries=[q for q in result.seed_queries if q.strip()][:10],
    )


def _best_match(query: str, candidates: list[PaperMeta]) -> PaperMeta | None:
    wanted = _words(query)
    if len(wanted) < MIN_CONTENT_WORDS:
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
