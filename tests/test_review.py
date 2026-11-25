from datetime import date

from research_pipeline.db import mark_in_digest, open_db, upsert_paper
from research_pipeline.library import record_skip, record_vote
from research_pipeline.models import PaperMeta
from research_pipeline.review import later_queue, queue_progress, review_queue


def _add(conn, pid, day=None, score=None):
    upsert_paper(conn, PaperMeta(id=pid, title=pid, abstract="A", source="arxiv", url="http://x"))
    if score is not None:
        conn.execute("UPDATE papers SET triage_score = ? WHERE id = ?", (score, pid))
        conn.commit()
    if day:
        mark_in_digest(conn, [pid], date.fromisoformat(day))


def _ids(rows):
    return [r["id"] for r in rows]


def test_queue_oldest_digest_first(tmp_path):
    conn = open_db(tmp_path / "p.db")
    _add(conn, "new", "2026-11-03", 0.9)
    _add(conn, "old-low", "2026-11-01", 0.1)
    _add(conn, "old-high", "2026-11-01", 0.8)
    _add(conn, "old-tie-b", "2026-11-01", 0.5)
    _add(conn, "old-tie-a", "2026-11-01", 0.5)
    _add(conn, "mid", "2026-11-02", 0.3)
    assert _ids(review_queue(conn)) == [
        "old-high", "old-tie-a", "old-tie-b", "old-low", "mid", "new",
    ]
    assert review_queue(conn)[0]["title"] == "old-high"  # full papers rows


def test_queue_excludes_voted_and_skipped(tmp_path):
    conn = open_db(tmp_path / "p.db")
    for p in "abc":
        _add(conn, p, "2026-11-01", 0.5)
    record_vote(conn, "a", 1)
    record_skip(conn, "b")
    assert _ids(review_queue(conn)) == ["c"]


def test_cleared_vote_returns_to_queue(tmp_path):
    conn = open_db(tmp_path / "p.db")
    _add(conn, "a", "2026-11-01", 0.5)
    record_vote(conn, "a", -1)
    assert review_queue(conn) == []
    record_vote(conn, "a", 0)
    assert _ids(review_queue(conn)) == ["a"]


def test_queue_ignores_papers_never_in_a_digest(tmp_path):
    conn = open_db(tmp_path / "p.db")
    _add(conn, "undigested", None, 0.9)
    assert review_queue(conn) == []


def test_later_queue_orders_by_skip_time(tmp_path):
    conn = open_db(tmp_path / "p.db")
    for p in "abc":
        _add(conn, p, "2026-11-01")
    record_skip(conn, "b", now="2026-11-01T00:00:01+00:00")
    record_skip(conn, "c", now="2026-11-01T00:00:02+00:00")
    record_skip(conn, "a", now="2026-11-01T00:00:03+00:00")
    assert _ids(later_queue(conn)) == ["b", "c", "a"]
    record_skip(conn, "b", now="2026-11-01T00:00:04+00:00")  # skip again: to the back
    assert _ids(later_queue(conn)) == ["c", "a", "b"]


def test_later_queue_drops_paper_once_voted(tmp_path):
    conn = open_db(tmp_path / "p.db")
    _add(conn, "a", "2026-11-01")
    record_skip(conn, "a")
    assert _ids(later_queue(conn)) == ["a"]
    record_vote(conn, "a", 1)
    assert later_queue(conn) == []
    record_vote(conn, "a", 0)  # cleared: back in the later list
    assert _ids(later_queue(conn)) == ["a"]


def test_queue_progress_counts(tmp_path):
    conn = open_db(tmp_path / "p.db")
    for p in "abcd":
        _add(conn, p, "2026-11-01")
    _add(conn, "other", "2026-11-02")
    record_vote(conn, "a", 1)
    record_skip(conn, "b")
    record_vote(conn, "c", 1)
    record_vote(conn, "c", 0)  # cleared: not handled
    assert queue_progress(conn, "2026-11-01") == (2, 4)
    assert queue_progress(conn, "2026-11-02") == (0, 1)
    assert queue_progress(conn, "2026-12-31") == (0, 0)


def test_null_triage_score_sorts_last_within_its_day(tmp_path):
    conn = open_db(tmp_path / "p.db")
    _add(conn, "no-score", "2026-11-01")
    _add(conn, "low", "2026-11-01", 0.01)
    _add(conn, "high", "2026-11-01", 0.9)
    _add(conn, "next-day", "2026-11-02", 0.99)
    assert _ids(review_queue(conn)) == ["high", "low", "no-score", "next-day"]
