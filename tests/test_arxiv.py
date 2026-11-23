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


def _known(ids):
    return lambda pid: pid in ids


@respx.mock
def test_fetch_stops_after_page_containing_a_known_id_and_keeps_that_page():
    route = respx.get(API).mock(
        side_effect=[
            httpx.Response(200, text=feed(100, 0)),
            httpx.Response(200, text=feed(100, 100)),
        ]
    )
    src = ArxivSource(["cs.LG"], max_results=1000, sleep=lambda s: None,
                      is_known=_known({"2601.00150"}))
    papers = src.fetch()
    assert len(papers) == 200
    assert route.call_count == 2
    assert src.last_warning is None


@respx.mock
def test_fetch_warns_when_cap_hit_without_seeing_a_known_id():
    respx.get(API).mock(
        side_effect=[httpx.Response(200, text=feed(100, 0)), httpx.Response(200, text=feed(50, 100))]
    )
    src = ArxivSource(["cs.LG"], max_results=150, sleep=lambda s: None, is_known=_known(set()))
    assert len(src.fetch()) == 150
    assert src.last_warning == (
        "arxiv: reached max_results (150) before already-seen papers; some may be missed"
    )


@respx.mock
def test_fetch_no_warning_when_known_id_on_final_capped_page():
    respx.get(API).mock(return_value=httpx.Response(200, text=feed(100, 0)))
    src = ArxivSource(["cs.LG"], max_results=100, sleep=lambda s: None,
                      is_known=_known({"2601.00099"}))
    src.fetch()
    assert src.last_warning is None


@respx.mock
def test_fetch_no_warning_on_short_page_and_warning_resets_each_fetch():
    respx.get(API).mock(
        side_effect=[httpx.Response(200, text=feed(10)), httpx.Response(200, text=feed(10))]
    )
    src = ArxivSource(["cs.LG"], max_results=10, sleep=lambda s: None)
    src.fetch()
    assert src.last_warning is not None  # cap hit, nothing known
    src.max_results = 50
    src.fetch()
    assert src.last_warning is None  # short page: arXiv ran out


def test_set_known_checker_installs_the_callable():
    src = ArxivSource(["cs.LG"])
    src.set_known_checker(lambda pid: True)
    assert src._is_known("x") is True


ALL_QUERY = "cat:cs.*"


@respx.mock
def test_all_categories_sends_cs_wildcard_query():
    route = respx.get(API).mock(return_value=httpx.Response(200, text=feed(2)))
    src = ArxivSource(["all"], max_results=5, sleep=lambda s: None)
    assert len(src.fetch()) == 2
    params = route.calls[0].request.url.params
    assert params["search_query"] == ALL_QUERY
    assert params["sortBy"] == "submittedDate"
    assert params["sortOrder"] == "descending"


@respx.mock
def test_all_categories_pages_and_warns_at_cap():
    route = respx.get(API).mock(
        side_effect=[httpx.Response(200, text=feed(100, 0)), httpx.Response(200, text=feed(50, 100))]
    )
    src = ArxivSource(["all"], max_results=150, sleep=lambda s: None, is_known=_known(set()))
    assert len(src.fetch()) == 150
    assert [c.request.url.params["search_query"] for c in route.calls] == [ALL_QUERY] * 2
    assert [c.request.url.params["start"] for c in route.calls] == ["0", "100"]
    assert src.last_warning is not None and "max_results (150)" in src.last_warning


@respx.mock
def test_all_categories_stops_at_known_paper():
    route = respx.get(API).mock(return_value=httpx.Response(200, text=feed(100, 0)))
    src = ArxivSource(["all"], max_results=1000, sleep=lambda s: None,
                      is_known=_known({"2601.00050"}))
    assert len(src.fetch()) == 100
    assert len(route.calls) == 1 and src.last_warning is None


@respx.mock
def test_fetch_notes_each_page_to_the_reporter():
    from research_pipeline.progress import ListReporter, stage

    respx.get(API).mock(
        side_effect=[
            httpx.Response(200, text=feed(100, 0)),
            httpx.Response(200, text=feed(30, 100)),
        ]
    )
    rep = ListReporter()
    src = ArxivSource(["cs.CR"], max_results=200, sleep=lambda s: None, clock=Clock())
    with stage(rep, "collect") as handle:
        src.set_reporter(handle)
        src.fetch()
    notes = [e.label for e in rep.events if e.kind == "note"]
    assert notes == ["arXiv page 1 (0 papers so far)", "arXiv page 2 (100 papers so far)"]


from research_pipeline.sources.arxiv import BACKOFF_SECONDS, MAX_RETRY_AFTER, retry_delay  # noqa: E402


@pytest.mark.parametrize(
    ("status", "header", "attempt", "expected"),
    [
        (429, None, 0, 15.0), (429, None, 1, 45.0), (429, None, 2, 120.0), (429, None, 3, None),
        (503, None, 0, 15.0), (500, None, 0, None), (404, None, 0, None),
        (429, "7", 0, 7.0), (429, "99999", 0, MAX_RETRY_AFTER),
        (429, "Wed, 21 Oct 2026 07:28:00 GMT", 1, 45.0), (429, "-5", 0, 15.0), (429, "", 2, 120.0),
    ],
)
def test_retry_delay_table(status, header, attempt, expected):
    assert retry_delay(status, header, attempt) == expected


def _ticking(clock):
    sleeps: list[float] = []

    def sleep(s):
        sleeps.append(s)
        clock.now += s

    return sleeps, sleep


@respx.mock
def test_429_is_retried_with_backoff_then_succeeds():
    respx.get(API).mock(side_effect=[httpx.Response(429), httpx.Response(429), httpx.Response(200, text=feed(2))])
    clock = Clock()
    sleeps, sleep = _ticking(clock)
    src = ArxivSource(["cs.LG"], max_results=100, sleep=sleep, clock=clock)
    assert len(src.fetch()) == 2
    assert sleeps == [15.0, 45.0]


@respx.mock
def test_retry_after_header_is_honoured():
    respx.get(API).mock(side_effect=[httpx.Response(429, headers={"Retry-After": "9"}), httpx.Response(200, text=feed(1))])
    clock = Clock()
    sleeps, sleep = _ticking(clock)
    ArxivSource(["cs.LG"], sleep=sleep, clock=clock).fetch()
    assert sleeps == [9.0]


@respx.mock
def test_429_gives_up_after_three_retries_with_a_clear_message():
    route = respx.get(API).mock(return_value=httpx.Response(429))
    clock = Clock()
    sleeps, sleep = _ticking(clock)
    src = ArxivSource(["cs.LG"], sleep=sleep, clock=clock)
    with pytest.raises(SourceError, match=r"HTTP 429 \(rate limited; gave up after 3 retries\)"):
        src.fetch()
    assert len(route.calls) == 1 + len(BACKOFF_SECONDS)
    assert sleeps == list(BACKOFF_SECONDS)


@respx.mock
def test_other_errors_are_not_retried():
    route = respx.get(API).mock(return_value=httpx.Response(500))
    with pytest.raises(SourceError, match="HTTP 500"):
        ArxivSource(["cs.LG"], sleep=lambda s: None).fetch()
    assert len(route.calls) == 1


@respx.mock
def test_retry_wait_is_announced_through_the_reporter():
    respx.get(API).mock(side_effect=[httpx.Response(429), httpx.Response(200, text=feed(1))])
    clock = Clock()
    _, sleep = _ticking(clock)
    notes: list[str] = []

    class Handle:
        def note(self, label):
            notes.append(label)

    src = ArxivSource(["cs.LG"], sleep=sleep, clock=clock)
    src.set_reporter(Handle())
    src.fetch()
    assert any("HTTP 429" in n and "waiting 15s" in n and "retry 1 of 3" in n for n in notes)
