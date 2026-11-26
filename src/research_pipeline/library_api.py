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

from research_pipeline import db, feed_format, ids, library, review, shelf
from research_pipeline.db import REQUEST_KINDS, UnknownPaperError
from research_pipeline.embed import Embedder
from research_pipeline.progress import Reporter
from research_pipeline.shelf import SyncResult
from research_pipeline.summary_models import Diagram, PaperSummary

__all__ = [
    "PaperView", "ShelfItemView", "QueueProgress", "VoteView", "SyncResult", "UnknownPaperError",
    "get_paper", "digest_papers", "recent_papers", "saved_papers", "search_papers",
    "vote", "save", "skip", "unskip", "review_queue", "later_queue", "queue_progress",
    "list_votes", "latest_digest_date", "shelf_items", "shelf_add", "shelf_remove",
    "shelf_sync", "request",
    "ReasonView", "ProvenanceView", "plain_reasons", "digest_dates", "summarize_states",
    "summary_provenance",
    "SourceView", "FollowedPostView", "FollowView", "DirectoryEntryView",
    "follows", "follow_request", "unfollow", "set_in_mix", "following_days", "following_posts",
    "mix_days", "mix_posts", "directory", "followed_post",
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
    diagram: Diagram | None = None  # the stored diagram, if the paper has one


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


class ReasonView(BaseModel):
    """One "why you're seeing this" reason in plain form (see ``plain_reasons``)."""

    kind: Literal["topic", "liked", "upvotes", "author", "keyword", "shelf"]
    label: str
    paper_id: str | None = None


class ProvenanceView(BaseModel):
    """Which model wrote a summary and when (``created_at`` is the stored timestamp)."""

    model: str
    created_at: str


class SourceView(BaseModel):
    """Whose pick a post is: you (``feed_id`` None, ``name`` "You") or a followed feed."""

    feed_id: str | None
    name: str
    handle: str | None = None


class FollowedPostView(BaseModel):
    """One entry of Following or Mix: the paper, who picked it and why they say so.

    ``paper.summary`` is the publisher's summary and ``paper.triage_reasons`` their public
    reasons; ``paper.vote`` and ``paper.saved`` are yours (0/False until you copy it in).
    ``feed_id`` is None for your own paper in Mix. ``sources`` lists you first, then the
    feeds in follow order.
    """

    feed_id: str | None
    paper: PaperView
    digest_date: str
    rank: int
    sources: list[SourceView]
    reasons: list[ReasonView]


class FollowView(BaseModel):
    """A feed you follow. ``waiting`` = followed but not fetched yet; ``stopped`` = see ``_is_stopped``."""

    feed_id: str
    handle: str
    display_name: str
    topics: list[str]
    in_mix: bool
    last_fetched_at: str | None
    last_error: str | None
    stopped: bool
    posts: int
    waiting: bool


class DirectoryEntryView(BaseModel):
    """A feed listed in the public directory, with how many topics it shares with you."""

    feed_id: str
    handle: str
    display_name: str
    topics: list[str]
    posts_per_day: float
    preview: list[dict]
    shared_topics: int
    following: bool


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
        diagram=db.get_diagram(conn, pid),
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

    A post of a feed you follow that is not in your papers yet is copied in first.

    ValueError for any other direction; UnknownPaperError for an id not in the database.
    """
    if direction not in _DIRECTIONS:
        raise ValueError(f"Direction must be up, down or clear, not {direction!r}.")
    if direction != "clear":
        _copy_in_followed_post(conn, paper_id)
    library.record_vote(conn, paper_id, _DIRECTIONS[direction])


def save(conn: sqlite3.Connection, paper_id: str, saved: bool) -> None:
    """Save (``True``) or unsave (``False``) a paper. Idempotent; commits."""
    if saved:
        _copy_in_followed_post(conn, paper_id)
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

    Only kinds in ``db.REQUEST_KINDS`` are accepted, and ``payload`` must be a dict
    (ValueError otherwise). For
    ``summarize`` the payload must hold a string ``paper_id``; it is normalised
    (``arXiv:2611.25005v2`` -> ``2611.25005``) and must name a paper already in the
    database, else UnknownPaperError. Asking again while an identical request is still
    pending or running returns the existing id.
    """
    if kind not in REQUEST_KINDS:
        raise ValueError(f"Unknown request kind {kind!r}; expected one of {', '.join(REQUEST_KINDS)}.")
    if not isinstance(payload, dict):
        raise ValueError(f"A request payload must be a dict, not {type(payload).__name__}.")
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


def open_request_ids(conn: sqlite3.Connection) -> set[int]:
    """Ids of the requests still waiting (`pending`) or being worked on (`running`).

    `rpipe requests add` asks this before ``request`` to tell "queued now" from "already
    waiting" (``request`` returns the existing id for a repeat).
    """
    return {row["id"] for row in db.requests_by_status(conn, "pending", "running")}


# ---- reasons, dates and states for the app's feed ----


def plain_reasons(reasons: list[str]) -> list[ReasonView]:
    """Turn stored triage reason strings into typed reasons, in order.

    ``disliked similar: ...`` and any string not recognised are dropped (the feed only
    explains what drew a paper in).
    """
    out: list[ReasonView] = []
    for text in reasons:
        if parsed := feed_format.parse_reason(text):
            kind, label, paper_id = parsed
            out.append(ReasonView(kind=kind, label=label, paper_id=paper_id))
    return out


def digest_dates(conn: sqlite3.Connection, limit: int = 60) -> list[str]:
    """Distinct digest dates (``YYYY-MM-DD``), newest first, at most ``limit``."""
    rows = conn.execute(
        "SELECT DISTINCT digest_date FROM papers WHERE digest_date IS NOT NULL"
        " ORDER BY digest_date DESC LIMIT ?", (limit,)
    ).fetchall()
    return [r[0] for r in rows]


def summarize_states(
    conn: sqlite3.Connection, paper_ids: list[str]
) -> dict[str, Literal["pending", "running", "done", "failed"]]:
    """Status of the newest ``summarize`` request for each paper; papers with none are absent."""
    if not paper_ids:
        return {}
    marks = ", ".join("?" * len(paper_ids))
    rows = conn.execute(
        f"SELECT json_extract(payload, '$.paper_id') AS pid, status, MAX(id) FROM requests"
        f" WHERE kind = 'summarize' AND json_extract(payload, '$.paper_id') IN ({marks})"
        f" GROUP BY pid", paper_ids
    ).fetchall()
    return {r["pid"]: r["status"] for r in rows}


def summary_provenance(conn: sqlite3.Connection, paper_id: str) -> ProvenanceView | None:
    """Which model made the stored summary and when, or None if unknown (no summary or older row)."""
    prov = db.get_provenance(conn, paper_id)
    if not prov or not prov.get("model") or not prov.get("created_at"):
        return None
    return ProvenanceView(model=prov["model"], created_at=prov["created_at"])


# ---- following other people's feeds ----


def _is_stopped(row: sqlite3.Row) -> bool:
    """Same rule as ``share_fetch.is_stopped`` (library_api must not import pipeline code)."""
    return row["failures"] >= feed_format.STOPPED_AFTER or not row["active"]


def _source(row: sqlite3.Row) -> SourceView:
    return SourceView(feed_id=row["feed_id"], name=row["display_name"], handle=row["handle"])


_YOU = SourceView(feed_id=None, name="You")


def _post_paper_view(conn: sqlite3.Connection, post: sqlite3.Row) -> PaperView:
    try:
        summary = PaperSummary.model_validate(json.loads(post["summary"])) if post["summary"] else None
    except ValueError:  # includes pydantic's ValidationError: a bad summary is shown as none
        summary = None
    pid = post["paper_id"]
    return PaperView(
        id=pid, title=post["title"], abstract=post["abstract"],
        authors=json.loads(post["authors"] or "[]"), url=post["url"], published=post["published"],
        status="feed", digest_date=post["digest_date"], triage_score=None,
        triage_reasons=json.loads(post["reasons"] or "[]"), summary=summary,
        vote=_current_vote(conn, pid), saved=library.is_saved(conn, pid),
    )


def _post_view(conn: sqlite3.Connection, post: sqlite3.Row, sources: list[sqlite3.Row]) -> FollowedPostView:
    paper = _post_paper_view(conn, post)
    return FollowedPostView(
        feed_id=post["feed_id"], paper=paper, digest_date=post["digest_date"], rank=post["rank"],
        sources=[_source(s) for s in sources], reasons=plain_reasons(paper.triage_reasons),
    )


def _copy_in_followed_post(conn: sqlite3.Connection, paper_id: str) -> None:
    """Copy a followed post into ``papers`` (once) so a vote or save has a paper to point at."""
    if db.get_paper(conn, paper_id) is not None:
        return
    post = _newest_post(conn, paper_id)
    if post is not None:
        db.insert_feed_paper(conn, post)


def _newest_post(conn: sqlite3.Connection, paper_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT p.*, f.display_name, f.handle FROM followed_posts p"
        " JOIN follows f ON f.feed_id = p.feed_id WHERE p.paper_id = ?"
        " ORDER BY p.digest_date DESC, p.fetched_at DESC, p.feed_id LIMIT 1", (paper_id,)
    ).fetchone()


def follows(conn: sqlite3.Connection) -> list[FollowView]:
    """The feeds you follow, in the order you followed them."""
    counts = {
        r[0]: r[1] for r in conn.execute("SELECT feed_id, COUNT(*) FROM followed_posts GROUP BY feed_id")
    }
    return [
        FollowView(
            feed_id=r["feed_id"], handle=r["handle"], display_name=r["display_name"],
            topics=json.loads(r["topics"]), in_mix=bool(r["in_mix"]),
            last_fetched_at=r["last_fetched_at"], last_error=r["last_error"],
            stopped=_is_stopped(r), posts=counts.get(r["feed_id"], 0),
            waiting=r["last_fetched_at"] is None,
        )
        for r in db.list_follows(conn)
    ]


def follow_request(conn: sqlite3.Connection, handle: str) -> None:
    """Follow the directory's feed with this handle. Commits; posts arrive at the next fetch.

    ValueError if no directory entry has that handle.
    """
    row = conn.execute(
        "SELECT * FROM directory_entries WHERE lower(handle) = lower(?)", (handle.strip(),)
    ).fetchone()
    if row is None:
        raise ValueError(f"No feed with handle {handle!r} in the directory.")
    db.upsert_follow(
        conn, row["feed_id"], row["feed_url"], row["handle"], row["display_name"],
        json.loads(row["topics"]),
    )
    conn.commit()


def unfollow(conn: sqlite3.Connection, feed_id: str) -> None:
    """Stop following a feed. Its posts go, except papers you saved or voted on. Commits.

    ValueError for a feed you never followed.
    """
    if db.get_follow(conn, feed_id) is None:
        raise ValueError(f"You do not follow a feed with id {feed_id!r}.")
    kept = {r["paper_id"] for r in library.list_saved(conn)} | {
        r["paper_id"] for r in library.current_votes(conn)
    }
    db.mark_unfollowed(conn, feed_id, kept)
    conn.commit()


def set_in_mix(conn: sqlite3.Connection, feed_id: str, on: bool) -> None:
    """Include (or leave out) a followed feed in Mix. Commits."""
    db.set_in_mix(conn, feed_id, on)
    conn.commit()


_ACTIVE_POSTS = (
    "SELECT p.*, f.display_name, f.handle FROM followed_posts p"
    " JOIN follows f ON f.feed_id = p.feed_id WHERE f.unfollowed_at IS NULL"
)
_FOLLOW_ORDER = " ORDER BY f.followed_at, f.feed_id, p.rank"


def following_days(conn: sqlite3.Connection, limit: int = 60) -> list[str]:
    """Dates (``YYYY-MM-DD``) with posts from feeds you follow, newest first."""
    rows = conn.execute(
        "SELECT DISTINCT p.digest_date FROM followed_posts p JOIN follows f ON f.feed_id = p.feed_id"
        " WHERE f.unfollowed_at IS NULL ORDER BY p.digest_date DESC LIMIT ?", (limit,)
    ).fetchall()
    return [r[0] for r in rows]


def following_posts(conn: sqlite3.Connection, day: str) -> list[FollowedPostView]:
    """One day of the feeds you follow: by follow order, then the publisher's rank."""
    rows = conn.execute(_ACTIVE_POSTS + " AND p.digest_date = ?" + _FOLLOW_ORDER, (day,)).fetchall()
    return [_post_view(conn, r, [r]) for r in rows]


def _mix_entries(conn: sqlite3.Connection) -> dict[str, dict]:
    """Every Mix paper by id: ``date`` (earliest among sources), ``own`` row, ``posts`` (follow order)."""
    entries: dict[str, dict] = {}
    for r in conn.execute("SELECT * FROM papers WHERE digest_date IS NOT NULL"):
        entries[r["id"]] = {"date": r["digest_date"], "own": r, "posts": []}
    for r in conn.execute(_ACTIVE_POSTS + " AND f.in_mix = 1" + _FOLLOW_ORDER):
        e = entries.setdefault(r["paper_id"], {"date": r["digest_date"], "own": None, "posts": []})
        e["date"] = min(e["date"], r["digest_date"])
        e["posts"].append(r)
    return entries


def mix_days(conn: sqlite3.Connection, limit: int = 60) -> list[str]:
    """Dates with anything in Mix (your digests plus followed feeds in the mix), newest first."""
    return sorted({e["date"] for e in _mix_entries(conn).values()}, reverse=True)[:limit]


def mix_posts(conn: sqlite3.Connection, day: str) -> list[FollowedPostView]:
    """One day of Mix: your papers first (score order), then followed posts; one entry per paper.

    A paper that is yours and also posted by feeds appears once with every source listed, you
    first, and sits under the earliest date among them.
    """
    entries = {pid: e for pid, e in _mix_entries(conn).items() if e["date"] == day}
    out: list[FollowedPostView] = []
    own = sorted(
        (e for e in entries.values() if e["own"] is not None),
        key=lambda e: (-(e["own"]["triage_score"] or 0.0), e["own"]["id"]),
    )
    for rank, e in enumerate(own, start=1):
        paper = _view(conn, e["own"])
        out.append(FollowedPostView(
            feed_id=None, paper=paper, digest_date=day, rank=rank,
            sources=[_YOU, *(_source(p) for p in e["posts"])], reasons=plain_reasons(paper.triage_reasons),
        ))
    for e in entries.values():  # insertion order = follow order, then rank
        if e["own"] is not None:
            continue
        out.append(_post_view(conn, e["posts"][0], e["posts"]))
    return out


def directory(conn: sqlite3.Connection, my_topics: list[str]) -> list[DirectoryEntryView]:
    """The public directory, most shared topics first, then by name."""
    following = {r["feed_id"] for r in db.list_follows(conn)}
    views = []
    for r in conn.execute("SELECT * FROM directory_entries"):
        topics = json.loads(r["topics"])
        views.append(DirectoryEntryView(
            feed_id=r["feed_id"], handle=r["handle"], display_name=r["display_name"], topics=topics,
            posts_per_day=r["posts_per_day"], preview=json.loads(r["preview"]),
            shared_topics=feed_format.shared_topics(topics, my_topics), following=r["feed_id"] in following,
        ))
    return sorted(views, key=lambda v: (-v.shared_topics, v.display_name.lower()))


def followed_post(conn: sqlite3.Connection, paper_id: str) -> FollowedPostView | None:
    """The newest post of this paper from any feed you follow (or followed and kept), or None."""
    post = _newest_post(conn, paper_id)
    return None if post is None else _post_view(conn, post, [post])
