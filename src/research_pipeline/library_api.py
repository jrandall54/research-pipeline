"""The app's door into the database.

Commands that belong to the person using rpipe (as opposed to the nightly pipeline) go
through the functions here: reading papers and the digest, voting, saving, the review
queue, the shelf, and asking the pipeline for work (``request``). Every function takes an
open connection first and returns data (Pydantic objects, lists, ids), never text laid out
for a terminal: ``cli.py`` formats the data for the terminal, and a future web or desktop
app can format the same objects its own way.

This module must stay light: it imports only ``db``, ``library``, ``ids``, ``review``,
``shelf`` and the data shapes from the package, never the language-model, profile-editing
or pipeline-stage code, so the app can run without them. See docs/guide/architecture.md.
"""

import json
import re
import sqlite3
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from research_pipeline import db, ids, library, review, shelf
from research_pipeline.db import REQUEST_KINDS, UnknownPaperError
from research_pipeline.embed import Embedder
from research_pipeline.progress import Reporter
from research_pipeline.shelf import SyncResult
from research_pipeline.summary_models import PaperSummary

__all__ = [
    "PaperView", "ShelfItemView", "QueueProgress", "VoteView", "SyncResult", "UnknownPaperError",
    "get_paper", "digest_papers", "recent_papers", "saved_papers", "search_papers",
    "vote", "save", "skip", "unskip", "review_queue", "later_queue", "queue_progress",
    "list_votes", "latest_digest_date", "shelf_items", "shelf_add", "shelf_remove",
    "shelf_sync", "request",
]


class PaperView(BaseModel):
    """One paper as the app sees it: its metadata plus your vote, save and the stored summary."""

    id: str
    title: str
    abstract: str
    authors: list[str]
    url: str
    published: str | None
    status: str
    digest_date: str | None
    triage_score: float | None
    triage_reasons: list[str]  # why triage kept it (`rpipe review`/`show` print these as "why: ...")
    summary: PaperSummary | None  # None until the paper has been summarized
    vote: Literal[-1, 0, 1]  # your current vote; 0 = none or cleared
    saved: bool


class ShelfItemView(BaseModel):
    """One item on the shelf: its name, when it was indexed, and how many chunks it has."""

    name: str
    added_at: str
    chunks: int


class QueueProgress(BaseModel):
    """How far through one digest's review you are: ``handled`` (voted or skipped) of ``total``."""

    handled: int
    total: int


class VoteView(BaseModel):
    """A current (not cleared) vote, with the paper's title."""

    paper_id: str
    title: str
    vote: Literal[-1, 1]
    voted_at: str


_DIRECTIONS: dict[str, int] = {"up": 1, "down": -1, "clear": 0}


# ---- reading papers ----


def _current_vote(conn: sqlite3.Connection, paper_id: str) -> int:
    row = conn.execute(
        "SELECT vote FROM votes WHERE paper_id = ? ORDER BY id DESC LIMIT 1", (paper_id,)
    ).fetchone()
    return int(row["vote"]) if row else 0


def _view(conn: sqlite3.Connection, row: sqlite3.Row) -> PaperView:
    pid = row["id"]
    return PaperView(
        id=pid,
        title=row["title"],
        abstract=row["abstract"] or "",
        authors=json.loads(row["authors"] or "[]"),
        url=row["url"],
        published=row["published"],
        status=row["status"],
        digest_date=row["digest_date"],
        triage_score=row["triage_score"],
        triage_reasons=json.loads(row["triage_reasons"]) if row["triage_reasons"] else [],
        summary=db.get_summary(conn, pid),
        vote=_current_vote(conn, pid),
        saved=library.is_saved(conn, pid),
    )


def _views(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[PaperView]:
    return [_view(conn, r) for r in rows]


def get_paper(conn: sqlite3.Connection, paper_id: str) -> PaperView:
    """One paper by its exact id. Raises UnknownPaperError if it is not in the database."""
    row = db.get_paper(conn, paper_id)
    if row is None:
        raise UnknownPaperError(f"No paper with id {paper_id!r} in the database.")
    return _view(conn, row)


def latest_digest_date(conn: sqlite3.Connection) -> str | None:
    """The newest digest date (``YYYY-MM-DD``), or None before the first digest."""
    return conn.execute("SELECT MAX(digest_date) FROM papers").fetchone()[0]


def digest_papers(conn: sqlite3.Connection, digest_date: str | None = None) -> list[PaperView]:
    """The papers of one digest (None = the latest), highest triage score first."""
    day = digest_date or latest_digest_date(conn)
    if day is None:
        return []
    rows = conn.execute(
        "SELECT * FROM papers WHERE digest_date = ? ORDER BY triage_score DESC, id ASC", (day,)
    ).fetchall()
    return _views(conn, rows)


def recent_papers(conn: sqlite3.Connection, n: int) -> list[PaperView]:
    """Papers of the latest ``n`` digests: newest digest first, score order within one."""
    return _views(conn, review.recent_digest_papers(conn, n))


def saved_papers(conn: sqlite3.Connection) -> list[PaperView]:
    """Your saved papers, newest save first."""
    return [get_paper(conn, r["paper_id"]) for r in library.list_saved(conn)]


def search_papers(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[PaperView]:
    """Papers whose title or abstract contain every word of ``query``, best match first.

    Only the words are used (each quoted), so search syntax such as ``"``, ``*``, ``AND``
    or brackets is treated as plain text and never raises. No words gives ``[]``.
    """
    tokens = re.findall(r"\w+", query)
    if not tokens:
        return []
    match = " ".join(f'"{t}"' for t in tokens)
    rows = conn.execute(
        "SELECT papers.* FROM papers_fts JOIN papers ON papers.rowid = papers_fts.rowid"
        " WHERE papers_fts MATCH ? ORDER BY papers_fts.rank LIMIT ?",
        (match, limit),
    ).fetchall()
    return _views(conn, rows)


# ---- your actions on papers ----


def vote(conn: sqlite3.Connection, paper_id: str, direction: Literal["up", "down", "clear"]) -> None:
    """Vote a paper up or down, or clear your vote. Commits.

    ValueError for any other direction; UnknownPaperError for an id not in the database.
    """
    if direction not in _DIRECTIONS:
        raise ValueError(f"Direction must be up, down or clear, not {direction!r}.")
    library.record_vote(conn, paper_id, _DIRECTIONS[direction])


def save(conn: sqlite3.Connection, paper_id: str, saved: bool) -> None:
    """Save (``True``) or unsave (``False``) a paper. Idempotent; commits."""
    library.set_saved(conn, paper_id, saved)


def skip(conn: sqlite3.Connection, paper_id: str) -> None:
    """Put a paper on the review-later list. Commits."""
    library.record_skip(conn, paper_id)


def unskip(conn: sqlite3.Connection, paper_id: str) -> None:
    """Take a paper off the review-later list (harmless if it is not there). Commits."""
    library.clear_skip(conn, paper_id)


def review_queue(conn: sqlite3.Connection) -> list[PaperView]:
    """Digest papers still waiting for a decision (no vote, not skipped), oldest digest first."""
    return _views(conn, review.review_queue(conn))


def later_queue(conn: sqlite3.Connection) -> list[PaperView]:
    """Skipped papers still without a vote, in the order you skipped them."""
    return _views(conn, review.later_queue(conn))


def queue_progress(conn: sqlite3.Connection, digest_date: str) -> QueueProgress:
    """How many papers of one digest you have voted on or skipped, out of how many."""
    handled, total = review.queue_progress(conn, digest_date)
    return QueueProgress(handled=handled, total=total)


def list_votes(conn: sqlite3.Connection) -> list[VoteView]:
    """Your current votes (cleared ones left out), oldest first, with each paper's title."""
    titles = {
        r["id"]: r["title"]
        for r in conn.execute(
            "SELECT id, title FROM papers WHERE id IN (SELECT paper_id FROM votes)"
        )
    }
    return [
        VoteView(paper_id=r["paper_id"], title=titles.get(r["paper_id"], ""), vote=r["vote"], voted_at=r["created_at"])
        for r in library.current_votes(conn)
    ]


# ---- the shelf ----


def _item_view(row: sqlite3.Row) -> ShelfItemView:
    return ShelfItemView(name=row["name"], added_at=str(row["added_at"]), chunks=row["n_chunks"])


def shelf_items(conn: sqlite3.Connection) -> list[ShelfItemView]:
    """Everything on the shelf, by name."""
    return [_item_view(r) for r in library.list_shelf_items(conn)]


def shelf_add(conn: sqlite3.Connection, my_work: Path, embedder: Embedder, source: str) -> ShelfItemView:
    """Add a ``.md``/``.txt`` file (copied) or a note's text to the shelf and index it.

    ValueError for another file type or empty text.
    """
    name = shelf.add_to_shelf(conn, my_work, embedder, source)
    return next(i for i in shelf_items(conn) if i.name == name)


def shelf_remove(conn: sqlite3.Connection, my_work: Path, name: str) -> list[str]:
    """Take an item off the shelf; its files move to ``my_work/.removed/``. Returns their new names.

    KeyError (with close matches in the message) if there is no such item.
    """
    return shelf.remove_from_shelf(conn, my_work, name)


def shelf_sync(
    conn: sqlite3.Connection, my_work: Path, embedder: Embedder, *, reporter: Reporter | None = None
) -> SyncResult:
    """Re-index ``my_work/`` after files were added, edited or deleted by hand."""
    return shelf.sync_shelf(conn, my_work, embedder, reporter=reporter)


# ---- asking the pipeline for work ----


def request(conn: sqlite3.Connection, kind: str, payload: dict) -> int:
    """Ask the pipeline to do some work later; return the request's id. Commits.

    Only kinds in ``db.REQUEST_KINDS`` are accepted (ValueError otherwise). For
    ``summarize`` the payload must hold a string ``paper_id``; it is normalised
    (``arXiv:2611.25005v2`` -> ``2611.25005``) and must name a paper already in the
    database, else UnknownPaperError. Asking again while an identical request is still
    pending or running returns the existing id.
    """
    if kind not in REQUEST_KINDS:
        raise ValueError(f"Unknown request kind {kind!r}; expected one of {', '.join(REQUEST_KINDS)}.")
    if kind == "summarize":
        raw = payload.get("paper_id")
        if not isinstance(raw, str):
            raise ValueError("A summarize request needs a paper_id (text, like 2611.25005).")
        try:
            paper_id = ids.normalise_paper_id(raw)
        except ValueError as exc:
            raise UnknownPaperError(f"{raw!r} is not a paper id (expected something like 2611.25005).") from exc
        if db.get_paper(conn, paper_id) is None:
            raise UnknownPaperError(f"No paper with id {paper_id!r} in the database.")
        payload = {**payload, "paper_id": paper_id}
    return db.insert_request(conn, kind, payload)
