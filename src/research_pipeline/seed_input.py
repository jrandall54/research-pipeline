"""Turn what a user typed (link, id or title) into candidate seed papers.

See docs/guide/profile.md ("Adding a seed") and docs/guide/sources.md.
"""

import re
from typing import Literal

from research_pipeline.models import PaperMeta
from research_pipeline.profile_edit import normalise_arxiv_id
from research_pipeline.sources.arxiv import ArxivSource

_URL_RE = re.compile(r"^(?:https?://)?(?:www\.)?arxiv\.org/(?:abs|pdf|html)/(?P<id>\S+?)(?:\.pdf)?$", re.I)


def parse_seed_input(text: str) -> tuple[Literal["id", "title"], str]:
    """Classify input as an arXiv id (normalised, no version) or a title to search."""
    t = text.strip()
    m = _URL_RE.match(t)
    candidate = m.group("id") if m else t
    try:
        return "id", normalise_arxiv_id(candidate)
    except ValueError:
        return "title", " ".join(t.split())


def find_seed_candidates(text: str, arxiv: ArxivSource) -> list[PaperMeta]:
    """Papers matching the input: 0 or 1 for an id, up to 5 for a title."""
    kind, value = parse_seed_input(text)
    if kind == "id":
        return arxiv.fetch_by_ids([value])
    return arxiv.search_title(value)
