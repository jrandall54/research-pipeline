"""Queue logic for `rpipe review`: which papers are still waiting for a decision.

Pure database reads, no terminal I/O. A paper has an *active vote* when the latest
`votes` row for it is not 0 (same rule as `db.current_votes`); a cleared vote (0)
puts it back in the queue.
"""

import sqlite3

_ACTIVE_VOTE = (
    "EXISTS (SELECT 1 FROM votes v WHERE v.paper_id = papers.id AND v.vote != 0"
    " AND v.id = (SELECT MAX(id) FROM votes WHERE paper_id = papers.id))"
)


def review_queue(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Digest papers with no active vote and not skipped, oldest digest first."""
    return conn.execute(
        "SELECT * FROM papers WHERE digest_date IS NOT NULL"
        f" AND NOT {_ACTIVE_VOTE}"
        " AND id NOT IN (SELECT paper_id FROM skips)"
        " ORDER BY digest_date ASC, triage_score DESC, id ASC"
    ).fetchall()


def later_queue(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Skipped papers with no active vote, in the order they were skipped."""
    return conn.execute(
        "SELECT papers.* FROM papers JOIN skips ON skips.paper_id = papers.id"
        f" WHERE NOT {_ACTIVE_VOTE}"
        " ORDER BY skips.skipped_at ASC, papers.id ASC"
    ).fetchall()


def queue_progress(conn: sqlite3.Connection, digest_date: str) -> tuple[int, int]:
    """(handled, total) for one digest date; handled = active vote or skipped."""
    row = conn.execute(
        "SELECT COUNT(*),"
        f" COALESCE(SUM({_ACTIVE_VOTE} OR id IN (SELECT paper_id FROM skips)), 0)"
        " FROM papers WHERE digest_date = ?",
        (digest_date,),
    ).fetchone()
    return int(row[1]), int(row[0])


def recent_digest_papers(conn: sqlite3.Connection, n: int) -> list[sqlite3.Row]:
    """Papers of the latest `n` distinct digest dates: newest date first, digest order within a date."""
    return conn.execute(
        "SELECT * FROM papers WHERE digest_date IN"
        " (SELECT DISTINCT digest_date FROM papers WHERE digest_date IS NOT NULL"
        "  ORDER BY digest_date DESC LIMIT ?)"
        " ORDER BY digest_date DESC, triage_score DESC, id ASC",
        (n,),
    ).fetchall()
