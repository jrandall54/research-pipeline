"""Hugging Face Daily Papers source: community-curated papers with upvotes."""

import re
from datetime import date

import httpx

from research_pipeline.models import PaperMeta
from research_pipeline.sources.base import SourceError

API_URL = "https://huggingface.co/api/daily_papers"
USER_AGENT = "research-pipeline/0.1"

_ID_RE = re.compile(r"^(?P<id>\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$")


def _clean(value: object) -> str:
    """Whitespace-collapsed string; anything that is not a string gives ""."""
    return " ".join(value.split()) if isinstance(value, str) else ""


def _parse_item(item: dict) -> PaperMeta | None:
    """One payload item as PaperMeta, or None if it has no usable arXiv id."""
    paper = item.get("paper")
    if not isinstance(paper, dict):
        return None
    m = _ID_RE.match(_clean(paper.get("id")))
    if m is None:
        return None
    paper_id = m.group("id")

    authors = [
        name
        for a in paper.get("authors") or []
        if isinstance(a, dict) and (name := _clean(a.get("name")))
    ]
    published = None
    raw_date = _clean(paper.get("publishedAt"))[:10]
    if raw_date:
        try:
            published = date.fromisoformat(raw_date)
        except ValueError:
            published = None

    repos: list[str] = []
    repo = _clean(paper.get("githubRepo"))
    if repo:
        repos.append(repo)

    upvotes = paper.get("upvotes")
    return PaperMeta(
        id=paper_id,
        title=_clean(paper.get("title")) or _clean(item.get("title")),
        abstract=_clean(paper.get("summary")),
        authors=authors,
        source="hf_daily",
        url=f"https://arxiv.org/abs/{paper_id}",
        hf_upvotes=upvotes if isinstance(upvotes, int) and upvotes > 0 else 0,
        repos=repos,
        published=published,
    )


def parse_hf_daily(data: list[dict]) -> list[PaperMeta]:
    """Parse the daily_papers payload. Items without an arXiv id are skipped."""
    if not isinstance(data, list):
        raise SourceError("could not parse Hugging Face response: expected a JSON list")
    papers = []
    for item in data:
        if not isinstance(item, dict):
            continue
        paper = _parse_item(item)
        if paper is not None:
            papers.append(paper)
    return papers


class HfDailySource:
    name = "hf_daily"

    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT})

    def fetch(self) -> list[PaperMeta]:
        """Today's featured papers, with HF upvotes and linked GitHub repos."""
        try:
            resp = self._client.get(API_URL, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SourceError(
                f"Hugging Face API returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise SourceError(f"Hugging Face API request failed: {exc!r}") from exc
        try:
            data = resp.json()
        except ValueError as exc:
            raise SourceError(f"could not parse Hugging Face response: {exc}") from exc
        return parse_hf_daily(data)
