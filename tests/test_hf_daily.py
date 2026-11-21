import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from research_pipeline.sources.base import SourceError
from research_pipeline.sources.hf_daily import API_URL, HfDailySource, parse_hf_daily

FIXTURE = Path(__file__).parent / "fixtures" / "hf_daily.json"


@pytest.fixture
def data() -> list[dict]:
    return json.loads(FIXTURE.read_text())


def test_parse_maps_upvotes_and_repo(data):
    p = parse_hf_daily(data)[0]
    assert p.id == "2611.26232"
    assert p.source == "hf_daily"
    assert p.url == "https://arxiv.org/abs/2611.26232"
    assert p.title == "Time Series Structure Learning with Local Causal Parent Discovery"
    assert p.hf_upvotes == 2
    assert p.repos == ["https://github.com/jmaasch/itpd"]
    assert p.authors[0] == "Jacqueline Maasch" and len(p.authors) == 5
    assert p.published == date(2026, 11, 19)
    assert p.abstract.startswith("Learning and inference")


def test_parse_missing_repo_and_summary_ok(data):
    papers = parse_hf_daily(data)
    assert len(papers) == 3
    p = papers[1]
    assert p.repos == [] and p.abstract == "" and p.hf_upvotes == 1
    assert papers[2].repos == [] and papers[2].hf_upvotes == 17


def test_parse_strips_version_and_skips_unusable_ids():
    items = [
        {"paper": {"id": "2611.00001v3", "title": "  A\n title ", "githubRepo": ""}},
        {"paper": {"title": "no id"}},
        {"paper": {"id": "not-an-id", "title": "junk"}},
        {"title": "no paper"},
        "garbage",
    ]
    papers = parse_hf_daily(items)
    assert [p.id for p in papers] == ["2611.00001"]
    assert papers[0].title == "A title" and papers[0].repos == []
    assert papers[0].published is None


def test_parse_non_list_raises():
    with pytest.raises(SourceError):
        parse_hf_daily({"error": "x"})  # type: ignore[arg-type]


@respx.mock
def test_fetch_returns_papers(data):
    route = respx.get(API_URL).mock(return_value=httpx.Response(200, json=data))
    papers = HfDailySource().fetch()
    assert len(papers) == 3
    assert route.calls[0].request.headers["user-agent"] == "research-pipeline/0.1"


@respx.mock
def test_fetch_malformed_json_raises_source_error():
    respx.get(API_URL).mock(return_value=httpx.Response(200, text="<html>nope"))
    with pytest.raises(SourceError, match="parse"):
        HfDailySource().fetch()


@respx.mock
def test_fetch_http_error_raises_source_error():
    respx.get(API_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(SourceError, match="503"):
        HfDailySource().fetch()


@respx.mock
def test_fetch_timeout_raises_source_error():
    respx.get(API_URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    with pytest.raises(SourceError, match="failed"):
        HfDailySource().fetch()
