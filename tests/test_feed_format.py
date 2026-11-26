import json
import xml.etree.ElementTree as ET

import pytest

from research_pipeline import feed_format as ff
from research_pipeline import library_api
from research_pipeline.feed_format import FeedError, PaperBlock, Post

NOW = "2026-11-26T03:00:00Z"


def post(pid, day, rank=1, title="A title", reasons=None):
    return Post(
        paper=PaperBlock(id=pid, title=title, abstract="An abstract.", authors=["A. Author"],
                         url=f"https://arxiv.org/abs/{pid}", published=day),
        digest_date=day, rank=rank, summary=None, reasons=reasons or ["topic: agents (0.80)"], provenance=None,
    )


def build(posts, **kw):
    args = dict(display_name="Alex", topics=["agents"], active=True, now=NOW, base_url="https://a.github.io/rpipe-feed/")
    return ff.build_files(posts, **{**args, **kw})


def test_parse_reason_matches_library_api_cases():
    assert ff.parse_reason("topic: agents (0.80)") == ("topic", "agents", None)
    assert ff.parse_reason("liked similar: 2611.1") == ("liked", "", "2611.1")
    assert ff.parse_reason("HF upvotes: 5") == ("upvotes", "5", None)
    assert ff.parse_reason("followed author: Ng") == ("author", "Ng", None)
    assert ff.parse_reason("keyword: rag") == ("keyword", "rag", None)
    assert ff.parse_reason("near your shelf: thesis.md") == ("shelf", "thesis.md", None)
    assert ff.parse_reason("disliked similar: 2611.2") is None
    assert ff.parse_reason("weird") is None
    got = library_api.plain_reasons(["topic: agents (0.80)", "liked similar: 2611.1", "weird"])
    assert [(r.kind, r.label, r.paper_id) for r in got] == [("topic", "agents", None), ("liked", "", "2611.1")]


def test_public_reasons_drop_liked_disliked_shelf():
    stored = ["topic: agents (0.80)", "liked similar: 2611.1", "disliked similar: 2611.2",
              "near your shelf: thesis.md", "HF upvotes: 5", "weird"]
    assert ff.public_reasons(stored) == ["topic: agents (0.80)", "HF upvotes: 5"]


def test_shared_topics_ignores_case_and_spaces():
    assert ff.shared_topics(["LLM  Agents", "rag"], [" llm agents", "security"]) == 1
    assert ff.shared_topics([], ["x"]) == 0


def test_build_files_round_trip():
    posts = [post("2610.1", "2026-10-02"), post("2611.1", "2026-11-20"), post("2611.2", "2026-11-25", rank=2)]
    files = build(posts)
    feed, dropped = ff.parse_feed(files["feed.json"])
    assert dropped == 0
    assert [a.path for a in feed.rpipe.archives] == ["archive/2026-10.json", "archive/2026-11.json"]
    assert [a.count for a in feed.rpipe.archives] == [1, 2]
    archived = []
    for ref in feed.rpipe.archives:
        items, bad = ff.parse_archive(files[ref.path])
        assert bad == 0
        archived += items
    assert sorted(i.id for i in archived) == ["2610.1", "2611.1", "2611.2"]
    # the items in the archives equal the items in feed.json for the recent ones
    assert {i.id: i for i in feed.items}["2611.1"] == {i.id: i for i in archived}["2611.1"]
    assert feed.feed_url == "https://a.github.io/rpipe-feed/feed.json"


def test_build_files_is_byte_stable():
    posts = [post("2611.1", "2026-11-20"), post("2611.2", "2026-11-25")]
    assert build(posts) == build(list(reversed(posts)))


def test_archive_month_splits_over_cap(monkeypatch):
    monkeypatch.setattr(ff, "ARCHIVE_MAX_ITEMS", 2)
    files = build([post(f"2611.{i}", "2026-11-05", rank=i) for i in range(5)])
    assert sorted(p for p in files if p.startswith("archive/")) == [
        "archive/2026-11-1.json", "archive/2026-11-2.json", "archive/2026-11-3.json"]


def test_feed_json_keeps_last_30_days():
    files = build([post("old", "2026-10-20"), post("edge", "2026-10-27"), post("new", "2026-11-25")])
    feed, _ = ff.parse_feed(files["feed.json"])
    assert [i.id for i in feed.items] == ["new", "edge"]
    assert sum(a.count for a in feed.rpipe.archives) == 3


def test_feed_only_publishes_public_reasons():
    files = build([post("2611.1", "2026-11-20", reasons=["topic: agents (0.80)", "near your shelf: thesis.md"])])
    assert b"thesis.md" not in files["feed.json"]


def test_atom_is_valid_xml_and_escapes():
    files = build([post("2611.1", "2026-11-20", title="<b>&</b>")])
    root = ET.fromstring(files["feed.xml"])
    ns = {"a": "http://www.w3.org/2005/Atom"}
    assert root.find("a:entry/a:title", ns).text == "<b>&</b>"
    assert root.find("a:entry/a:id", ns).text == "urn:rpipe:2611.1"
    assert b"feed.json" in files["README.md"]


def test_parse_feed_rejects_newer_format():
    doc = json.loads(build([])["feed.json"])
    doc["_rpipe"]["format"] = 2
    with pytest.raises(FeedError, match="newer rpipe"):
        ff.parse_feed(json.dumps(doc).encode())


def test_parse_feed_rejects_junk():
    for data in (b"not json", b"[]", b"{}", b"x" * (ff.FEED_MAX_BYTES + 1)):
        with pytest.raises(FeedError):
            ff.parse_feed(data)


def test_parse_feed_drops_bad_items():
    doc = json.loads(build([post("2611.1", "2026-11-20"), post("2611.2", "2026-11-21")])["feed.json"])
    doc["items"][0]["title"] = "x" * 600
    feed, dropped = ff.parse_feed(json.dumps(doc).encode())
    assert dropped == 1 and len(feed.items) == 1
