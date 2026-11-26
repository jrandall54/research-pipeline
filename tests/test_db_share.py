import json

from research_pipeline.db import (
    get_follow,
    get_paper,
    list_follows,
    mark_unfollowed,
    open_db,
    record_fetch,
    replace_directory,
    replace_followed_posts,
    set_in_mix,
    upsert_follow,
    upsert_paper,
)
from research_pipeline.models import PaperMeta

FID = "feed-abc"


def post(pid="p1", date="2026-11-01", **kw):
    base = dict(
        paper_id=pid, digest_date=date, rank=1, title="T", abstract="A", authors=["Ann"],
        url="http://x", published=None, summary=None, reasons=["r"], provenance=None,
    )
    base.update(kw)
    return base


def follow(conn, fid=FID):
    upsert_follow(conn, fid, f"http://h/{fid}", "@ann", "Ann", ["ml"])


def test_v4_database_migrates_to_v5(tmp_path):
    path = tmp_path / "v.sqlite"
    conn = open_db(path)
    for t in ("follows", "followed_posts", "directory_entries"):
        conn.execute(f"DROP TABLE {t}")
    conn.execute(
        "INSERT INTO papers (id, title, source, url, created_at, updated_at)"
        " VALUES ('x', 'T', 'arxiv', 'u', 'n', 'n')"
    )
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    conn.close()
    conn = open_db(path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"follows", "followed_posts", "directory_entries"} <= names
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    assert get_paper(conn, "x") is not None


def test_upsert_follow_reactivates_unfollowed(tmp_path):
    conn = open_db(tmp_path / "v.sqlite")
    follow(conn)
    mark_unfollowed(conn, FID, set())
    assert get_follow(conn, FID)["unfollowed_at"] is not None
    assert list_follows(conn) == []
    assert len(list_follows(conn, include_unfollowed=True)) == 1
    follow(conn)
    assert get_follow(conn, FID)["unfollowed_at"] is None
    assert len(list_follows(conn)) == 1


def test_record_fetch_counts_failures(tmp_path):
    conn = open_db(tmp_path / "v.sqlite")
    follow(conn)
    record_fetch(conn, FID, ok=False, error="boom")
    record_fetch(conn, FID, ok=False, error="boom2")
    row = get_follow(conn, FID)
    assert row["failures"] == 2 and row["last_error"] == "boom2"
    record_fetch(conn, FID, ok=True, error=None, display_name="Ann B", topics=["a"])
    row = get_follow(conn, FID)
    assert row["failures"] == 0 and row["last_error"] is None
    assert row["display_name"] == "Ann B" and json.loads(row["topics"]) == ["a"]
    assert row["last_fetched_at"] is not None


def test_replace_followed_posts_counts_new_and_replaces(tmp_path):
    conn = open_db(tmp_path / "v.sqlite")
    follow(conn)
    assert replace_followed_posts(conn, FID, [post()]) == 1
    assert replace_followed_posts(conn, FID, [post(date="2026-11-02", summary={"a": 1})]) == 0
    rows = conn.execute("SELECT * FROM followed_posts").fetchall()
    assert len(rows) == 1 and rows[0]["digest_date"] == "2026-11-02"
    assert json.loads(rows[0]["summary"]) == {"a": 1}
    assert json.loads(rows[0]["authors"]) == ["Ann"] and rows[0]["fetched_at"]


def test_mark_unfollowed_keeps_listed_posts(tmp_path):
    conn = open_db(tmp_path / "v.sqlite")
    follow(conn)
    replace_followed_posts(conn, FID, [post("p1"), post("p2")])
    mark_unfollowed(conn, FID, {"p1"})
    ids = [r["paper_id"] for r in conn.execute("SELECT paper_id FROM followed_posts")]
    assert ids == ["p1"]


def test_set_in_mix_and_replace_directory(tmp_path):
    conn = open_db(tmp_path / "v.sqlite")
    follow(conn)
    set_in_mix(conn, FID, False)
    assert get_follow(conn, FID)["in_mix"] == 0
    entry = dict(feed_id="d1", feed_url="u", handle="@d", display_name="D", topics=["x"],
                 posts_per_day=1.5, preview=[{"t": 1}], checked_at="now")
    replace_directory(conn, [entry])
    replace_directory(conn, [dict(entry, feed_id="d2")])
    rows = conn.execute("SELECT feed_id FROM directory_entries").fetchall()
    assert [r[0] for r in rows] == ["d2"]


def test_collector_takes_over_feed_row(tmp_path):
    conn = open_db(tmp_path / "v.sqlite")
    conn.execute(
        "INSERT INTO papers (id, title, source, url, status, created_at, updated_at)"
        " VALUES ('2611.1', 'old', 'feed', 'u', 'skipped', 'n', 'n')"
    )
    conn.execute(
        "INSERT INTO votes (paper_id, vote, created_at) VALUES ('2611.1', 1, 'n')"
    )
    p = PaperMeta(id="2611.1", title="New", abstract="abs", source="arxiv", url="http://a")
    assert upsert_paper(conn, p) == "updated"
    row = get_paper(conn, "2611.1")
    assert (row["source"], row["status"], row["title"]) == ("arxiv", "new", "New")
    assert conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM papers_fts WHERE papers_fts MATCH 'New'"
    ).fetchone()[0] == 1
