import json
from pathlib import Path

import httpx
import pytest
import respx

from research_pipeline.db import open_db
from research_pipeline.models import PaperMeta
from research_pipeline.seed_input import find_seed_candidates, parse_seed_input
from research_pipeline.seeds import cache_seed, resolve_seed_texts
from research_pipeline.sources.arxiv import ArxivSource
from research_pipeline.sources.base import SourceError

API = "https://export.arxiv.org/api/query"
TITLE_XML = (Path(__file__).parent / "fixtures" / "arxiv_title_search.xml").read_text()
EMPTY = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'


def source() -> ArxivSource:
    return ArxivSource(categories=[], sleep=lambda s: None)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://arxiv.org/abs/2307.15043", ("id", "2307.15043")),
        ("arxiv.org/pdf/2307.15043v2.pdf", ("id", "2307.15043")),
        ("http://arxiv.org/html/2307.15043v1", ("id", "2307.15043")),
        ("2307.15043", ("id", "2307.15043")),
        ("hep-th/9901001", ("id", "hep-th/9901001")),
        (
            "Universal and Transferable Adversarial Attacks",
            ("title", "Universal and Transferable Adversarial Attacks"),
        ),
    ],
)
def test_parse_seed_input_variants(text, expected):
    assert parse_seed_input(text) == expected


@respx.mock
def test_search_title_builds_ti_query():
    route = respx.get(API).mock(return_value=httpx.Response(200, text=TITLE_XML))
    out = source().search_title("Universal and Transferable")
    params = route.calls.last.request.url.params
    assert params["search_query"] == 'ti:"Universal and Transferable"'
    assert params["max_results"] == "5"
    assert len(out) == 3


@respx.mock
def test_find_seed_candidates_by_id_returns_one():
    xml = TITLE_XML.split("<entry>")
    one = xml[0] + "<entry>" + xml[1].split("</entry>")[0] + "</entry></feed>"
    route = respx.get(API).mock(return_value=httpx.Response(200, text=one))
    out = find_seed_candidates("https://arxiv.org/abs/2307.15043v2", source())
    assert [p.id for p in out] == ["2307.15043"]
    assert route.calls.last.request.url.params["id_list"] == "2307.15043"


@respx.mock
def test_find_seed_candidates_unknown_id_returns_empty():
    respx.get(API).mock(return_value=httpx.Response(200, text=EMPTY))
    assert find_seed_candidates("2307.99999", source()) == []


@respx.mock
def test_find_seed_candidates_title_returns_up_to_five():
    route = respx.get(API).mock(return_value=httpx.Response(200, text=TITLE_XML))
    out = find_seed_candidates("Universal and Transferable Adversarial Attacks", source())
    assert 1 <= len(out) <= 5
    assert route.calls.last.request.url.params["search_query"].startswith("ti:")


def test_cache_seed_merges_and_resolve_uses_it(tmp_path):
    cache = tmp_path / "cache" / "seeds.json"
    cache.parent.mkdir()
    cache.write_text(json.dumps({"1111.11111": "old"}))
    meta = PaperMeta(
        id="2307.15043", title="T", abstract="A", source="arxiv",
        url="https://arxiv.org/abs/2307.15043",
    )
    cache_seed(cache, meta)
    assert json.loads(cache.read_text()) == {"1111.11111": "old", "2307.15043": "T\nA"}

    def boom(ids):
        raise RuntimeError("offline")

    conn = open_db(tmp_path / "t.db")
    try:
        assert resolve_seed_texts(conn, cache, ["2307.15043"], boom) == {"2307.15043": "T\nA"}
    finally:
        conn.close()


@respx.mock
def test_search_title_http_error_raises_source_error():
    respx.get(API).mock(return_value=httpx.Response(503))
    with pytest.raises(SourceError):
        source().search_title("anything")
