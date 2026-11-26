import json

import httpx
import pytest
import respx

from research_pipeline import feed_format as ff
from research_pipeline import share_fetch as sf
from research_pipeline.db import get_follow, list_follows, open_db, upsert_follow
from research_pipeline.feed_format import PaperBlock, Post
from research_pipeline.progress import ListReporter

NOW = "2026-11-26T03:00:00Z"
BASE = "https://alex.github.io/rpipe-feed/"
FEED = BASE + "feed.json"
FID = "github:alex/rpipe-feed"


def make_post(pid, day):
    return Post(
        paper=PaperBlock(id=pid, title=f"Title {pid}", abstract="Abs", authors=["A"],
                         url=f"https://arxiv.org/abs/{pid}", published=day),
        digest_date=day, rank=1, summary=None, reasons=["topic: agents (0.80)"], provenance=None,
    )


def build(days=("2026-11-25", "2026-10-10", "2026-09-10"), name="Alex M.", active=True):
    posts = [make_post(f"p{d}", d) for d in days]
    return ff.build_files(posts, display_name=name, topics=["agents"], active=active,
                          now=NOW, base_url=BASE)


def serve(mock, files, base=BASE):
    for path, data in files.items():
        if path in ("feed.json",) or path.startswith("archive/"):
            mock.get(base + path).respond(200, content=data)


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def http():
    with httpx.Client() as c:
        yield c


def test_resolve_forms():
    assert sf.resolve("@Alex") == (FID, FEED, "alex")
    assert sf.resolve("alex")[0] == FID
    assert sf.resolve("https://x.org/f/feed.json") == ("url:https://x.org/f/feed.json", "https://x.org/f/feed.json", None)
    for bad in ("http://x.org/feed.json", "ftp://x", "not a handle!", "", "-a"):
        with pytest.raises(ValueError):
            sf.resolve(bad)


@respx.mock
def test_follow_takes_whole_past(conn, http):
    files = build()
    assert len([p for p in files if p.startswith("archive/")]) == 3
    serve(respx.mock, files)
    r = sf.follow(conn, "@alex", http, own_handle="me", reporter=None)
    assert (r.new_posts, r.error) == (3, None)
    rows = conn.execute("SELECT paper_id, digest_date FROM followed_posts ORDER BY paper_id").fetchall()
    assert [x[1] for x in rows] == ["2026-09-10", "2026-10-10", "2026-11-25"]
    f = get_follow(conn, FID)
    assert f["display_name"] == "Alex M." and json.loads(f["topics"]) == ["agents"]
    assert f["failures"] == 0 and len(json.loads(f["archive_hashes"])) >= 2


@respx.mock
def test_second_fetch_skips_unchanged_archives(conn, http):
    serve(respx.mock, build())
    sf.follow(conn, "alex", http, own_handle=None, reporter=None)
    before = len(respx.calls)
    r = sf.fetch_one(conn, FID, http)
    assert r.error is None and r.new_posts == 0
    assert [str(c.request.url) for c in list(respx.calls)[before:]] == [FEED]


@respx.mock
def test_changed_archive_is_downloaded_again(conn, http):
    serve(respx.mock, build())
    sf.follow(conn, "alex", http, own_handle=None, reporter=None)
    files = build(days=("2026-11-25", "2026-10-10", "2026-10-11", "2026-09-10"))
    serve(respx.mock, files)
    before = len(respx.calls)
    r = sf.fetch_one(conn, FID, http)
    assert r.new_posts == 1
    urls = [str(c.request.url) for c in list(respx.calls)[before:]]
    assert FEED in urls and BASE + "archive/2026-10.json" in urls and BASE + "archive/2026-09.json" not in urls


@respx.mock
def test_fetch_rejects_oversize(conn, http, monkeypatch):
    serve(respx.mock, build())
    monkeypatch.setattr(ff, "FEED_MAX_BYTES", 100)
    with pytest.raises(ValueError, match="too big"):
        sf.follow(conn, "alex", http, own_handle=None, reporter=None)
    assert list_follows(conn) == []


@respx.mock
def test_get_limited_aborts_over_cap(http):
    respx.get(FEED).respond(200, content=b"x" * 1000)
    with pytest.raises(sf.FetchFailed, match="too big"):
        sf.get_limited(http, FEED, 100)


@respx.mock
def test_fetch_skips_bad_items(conn, http):
    files = build(days=("2026-11-25", "2026-11-24"))
    doc = json.loads(files["feed.json"])
    doc["items"][1]["title"] = "x" * 600
    serve(respx.mock, files)
    respx.get(FEED).respond(200, content=json.dumps(doc).encode())
    upsert_follow(conn, FID, FEED, "alex", "Alex", [])
    r = sf.fetch_one(conn, FID, http)
    assert (r.skipped_items, r.error) == (1, None)


@respx.mock
def test_failed_fetch_stores_nothing_and_counts(conn, http):
    files = build()
    respx.get(FEED).respond(200, content=files["feed.json"])
    for k in ("2026-11", "2026-10"):
        respx.get(BASE + f"archive/{k}.json").respond(200, content=files[f"archive/{k}.json"])
    respx.get(BASE + "archive/2026-09.json").respond(500)
    upsert_follow(conn, FID, FEED, "alex", "Alex", [])
    r = sf.fetch_one(conn, FID, http)
    assert r.error and "500" in r.error
    assert conn.execute("SELECT COUNT(*) FROM followed_posts").fetchone()[0] == 0
    f = get_follow(conn, FID)
    assert f["failures"] == 1 and f["archive_hashes"] == "{}"


@respx.mock
def test_one_bad_feed_does_not_stop_others(conn, http):
    upsert_follow(conn, FID, FEED, "alex", "Alex M.", [])
    bad = "https://bob.github.io/rpipe-feed/"
    upsert_follow(conn, "github:bob/rpipe-feed", bad + "feed.json", "bob", "Bob", [])
    respx.get(bad + "feed.json").respond(404)
    serve(respx.mock, build())
    counts, errors = sf.fetch_all(conn, http, None)
    assert counts == {"followed_feeds": 2, "followed_new": 3}
    assert len(errors) == 1 and errors[0].startswith("fetch Bob: ")


@respx.mock
def test_follow_unknown_handle_stores_nothing(conn, http):
    respx.get(FEED).respond(404)
    with pytest.raises(ValueError, match="No rpipe feed at https://alex.github.io"):
        sf.follow(conn, "alex", http, own_handle=None, reporter=None)
    assert conn.execute("SELECT COUNT(*) FROM follows").fetchone()[0] == 0


def test_follow_own_handle_refused(conn, http):
    with pytest.raises(ValueError, match="own feed"):
        sf.follow(conn, "@Alex", http, own_handle="alex", reporter=None)


@respx.mock
def test_http_redirect_to_http_refused(conn, http):
    respx.get(FEED).respond(302, headers={"location": "http://evil.test/feed.json"})
    with pytest.raises(ValueError, match="https"):
        sf.follow(conn, "alex", http, own_handle=None, reporter=None)
    assert conn.execute("SELECT COUNT(*) FROM follows").fetchone()[0] == 0


@respx.mock
def test_https_redirect_is_followed(http):
    respx.get(FEED).respond(302, headers={"location": "https://other.test/feed.json"})
    respx.get("https://other.test/feed.json").respond(200, content=b"ok")
    assert sf.get_limited(http, FEED, 100) == b"ok"


def test_stopped_after_seven_failures(conn):
    upsert_follow(conn, FID, FEED, "alex", "Alex", [])
    assert not sf.is_stopped(get_follow(conn, FID))
    conn.execute("UPDATE follows SET failures = 6")
    assert not sf.is_stopped(get_follow(conn, FID))
    conn.execute("UPDATE follows SET failures = 7")
    assert sf.is_stopped(get_follow(conn, FID))
    conn.execute("UPDATE follows SET failures = 0, active = 0")
    assert sf.is_stopped(get_follow(conn, FID))


@respx.mock
def test_inactive_feed_is_recorded(conn, http):
    serve(respx.mock, build(days=("2026-11-25",), active=False))
    sf.follow(conn, "alex", http, own_handle=None, reporter=None)
    assert sf.is_stopped(get_follow(conn, FID))


@respx.mock
def test_fetch_progress_steps(conn, http):
    upsert_follow(conn, FID, FEED, "alex", "Alex M.", [])
    bad = "https://bob.github.io/rpipe-feed/feed.json"
    upsert_follow(conn, "github:bob/rpipe-feed", bad, "bob", "Bob", [])
    respx.get(bad).respond(404)
    serve(respx.mock, build())
    rep = ListReporter()
    sf.fetch_all(conn, http, rep)
    labels = [e.label for e in rep.events if e.kind == "step"]
    assert "fetching 1 of 2: Alex M." in labels and "fetching 2 of 2: Bob" in labels
    assert rep.events[0].kind == "start" and rep.events[-1].kind == "done"


@respx.mock
def test_malformed_redirect_fails_alone(conn, http):
    upsert_follow(conn, FID, FEED, "alex", "Alex M.", [])
    bad = "https://bob.github.io/rpipe-feed/"
    upsert_follow(conn, "github:bob/rpipe-feed", bad + "feed.json", "bob", "Bob", [])
    respx.get(FEED).respond(302, headers={"location": "https://[bad"})
    serve(respx.mock, build(name="Bob"), base=bad)
    counts, errors = sf.fetch_all(conn, http, None)
    assert counts == {"followed_feeds": 2, "followed_new": 3}
    assert len(errors) == 1 and errors[0].startswith("fetch Alex M.: ")
    assert get_follow(conn, FID)["failures"] == 1
    assert get_follow(conn, "github:bob/rpipe-feed")["failures"] == 0


def test_unexpected_error_is_recorded_not_raised(conn, monkeypatch):
    upsert_follow(conn, FID, FEED, "alex", "Alex", [])
    monkeypatch.setattr(sf, "_download", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    r = sf.fetch_one(conn, FID, None)
    assert r.error and "RuntimeError" in r.error and get_follow(conn, FID)["failures"] == 1


@respx.mock
def test_declared_oversize_is_refused_before_reading(http):
    respx.get(FEED).respond(200, content=b"x", headers={"content-length": "999999999"})
    with pytest.raises(sf.FetchFailed, match="too big"):
        sf.get_limited(http, FEED, 100)


@respx.mock
def test_oversize_archive_fails_the_fetch(conn, http, monkeypatch):
    serve(respx.mock, build())
    monkeypatch.setattr(ff, "ARCHIVE_MAX_BYTES", 50)
    upsert_follow(conn, FID, FEED, "alex", "Alex", [])
    r = sf.fetch_one(conn, FID, http)
    assert r.error and "too big" in r.error
    assert conn.execute("SELECT COUNT(*) FROM followed_posts").fetchone()[0] == 0
