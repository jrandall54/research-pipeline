"""Turn what a user typed (link, id or title) into candidate seed papers.

See docs/guide/profile.md ("Adding a seed") and docs/guide/sources.md.
"""

import re
from typing import Literal

from research_pipeline.models import PaperMeta
from research_pipeline.profile_edit import normalise_arxiv_id
from research_pipeline.sources.arxiv import ArxivSource

_HOST = r"(?:[a-z0-9-]+\.)*arxiv\.org"
_LINK_RE = re.compile(rf"^(?:https?://)?{_HOST}/(?:abs|pdf|html)/(?P<id>\S+?)(?:\.pdf)?$", re.I)
_LOOKS_LIKE_URL_RE = re.compile(rf"^(?:https?://|{_HOST}/)", re.I)


def parse_seed_input(text: str) -> tuple[Literal["id", "title"], str]:
    """Classify input as an arXiv id (normalised, no version) or a title to search.

    Links may carry a query, a #fragment, a trailing slash or any arxiv.org host. Something
    that looks like a link but holds no arXiv id raises ValueError (it is never title-searched).
    """
    t = text.strip()
    if _LOOKS_LIKE_URL_RE.match(t):
        link = re.split(r"[?#]", t, maxsplit=1)[0].rstrip("/")
        m = _LINK_RE.match(link)
        try:
            if not m:
                raise ValueError
            return "id", normalise_arxiv_id(m.group("id"))
        except ValueError:
            raise ValueError(f"couldn't find an arXiv id in that link: {t!r}. Try the id itself, e.g. 2307.15043.") from None
    try:
        return "id", normalise_arxiv_id(t)
    except ValueError:
        return "title", " ".join(t.split())


def find_seed_candidates(text: str, arxiv: ArxivSource) -> list[PaperMeta]:
    """Papers matching the input: 0 or 1 for an id, up to 5 for a title."""
    kind, value = parse_seed_input(text)
    if kind == "id":
        return arxiv.fetch_by_ids([value])
    return arxiv.search_title(value)
