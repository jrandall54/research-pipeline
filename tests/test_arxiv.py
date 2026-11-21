from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from research_pipeline.sources.arxiv import ArxivSource, parse_arxiv_atom
from research_pipeline.sources.base import SourceError

FIXTURE = Path(__file__).parent / "fixtures" / "arxiv_atom.xml"
API = "https://export.arxiv.org/api/query"


@pytest.fixture
def xml() -> str:
    return FIXTURE.read_text()


def feed(n: int, start: int = 0) -> str:
    entries = "".join(
        f"""<entry><id>http://arxiv.org/abs/2601.{start + i:05d}v1</id>
        <title>T{start + i}</title><summary>S</summary>
        <published>2026-01-01T00:00:00Z</published>
        <author><name>A</name></author></entry>"""
        for i in range(n)
    )
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'


class Clock:
    """Fake clock: only advances when told to."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_parse_extracts_id_and_version(xml):
    papers = parse_arxiv_atom(xml)
    p = next(p for p in papers if p.id == "2501.01234")
    assert p.version == 2
    assert p.url == "https://arxiv.org/abs/2501.01234"
    assert p.source == "arxiv"
    assert p.authors == ["Alper Cakan"]
    assert p.published == date(2025, 1, 2)
    assert len(papers) == 3


def test_parse_collapses_whitespace(xml):
    p = parse_arxiv_atom(xml)[0]
    assert p.title == "SoK: Assessing the Evolution of LLM Capability in Software Vulnerability Research"
    assert "\n" not in p.abstract and "  " not in p.abstract
    assert p.authors == ["Amine Lbath", "Manan Suri", "Dinesh Manocha"]


def test_parse_empty_summary_gives_empty_abstract(xml):
    p = parse_arxiv_atom(xml)[2]
    assert p.abstract == ""


def test_parse_bad_xml_raises():
    with pytest.raises(SourceError):
        parse_arxiv_atom("<feed><entry>")


@respx.mock
def test_fetch_builds_query_and_sets_user_agent():
    route = respx.get(API).mock(return_value=httpx.Response(200, text=feed(2)))
    src = ArxivSource(["cs.LG", "cs.CR"], max_results=5, sleep=lambda s: None)
    papers = src.fetch()
    assert len(papers) == 2
    req = route.calls[0].request
    assert req.url.params["search_query"] == "cat:cs.LG OR cat:cs.CR"
    assert req.url.params["sortBy"] == "submittedDate"
    assert req.url.params["sortOrder"] == "descending"
    assert req.url.params["start"] == "0"
    assert req.headers["user-agent"].startswith("research-pipeline/")


@respx.mock
def test_fetch_paginates_and_sleeps_3s_between_calls():
    route = respx.get(API).mock(
        side_effect=[
            httpx.Response(200, text=feed(100, 0)),
            httpx.Response(200, text=feed(100, 100)),
            httpx.Response(200, text=feed(30, 200)),
        ]
    )
    sleeps: list[float] = []
    src = ArxivSource(["cs.LG"], max_results=230, sleep=sleeps.append, clock=Clock())
    papers = src.fetch()
    assert len(papers) == 230
    assert [c.request.url.params["start"] for c in route.calls] == ["0", "100", "200"]
    assert [c.request.url.params["max_results"] for c in route.calls] == ["100", "100", "30"]
    assert sleeps == [3.0, 3.0]


@respx.mock
def test_fetch_stops_on_short_page():
    route = respx.get(API).mock(return_value=httpx.Response(200, text=feed(40)))
    src = ArxivSource(["cs.LG"], max_results=300, sleep=lambda s: None)
    assert len(src.fetch()) == 40
    assert route.call_count == 1


@respx.mock
def test_spacing_only_sleeps_remaining_gap():
    respx.get(API).mock(return_value=httpx.Response(200, text=feed(1)))
    clock = Clock()
    sleeps: list[float] = []
    src = ArxivSource(["cs.LG"], max_results=1, sleep=sleeps.append, clock=clock)
    src.fetch()
    clock.now += 1.0
    src.fetch()
    clock.now += 10.0
    src.fetch()
    assert sleeps == [2.0]


@respx.mock
def test_fetch_http_500_raises():
    respx.get(API).mock(return_value=httpx.Response(500))
    src = ArxivSource(["cs.LG"], sleep=lambda s: None)
    with pytest.raises(SourceError, match="500"):
        src.fetch()


@respx.mock
def test_fetch_timeout_raises():
    respx.get(API).mock(side_effect=httpx.ConnectTimeout("slow"))
    src = ArxivSource(["cs.LG"], sleep=lambda s: None)
    with pytest.raises(SourceError, match="arXiv"):
        src.fetch()


@respx.mock
def test_fetch_by_ids_batches_and_returns_metas():
    ids = [f"2601.{i:05d}" for i in range(150)]
    route = respx.get(API).mock(
        side_effect=[
            httpx.Response(200, text=feed(100, 0)),
            httpx.Response(200, text=feed(50, 100)),
        ]
    )
    sleeps: list[float] = []
    src = ArxivSource(["cs.LG"], sleep=sleeps.append, clock=Clock())
    papers = src.fetch_by_ids(ids)
    assert len(papers) == 150
    assert route.call_count == 2
    first = route.calls[0].request.url.params
    assert first["id_list"] == ",".join(ids[:100])
    assert first["max_results"] == "100"
    assert route.calls[1].request.url.params["id_list"] == ",".join(ids[100:])
    assert sleeps == [3.0]


@respx.mock
def test_spacing_holds_across_fetch_and_fetch_by_ids():
    respx.get(API).mock(return_value=httpx.Response(200, text=feed(1)))
    sleeps: list[float] = []
    src = ArxivSource(["cs.LG"], max_results=1, sleep=sleeps.append, clock=Clock())
    src.fetch()
    src.fetch_by_ids(["2601.00000"])
    assert sleeps == [3.0]


def test_fetch_by_ids_empty_makes_no_call():
    src = ArxivSource(["cs.LG"], sleep=lambda s: None)
    assert src.fetch_by_ids([]) == []
