import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from research_pipeline import feed_format as ff
from research_pipeline import share_directory as sd
from research_pipeline.db import open_db
from research_pipeline.feed_format import PaperBlock, Post
from research_pipeline.progress import ListReporter

NOW = datetime(2026, 11, 26, 3, 0, tzinfo=UTC)


def hit(login, name="rpipe-feed"):
    return {"name": name, "owner": {"login": login}}


def search(*hits):
    return httpx.Response(200, json={"items": list(hits)})


def feed_bytes(days, name="Alex M."):
    posts = [
        Post(paper=PaperBlock(id=f"p{i}", title=f"Title {i}", abstract=f"Abstract {i}", authors=["A"],
                              url=f"https://arxiv.org/abs/{i}", published=d),
             digest_date=d, rank=1, summary=None, reasons=[], provenance=None)
        for i, d in enumerate(days)
    ]
    files = ff.build_files(posts, display_name=name, topics=["agents"], active=True,
                           now="2026-11-26T03:00:00Z", base_url="https://x.github.io/rpipe-feed/")
    return files["feed.json"]


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def http():
    with httpx.Client() as c:
        yield c


def rows(conn):
    return conn.execute("SELECT * FROM directory_entries ORDER BY feed_id").fetchall()


@respx.mock
def test_refresh_keeps_only_valid_feeds(conn, http):
    respx.get(sd.SEARCH_URL).mock(return_value=search(hit("Alex"), hit("gone"), hit("junk"), hit("Other", "notes")))
    respx.get(sd.feed_url("Alex")).respond(200, content=feed_bytes(["2026-11-25"]))
    respx.get(sd.feed_url("gone")).respond(404)
    respx.get(sd.feed_url("junk")).respond(200, content=b"{not json")
    reporter = ListReporter()
    count, errors = sd.refresh(conn, http, reporter, now=NOW)
    assert (count, errors) == (1, [])
    (row,) = rows(conn)
    assert row["feed_id"] == "github:alex/rpipe-feed"
    assert row["handle"] == "Alex" and row["display_name"] == "Alex M."
    assert json.loads(row["topics"]) == ["agents"]
    labels = [e.label for e in reporter.events if e.kind == "step"]
    assert "checking 2 of 3: gone" in labels


@respx.mock
def test_failed_search_keeps_old_list(conn, http):
    respx.get(sd.SEARCH_URL).mock(return_value=search(hit("alex")))
    respx.get(sd.feed_url("alex")).respond(200, content=feed_bytes(["2026-11-25"]))
    sd.refresh(conn, http, None, now=NOW)
    respx.get(sd.SEARCH_URL).respond(403)
    count, errors = sd.refresh(conn, http, None, now=NOW)
    assert count == 1 and len(errors) == 1 and "403" in errors[0]
    assert len(rows(conn)) == 1


@respx.mock
def test_search_with_wrong_shape_keeps_old_list(conn, http):
    respx.get(sd.SEARCH_URL).respond(200, json={"message": "nope"})
    count, errors = sd.refresh(conn, http, None, now=NOW)
    assert count == 0 and errors and rows(conn) == []


@respx.mock
def test_preview_and_rate(conn, http):
    days = ["2026-11-25", "2026-11-24", "2026-11-20", "2026-11-10", "2026-11-01", "2026-09-01"]
    respx.get(sd.SEARCH_URL).mock(return_value=search(hit("alex")))
    respx.get(sd.feed_url("alex")).respond(200, content=feed_bytes(days))
    sd.refresh(conn, http, None, now=NOW)
    (row,) = rows(conn)
    assert row["posts_per_day"] == 0.2  # 5 papers in the last 30 days
    preview = json.loads(row["preview"])
    assert [p["title"] for p in preview] == ["Title 0", "Title 1", "Title 2"]
    assert preview[0]["one_line"] == "Abstract 0"
