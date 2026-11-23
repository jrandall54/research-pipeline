"""arXiv source: queries the public Atom API and parses entries into PaperMeta."""

import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import date

import httpx

from research_pipeline.models import PaperMeta
from research_pipeline.progress import StageHandle
from research_pipeline.sources.base import SourceError

API_URL = "https://export.arxiv.org/api/query"
USER_AGENT = "research-pipeline/0.1"
PAGE_SIZE = 100
# For category "all" (every computer-science category, cs.*) we use arXiv's prefix
# wildcard, which matches any cs.* subcategory; with sortBy=submittedDate descending
# this returns the newest papers first. (Checked live: every entry carried a cs category.)
ALL_QUERY = "cat:cs.*"
MIN_SPACING = 3.0  # seconds between API calls, per arXiv's terms of use
# arXiv answers 429 (too many requests) or 503 when a client is going too fast. We wait and try
# again a few times, then give up with a clear message. See docs/guide/sources.md.
RETRY_STATUSES = (429, 503)
BACKOFF_SECONDS = (15.0, 45.0, 120.0)  # waits before retry 1, 2, 3 when the server gives no Retry-After
MAX_RETRY_AFTER = 300.0  # never wait longer than this because a server asked (seconds)

_ATOM = "{http://www.w3.org/2005/Atom}"
_ID_RE = re.compile(r"^(?P<id>.+?)(?:v(?P<version>\d+))?$")


def retry_delay(status: int, retry_after: str | None, attempt: int) -> float | None:
    """Seconds to wait before retry number ``attempt + 1``, or None to give up.

    ``attempt`` counts retries already made (0 for the first failure). Only 429 and 503 are
    retried. A numeric ``Retry-After`` header is honoured (capped at MAX_RETRY_AFTER); a missing
    or non-numeric one (an HTTP date) falls back to BACKOFF_SECONDS.
    """
    if status not in RETRY_STATUSES or attempt >= len(BACKOFF_SECONDS):
        return None
    try:
        wait = float(retry_after) if retry_after else -1.0
    except ValueError:
        wait = -1.0
    if wait < 0:
        wait = BACKOFF_SECONDS[attempt]
    return min(wait, MAX_RETRY_AFTER)


def _text(el: ET.Element | None) -> str:
    """Element text with all whitespace runs collapsed to single spaces."""
    if el is None or el.text is None:
        return ""
    return " ".join(el.text.split())


def _parse_entry(entry: ET.Element) -> PaperMeta:
    raw_id = _text(entry.find(f"{_ATOM}id"))
    tail = raw_id.split("/abs/", 1)[-1]
    m = _ID_RE.match(tail)
    paper_id = m.group("id")
    version = int(m.group("version") or 1)
    published = _text(entry.find(f"{_ATOM}published"))[:10]
    return PaperMeta(
        id=paper_id,
        title=_text(entry.find(f"{_ATOM}title")),
        abstract=_text(entry.find(f"{_ATOM}summary")),
        authors=[_text(a.find(f"{_ATOM}name")) for a in entry.findall(f"{_ATOM}author")],
        source="arxiv",
        url=f"https://arxiv.org/abs/{paper_id}",
        version=version,
        published=date.fromisoformat(published) if published else None,
    )


def parse_arxiv_atom(xml: str) -> list[PaperMeta]:
    """Parse an arXiv API Atom response. Raises SourceError if it is not valid."""
    try:
        root = ET.fromstring(xml)
        return [_parse_entry(e) for e in root.findall(f"{_ATOM}entry")]
    except (ET.ParseError, ValueError, AttributeError) as exc:
        raise SourceError(f"could not parse arXiv response: {exc}") from exc


class ArxivSource:
    name = "arxiv"

    def __init__(
        self,
        categories: list[str],
        max_results: int = 1000,
        is_known: Callable[[str], bool] | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.categories = categories
        self.max_results = max_results
        self._is_known = is_known
        self.last_warning: str | None = None
        self._client = client or httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT})
        self._sleep = sleep
        self._clock = clock
        self._last_call: float | None = None
        self._reporter: StageHandle | None = None

    def close(self) -> None:
        """Release the HTTP connection pool."""
        self._client.close()

    def _get(self, params: dict[str, str | int]) -> list[PaperMeta]:
        """One API call, waiting out the 3 s gap since the last one; retries a 429/503 a few times."""
        attempt = 0
        while True:
            if self._last_call is not None:
                gap = MIN_SPACING - (self._clock() - self._last_call)
                if gap > 0:
                    self._sleep(gap)
            try:
                resp = self._client.get(API_URL, params=params, headers={"User-Agent": USER_AGENT})
                resp.raise_for_status()
                return parse_arxiv_atom(resp.text)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                delay = retry_delay(status, exc.response.headers.get("Retry-After"), attempt)
                if delay is None:
                    hint = f" (rate limited; gave up after {attempt} retries)" if status in RETRY_STATUSES and attempt else ""
                    raise SourceError(f"arXiv API returned HTTP {status}{hint}") from exc
                if self._reporter is not None:
                    self._reporter.note(f"arXiv said HTTP {status} (too many requests): waiting {delay:.0f}s, retry {attempt + 1} of {len(BACKOFF_SECONDS)}")
            except httpx.HTTPError as exc:
                raise SourceError(f"arXiv API request failed: {exc!r}") from exc
            finally:
                self._last_call = self._clock()
            self._sleep(delay)
            attempt += 1

    def set_known_checker(self, is_known: Callable[[str], bool]) -> None:
        """Install the "is this id already stored?" test (the caller owns the DB)."""
        self._is_known = is_known

    def set_reporter(self, handle: StageHandle) -> None:
        """Install the progress handle; `fetch()` then notes each page (optional, like the checker)."""
        self._reporter = handle

    def fetch(self) -> list[PaperMeta]:
        """Newest papers in the configured categories.

        Stops at the first of: a short page (arXiv has no more), a page that
        contains an already-stored id (everything older was collected before; that
        page is kept and upsert dedupes it), or ``max_results``. Hitting the cap
        without seeing a known id sets ``last_warning``.
        """
        self.last_warning = None
        if self.categories == ["all"]:
            query = ALL_QUERY
        else:
            query = " OR ".join(f"cat:{c}" for c in self.categories)
        papers: list[PaperMeta] = []
        while len(papers) < self.max_results:
            size = min(PAGE_SIZE, self.max_results - len(papers))
            if self._reporter is not None:
                page_no = len(papers) // PAGE_SIZE + 1
                self._reporter.note(f"arXiv page {page_no} ({len(papers)} papers so far)")
            page = self._get(
                {
                    "search_query": query,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                    "start": len(papers),
                    "max_results": size,
                }
            )
            papers.extend(page)
            if len(page) < size:
                return papers
            if self._is_known is not None and any(self._is_known(p.id) for p in page):
                return papers
        self.last_warning = (
            f"arxiv: reached max_results ({self.max_results}) before already-seen papers;"
            " some may be missed"
        )
        return papers

    def fetch_by_ids(self, ids: list[str]) -> list[PaperMeta]:
        """Look up specific papers by arXiv id (used to resolve profile seeds)."""
        papers: list[PaperMeta] = []
        for i in range(0, len(ids), PAGE_SIZE):
            batch = ids[i : i + PAGE_SIZE]
            papers.extend(
                self._get({"id_list": ",".join(batch), "max_results": len(batch)})
            )
        return papers

    def search_title(self, query: str, max_results: int = 5) -> list[PaperMeta]:
        """Papers whose title matches `query` (used to find a seed by name)."""
        quoted = query.replace('"', " ")
        return self._get(
            {"search_query": f'ti:"{quoted}"', "start": 0, "max_results": max_results}
        )
