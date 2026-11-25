"""User actions: votes, saves, review-later skips and the shelf's rows.

Everything here is written by the person using the app (``rpipe vote``, ``rpipe save``,
``rpipe review``, ``rpipe shelf``). The pipeline only reads some of it, through the names in
``PIPELINE_READS``. Storage, schema and paper plumbing stay in ``db.py``.
"""

import sqlite3

from research_pipeline.db import _now, _require_paper

# The only names pipeline modules (triage, rank, digest) may import from this module.
PIPELINE_READS: frozenset[str] = frozenset(
    {"current_votes", "list_saved", "is_saved", "shelf_chunk_rows", "shelf_embedding_id"}
)


def record_vote(
    conn: sqlite3.Connection, paper_id: str, vote: int, now: str | None = None
) -> None:
    """Append a vote (+1, -1, or 0 to clear). Commits. Raises UnknownPaperError for a bad id."""
    if vote not in (-1, 0, 1):
        raise ValueError(f"vote must be -1, 0 or 1, got {vote!r}")
    _require_paper(conn, paper_id)
    conn.execute(
        "INSERT INTO votes (paper_id, vote, created_at) VALUES (?, ?, ?)",
        (paper_id, vote, now or _now()),
    )
    conn.commit()


def record_skip(conn: sqlite3.Connection, paper_id: str, now: str | None = None) -> None:
    """Mark a paper "review later" (refreshing the time if already skipped). Commits."""
    _require_paper(conn, paper_id)
    conn.execute(
        "INSERT INTO skips (paper_id, skipped_at) VALUES (?, ?)"
        " ON CONFLICT(paper_id) DO UPDATE SET skipped_at = excluded.skipped_at",
        (paper_id, now or _now()),
    )
    conn.commit()


def clear_skip(conn: sqlite3.Connection, paper_id: str) -> None:
    """Remove a paper from the review-later list (harmless if it is not there). Commits."""
    conn.execute("DELETE FROM skips WHERE paper_id = ?", (paper_id,))
    conn.commit()


def current_votes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Latest vote per paper, leaving out papers whose latest vote is 0."""
    return conn.execute(
        "SELECT paper_id, vote, created_at FROM votes WHERE id IN"
        " (SELECT MAX(id) FROM votes GROUP BY paper_id) AND vote != 0"
        " ORDER BY id"
    ).fetchall()


def set_saved(
    conn: sqlite3.Connection, paper_id: str, saved: bool, now: str | None = None
) -> None:
    """Save or unsave a paper (idempotent). Commits. Saving an unknown id raises UnknownPaperError."""
    if saved:
        _require_paper(conn, paper_id)
        conn.execute(
            "INSERT OR IGNORE INTO saves (paper_id, created_at) VALUES (?, ?)",
            (paper_id, now or _now()),
        )
    else:
        conn.execute("DELETE FROM saves WHERE paper_id = ?", (paper_id,))
    conn.commit()


def is_saved(conn: sqlite3.Connection, paper_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM saves WHERE paper_id = ?", (paper_id,)
    ).fetchone() is not None


def list_saved(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Saved papers, newest save first; columns paper_id, created_at."""
    return conn.execute(
        "SELECT paper_id, created_at FROM saves ORDER BY created_at DESC, rowid DESC"
    ).fetchall()


def shelf_embedding_id(name: str, idx: int) -> str:
    """The ``embeddings.item_id`` for chunk ``idx`` of shelf item ``name``."""
    return f"shelf:{name}#{idx}"


def _delete_shelf_embeddings(conn: sqlite3.Connection, name: str) -> None:
    # Only this item's own chunk ids: "shelf:<name>#<digits>". A bare prefix match would also
    # hit item "notes#2" (ids "shelf:notes#2#0") when deleting item "notes".
    prefix = f"shelf:{name}#"
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = conn.execute(
        "SELECT item_id FROM embeddings WHERE item_id LIKE ? ESCAPE '\\'", (escaped + "%",)
    ).fetchall()
    own = [r[0] for r in rows if r[0].startswith(prefix) and r[0][len(prefix):].isdigit()]
    conn.executemany("DELETE FROM embeddings WHERE item_id = ?", [(i,) for i in own])


def upsert_shelf_item(
    conn: sqlite3.Connection, name: str, content_hash: str, chunks: list[str]
) -> None:
    """Store a shelf item, replacing any earlier chunks and their embeddings. Commits."""
    delete_shelf_item(conn, name, commit=False)
    conn.execute(
        "INSERT INTO shelf_items (name, content_hash, added_at) VALUES (?, ?, ?)",
        (name, content_hash, _now()),
    )
    conn.executemany(
        "INSERT INTO shelf_chunks (item_name, chunk_idx, text) VALUES (?, ?, ?)",
        [(name, i, text) for i, text in enumerate(chunks)],
    )
    conn.commit()


def delete_shelf_item(conn: sqlite3.Connection, name: str, *, commit: bool = True) -> None:
    """Remove a shelf item, its chunks (cascade) and its embeddings. Idempotent."""
    _delete_shelf_embeddings(conn, name)
    conn.execute("DELETE FROM shelf_items WHERE name = ?", (name,))
    if commit:
        conn.commit()


def list_shelf_items(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Shelf items by name, with their chunk counts."""
    return conn.execute(
        "SELECT i.name, i.content_hash, i.added_at, COUNT(c.chunk_idx) AS n_chunks"
        " FROM shelf_items i LEFT JOIN shelf_chunks c ON c.item_name = i.name"
        " GROUP BY i.name ORDER BY i.name"
    ).fetchall()


def shelf_chunk_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every shelf chunk, ordered by item then position."""
    return conn.execute(
        "SELECT item_name, chunk_idx, text FROM shelf_chunks ORDER BY item_name, chunk_idx"
    ).fetchall()
