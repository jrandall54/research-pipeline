"""SQLite storage: schema, status-aware paper upsert, status queries, run log."""

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from research_pipeline.models import PaperMeta, Section, Status
from research_pipeline.summary_models import Critique, Diagram, PaperSummary, Summary

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    rowid          INTEGER PRIMARY KEY,
    id             TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL,
    abstract       TEXT NOT NULL DEFAULT '',
    authors        TEXT NOT NULL DEFAULT '[]',
    source         TEXT NOT NULL,
    url            TEXT NOT NULL,
    version        INTEGER NOT NULL DEFAULT 1,
    hf_upvotes     INTEGER NOT NULL DEFAULT 0,
    repos          TEXT NOT NULL DEFAULT '[]',
    published      TEXT,
    status         TEXT NOT NULL DEFAULT 'new',
    triage_score   REAL,
    triage_reasons TEXT,
    digest_date    TEXT,
    retry_count    INTEGER NOT NULL DEFAULT 0,
    error          TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_papers_status ON papers(status);

CREATE TABLE IF NOT EXISTS summaries (
    paper_id   TEXT NOT NULL REFERENCES papers(id),
    kind       TEXT NOT NULL,
    text       TEXT NOT NULL,
    model      TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (paper_id, kind)
);

CREATE TABLE IF NOT EXISTS fulltexts (
    paper_id   TEXT PRIMARY KEY REFERENCES papers(id),
    sections   TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS votes (
    id         INTEGER PRIMARY KEY,
    paper_id   TEXT NOT NULL REFERENCES papers(id),
    vote       INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS saves (
    paper_id   TEXT PRIMARY KEY REFERENCES papers(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skips (
    paper_id   TEXT PRIMARY KEY REFERENCES papers(id),
    skipped_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings (
    item_id         TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    vector          BLOB NOT NULL,
    dim             INTEGER NOT NULL,
    PRIMARY KEY (item_id, embedding_model)
);

CREATE TABLE IF NOT EXISTS links (
    src_id     TEXT NOT NULL,
    dst_id     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (src_id, dst_id, kind)
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    counts      TEXT,
    errors      TEXT
);

CREATE TABLE IF NOT EXISTS shelf_items (
    name         TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    added_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shelf_chunks (
    item_name TEXT NOT NULL REFERENCES shelf_items(name) ON DELETE CASCADE,
    chunk_idx INTEGER NOT NULL,
    text      TEXT NOT NULL,
    PRIMARY KEY (item_name, chunk_idx)
);

-- The request list: work the app asks the pipeline to do (e.g. summarize one paper now).
CREATE TABLE IF NOT EXISTS requests (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL,          -- canonical JSON: json.dumps(payload, sort_keys=True)
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | failed
    tries      INTEGER NOT NULL DEFAULT 0,
    error      TEXT,
    result     TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Phase 10 sharing: the feeds you follow, their posts, and the public directory.
CREATE TABLE IF NOT EXISTS follows (
    feed_id        TEXT PRIMARY KEY,
    feed_url       TEXT NOT NULL,
    handle         TEXT NOT NULL,
    display_name   TEXT NOT NULL,
    topics         TEXT NOT NULL DEFAULT '[]',
    in_mix         INTEGER NOT NULL DEFAULT 1,
    followed_at    TEXT NOT NULL,
    unfollowed_at  TEXT,
    last_fetched_at TEXT,
    last_error     TEXT,
    failures       INTEGER NOT NULL DEFAULT 0,
    active         INTEGER NOT NULL DEFAULT 1,
    archive_hashes TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS followed_posts (
    feed_id     TEXT NOT NULL,
    paper_id    TEXT NOT NULL,
    digest_date TEXT NOT NULL,
    rank        INTEGER NOT NULL,
    title       TEXT NOT NULL,
    abstract    TEXT NOT NULL,
    authors     TEXT NOT NULL DEFAULT '[]',
    url         TEXT NOT NULL,
    published   TEXT,
    summary     TEXT,
    reasons     TEXT NOT NULL DEFAULT '[]',
    provenance  TEXT,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (feed_id, paper_id)
);
CREATE INDEX IF NOT EXISTS idx_followed_posts_date ON followed_posts(digest_date);

CREATE TABLE IF NOT EXISTS directory_entries (
    feed_id       TEXT PRIMARY KEY,
    feed_url      TEXT NOT NULL,
    handle        TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    topics        TEXT NOT NULL DEFAULT '[]',
    posts_per_day REAL NOT NULL DEFAULT 0,
    preview       TEXT NOT NULL DEFAULT '[]',
    checked_at    TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts
    USING fts5(title, abstract, tokenize = 'unicode61');
"""

# Statuses that fall back to `triaged` when a new version of the paper arrives.
_RETRIAGE = {Status.in_digest, Status.summarized, Status.fetched, Status.triaged}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


SCHEMA_VERSION = 5


class SchemaTooNewError(RuntimeError):
    """The database was written by a newer rpipe than the one running."""


class UnknownPaperError(ValueError):
    """A vote or save named a paper id that is not in the database."""


def _migrate(conn: sqlite3.Connection, from_version: int) -> None:
    """Bring an older database up to SCHEMA_VERSION (one ``if`` step per version)."""
    if from_version < 1:
        pass  # v0 -> v1: the `saves` table is added by CREATE TABLE IF NOT EXISTS
    if from_version < 2:
        pass  # v1 -> v2: the `skips` table is added by CREATE TABLE IF NOT EXISTS
    if from_version < 3:
        pass  # v2 -> v3: `shelf_items` and `shelf_chunks` are added by CREATE TABLE IF NOT EXISTS
    if from_version < 4:
        pass  # v3 -> v4: the `requests` table is added by CREATE TABLE IF NOT EXISTS
    if from_version < 5:
        pass  # v4 -> v5: follows, followed_posts, directory_entries via CREATE TABLE IF NOT EXISTS


def open_db(path: Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open (creating if needed) the database with all tables, WAL and Row access.

    ``check_same_thread=False`` is for the app server only: one request opens, uses and closes its
    connection strictly one step after another, but those steps may run on different worker threads.
    """
    # Wait up to 5 s for another connection to finish writing before failing with "database is
    # locked" (Python's default, written out: app commands write while a locked pipeline job runs).
    conn = sqlite3.connect(path, timeout=5.0, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    # Check the version before anything that could write to the file (WAL, tables, migration).
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        conn.close()
        raise SchemaTooNewError(
            f"{path} has database version {version}, but this rpipe only understands up to"
            f" {SCHEMA_VERSION}. It was written by a newer rpipe. Please upgrade rpipe;"
            " the file was not changed."
        )
    conn.execute("PRAGMA foreign_keys=ON")  # per connection, so set on every open
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    if version < SCHEMA_VERSION:
        _migrate(conn, version)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return conn


# Kinds of work the app may put on the request list. The pipeline knows how to do each one.
REQUEST_KINDS: tuple[str, ...] = ("summarize",)


def insert_request(conn: sqlite3.Connection, kind: str, payload: dict) -> int:
    """Add a `pending` request and return its id. Commits.

    If a `pending` or `running` request with the same kind and payload is already on the
    list, return that one's id instead, so asking twice does not queue the work twice.
    Callers validate `kind` and `payload` (see ``library_api.request``).
    """
    canonical = json.dumps(payload, sort_keys=True)
    existing = conn.execute(
        "SELECT id FROM requests WHERE kind = ? AND payload = ? AND status IN ('pending', 'running')"
        " ORDER BY id LIMIT 1",
        (kind, canonical),
    ).fetchone()
    if existing is not None:
        return existing["id"]
    now = _now()
    cur = conn.execute(
        "INSERT INTO requests (kind, payload, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (kind, canonical, now, now),
    )
    conn.commit()
    return cur.lastrowid


def requests_by_status(conn: sqlite3.Connection, *statuses: str) -> list[sqlite3.Row]:
    """Requests whose status is one of `statuses`, oldest first (none named -> empty list)."""
    if not statuses:
        return []
    marks = ", ".join("?" * len(statuses))
    return conn.execute(
        f"SELECT * FROM requests WHERE status IN ({marks}) ORDER BY id", statuses
    ).fetchall()


def mark_request(
    conn: sqlite3.Connection,
    rid: int,
    status: str,
    *,
    error: str | None = None,
    result: dict | None = None,
    add_try: bool = False,
    undo_try: bool = False,
) -> None:
    """Set a request's status, `error` and `result` (None clears them) and `updated_at`. Commits.

    ``add_try`` counts one more try (taken as the work starts); ``undo_try`` gives one back
    (the work stopped for a reason that was not the request's fault, like a usage limit).
    """
    delta = int(add_try) - int(undo_try)
    conn.execute(
        "UPDATE requests SET status = ?, error = ?, result = ?, tries = MAX(0, tries + ?),"
        " updated_at = ? WHERE id = ?",
        (status, error, json.dumps(result, sort_keys=True) if result is not None else None,
         delta, _now(), rid),
    )
    conn.commit()


def reset_running_requests(conn: sqlite3.Connection) -> int:
    """Put `running` requests back to `pending` and return how many. Commits.

    A request is only `running` while a pipeline command works on it under the run lock, so
    one still `running` when the next drain starts was left by a run that died mid-request.
    Its try stays counted (the drain then fails a row that has no tries left).
    """
    cur = conn.execute(
        "UPDATE requests SET status = 'pending', updated_at = ? WHERE status = 'running'", (_now(),)
    )
    conn.commit()
    return cur.rowcount


def _require_paper(conn: sqlite3.Connection, paper_id: str) -> None:
    if get_paper(conn, paper_id) is None:
        raise UnknownPaperError(f"No paper with id {paper_id!r} in the database.")


def _index(conn: sqlite3.Connection, rowid: int, title: str, abstract: str) -> None:
    conn.execute("DELETE FROM papers_fts WHERE rowid = ?", (rowid,))
    conn.execute(
        "INSERT INTO papers_fts(rowid, title, abstract) VALUES (?, ?, ?)",
        (rowid, title, abstract),
    )


def upsert_paper(
    conn: sqlite3.Connection, p: PaperMeta
) -> Literal["inserted", "updated", "unchanged"]:
    """Insert a paper or merge it into the stored row; see docs/guide/data-model.md.

    Does not commit: the caller owns the transaction (``run_collect`` commits per source).
    """
    now = _now()
    row = conn.execute("SELECT * FROM papers WHERE id = ?", (p.id,)).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO papers (id, title, abstract, authors, source, url, version,"
            " hf_upvotes, repos, published, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)",
            (
                p.id, p.title, p.abstract, json.dumps(p.authors), p.source, p.url,
                p.version, p.hf_upvotes, json.dumps(p.repos),
                p.published.isoformat() if p.published else None, now, now,
            ),
        )
        _index(conn, cur.lastrowid, p.title, p.abstract)
        return "inserted"

    if row["source"] == "feed":
        # A followed feed's stub: the collector now has the real paper, so it takes the row over.
        conn.execute("DELETE FROM fulltexts WHERE paper_id = ?", (p.id,))
        conn.execute("DELETE FROM summaries WHERE paper_id = ?", (p.id,))
        conn.execute(
            "UPDATE papers SET source=?, title=?, abstract=?, authors=?, url=?, version=?,"
            " hf_upvotes=?, repos=?, published=?, status='new', retry_count=0, error=NULL,"
            " updated_at=? WHERE id=?",
            (
                p.source, p.title, p.abstract, json.dumps(p.authors), p.url, p.version,
                p.hf_upvotes, json.dumps(p.repos),
                p.published.isoformat() if p.published else None, now, p.id,
            ),
        )
        _index(conn, row["rowid"], p.title, p.abstract)
        return "updated"

    upvotes = max(row["hf_upvotes"], p.hf_upvotes)
    stored_repos = json.loads(row["repos"])
    repos = stored_repos + [r for r in p.repos if r not in stored_repos]

    if p.version > row["version"]:
        status = row["status"]
        if Status(status) in _RETRIAGE:
            status = Status.triaged.value
        # A new version invalidates everything derived from the old text.
        conn.execute("DELETE FROM fulltexts WHERE paper_id = ?", (p.id,))
        conn.execute("DELETE FROM summaries WHERE paper_id = ?", (p.id,))
        conn.execute("UPDATE papers SET retry_count = 0, error = NULL WHERE id = ?", (p.id,))
        conn.execute(
            "UPDATE papers SET title=?, abstract=?, authors=?, url=?, version=?,"
            " hf_upvotes=?, repos=?, published=?, status=?, updated_at=? WHERE id=?",
            (
                p.title, p.abstract, json.dumps(p.authors), p.url, p.version, upvotes,
                json.dumps(repos),
                p.published.isoformat() if p.published else row["published"],
                status, now, p.id,
            ),
        )
        _index(conn, row["rowid"], p.title, p.abstract)
        return "updated"

    if upvotes == row["hf_upvotes"] and repos == stored_repos:
        return "unchanged"
    conn.execute(
        "UPDATE papers SET hf_upvotes=?, repos=?, updated_at=? WHERE id=?",
        (upvotes, json.dumps(repos), now, p.id),
    )
    return "updated"


def insert_feed_paper(conn: sqlite3.Connection, post: sqlite3.Row) -> None:
    """Copy a followed post into `papers` as a `source='feed'`, `skipped` row, indexed for search.

    Used when you vote on or save a post of a feed you follow. No commit. The collector's
    `upsert_paper` takes such a row over if it later finds the real paper.
    """
    now = _now()
    cur = conn.execute(
        "INSERT INTO papers (id, title, abstract, authors, source, url, published, status,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, 'feed', ?, ?, 'skipped', ?, ?)",
        (post["paper_id"], post["title"], post["abstract"], post["authors"], post["url"],
         post["published"], now, now),
    )
    _index(conn, cur.lastrowid, post["title"], post["abstract"])


def upsert_follow(
    conn: sqlite3.Connection, feed_id: str, feed_url: str, handle: str, display_name: str,
    topics: list[str],
) -> None:
    """Follow a feed, or re-activate one you unfollowed (clears `unfollowed_at` and forgets the downloaded archives, so the whole past is fetched again). No commit."""
    conn.execute(
        "INSERT INTO follows (feed_id, feed_url, handle, display_name, topics, followed_at)"
        " VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(feed_id) DO UPDATE SET feed_url=excluded.feed_url, handle=excluded.handle,"
        " display_name=excluded.display_name, topics=excluded.topics,"
        # a re-follow starts from scratch: forget which archives were downloaded, so the whole past comes back
        " archive_hashes=CASE WHEN follows.unfollowed_at IS NULL THEN follows.archive_hashes ELSE '{}' END,"
        " failures=CASE WHEN follows.unfollowed_at IS NULL THEN follows.failures ELSE 0 END,"
        " followed_at=CASE WHEN follows.unfollowed_at IS NULL THEN follows.followed_at ELSE excluded.followed_at END,"
        " unfollowed_at=NULL",
        (feed_id, feed_url, handle, display_name, json.dumps(topics), _now()),
    )


def get_follow(conn: sqlite3.Connection, feed_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM follows WHERE feed_id = ?", (feed_id,)).fetchone()


def list_follows(
    conn: sqlite3.Connection, include_unfollowed: bool = False
) -> list[sqlite3.Row]:
    """Follows in follow order; unfollowed ones only when asked for."""
    where = "" if include_unfollowed else " WHERE unfollowed_at IS NULL"
    return conn.execute(
        f"SELECT * FROM follows{where} ORDER BY followed_at, feed_id"
    ).fetchall()


def record_fetch(
    conn: sqlite3.Connection,
    feed_id: str,
    *,
    ok: bool,
    error: str | None,
    display_name: str | None = None,
    topics: list[str] | None = None,
    active: bool | None = None,
    archive_hashes: dict | None = None,
) -> None:
    """Note the outcome of fetching a feed. ok resets `failures`; a failure adds one. No commit.

    Optional fields left as None keep their stored value.
    """
    conn.execute(
        "UPDATE follows SET last_fetched_at = ?,"
        " failures = CASE WHEN ? THEN 0 ELSE failures + 1 END,"
        " last_error = CASE WHEN ? THEN NULL ELSE ? END,"
        " display_name = COALESCE(?, display_name), topics = COALESCE(?, topics),"
        " active = COALESCE(?, active), archive_hashes = COALESCE(?, archive_hashes)"
        " WHERE feed_id = ?",
        (
            _now(), ok, ok, error, display_name,
            json.dumps(topics) if topics is not None else None,
            None if active is None else int(active),
            json.dumps(archive_hashes) if archive_hashes is not None else None,
            feed_id,
        ),
    )


def _dumps_or_none(value: object) -> str | None:
    return None if value is None else json.dumps(value)


def replace_followed_posts(conn: sqlite3.Connection, feed_id: str, posts: list[dict]) -> int:
    """Upsert a feed's posts by (feed_id, paper_id); return how many were new. No commit.

    Each dict has the `followed_posts` column names as keys (`authors`, `reasons`,
    `summary`, `provenance` as Python values; they are stored as JSON).
    """
    now = _now()
    new = 0
    for p in posts:
        exists = conn.execute(
            "SELECT 1 FROM followed_posts WHERE feed_id = ? AND paper_id = ?",
            (feed_id, p["paper_id"]),
        ).fetchone()
        new += exists is None
        conn.execute(
            "INSERT OR REPLACE INTO followed_posts (feed_id, paper_id, digest_date, rank, title,"
            " abstract, authors, url, published, summary, reasons, provenance, fetched_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                feed_id, p["paper_id"], p["digest_date"], p["rank"], p["title"], p["abstract"],
                json.dumps(p["authors"]), p["url"], p["published"],
                _dumps_or_none(p["summary"]), json.dumps(p["reasons"]),
                _dumps_or_none(p["provenance"]), now,
            ),
        )
    return new


def mark_unfollowed(conn: sqlite3.Connection, feed_id: str, keep_paper_ids: set[str]) -> None:
    """Unfollow a feed: stamp `unfollowed_at` and delete its posts except `keep_paper_ids`."""
    conn.execute(
        "UPDATE follows SET unfollowed_at = ?, archive_hashes = '{}', failures = 0 WHERE feed_id = ?",
        (_now(), feed_id),
    )
    for (pid,) in conn.execute(
        "SELECT paper_id FROM followed_posts WHERE feed_id = ?", (feed_id,)
    ).fetchall():
        if pid not in keep_paper_ids:
            conn.execute(
                "DELETE FROM followed_posts WHERE feed_id = ? AND paper_id = ?", (feed_id, pid)
            )


def set_in_mix(conn: sqlite3.Connection, feed_id: str, on: bool) -> None:
    conn.execute("UPDATE follows SET in_mix = ? WHERE feed_id = ?", (int(on), feed_id))


def replace_directory(conn: sqlite3.Connection, entries: list[dict]) -> None:
    """Replace the whole directory snapshot with `entries` (dicts keyed by column name)."""
    conn.execute("DELETE FROM directory_entries")
    for e in entries:
        conn.execute(
            "INSERT INTO directory_entries (feed_id, feed_url, handle, display_name, topics,"
            " posts_per_day, preview, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                e["feed_id"], e["feed_url"], e["handle"], e["display_name"],
                json.dumps(e["topics"]), e["posts_per_day"], json.dumps(e["preview"]),
                e["checked_at"],
            ),
        )


def get_paper(conn: sqlite3.Connection, paper_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()


def papers_with_status(conn: sqlite3.Connection, status: Status) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM papers WHERE status = ? ORDER BY rowid", (status.value,)
    ).fetchall()


def set_status(
    conn: sqlite3.Connection,
    paper_id: str,
    status: Status,
    score: float | None = None,
    reasons: list[str] | None = None,
) -> None:
    """Set a paper's status. `score`/`reasons` of None leave those columns unchanged."""
    conn.execute(
        "UPDATE papers SET status = ?, triage_score = COALESCE(?, triage_score),"
        " triage_reasons = COALESCE(?, triage_reasons), updated_at = ? WHERE id = ?",
        (
            status.value,
            score,
            json.dumps(reasons) if reasons is not None else None,
            _now(),
            paper_id,
        ),
    )
    conn.commit()


def mark_in_digest(conn: sqlite3.Connection, paper_ids: list[str], digest_date: date) -> None:
    """Set the given papers to `in_digest` and record which day's digest listed them."""
    now = _now()
    conn.executemany(
        "UPDATE papers SET status = ?, digest_date = ?, updated_at = ? WHERE id = ?",
        [(Status.in_digest.value, digest_date.isoformat(), now, pid) for pid in paper_ids],
    )
    conn.commit()


def start_run(conn: sqlite3.Connection) -> int:
    cur = conn.execute("INSERT INTO runs (started_at) VALUES (?)", (_now(),))
    conn.commit()
    return cur.lastrowid


def finish_run(
    conn: sqlite3.Connection, run_id: int, counts: dict[str, int], errors: list[str]
) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, counts = ?, errors = ? WHERE id = ?",
        (_now(), json.dumps(counts), json.dumps(errors), run_id),
    )
    conn.commit()


def list_runs(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    """The most recent runs, newest first."""
    return conn.execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def previous_run_unfinished(conn: sqlite3.Connection, run_id: int) -> bool:
    """True if the run just before `run_id` exists and never finished (it crashed or was killed)."""
    row = conn.execute(
        "SELECT finished_at FROM runs WHERE id < ? ORDER BY id DESC LIMIT 1", (run_id,)
    ).fetchone()
    return row is not None and row[0] is None


def save_fulltext(conn: sqlite3.Connection, paper_id: str, sections: list[Section]) -> None:
    """Store (or replace) a paper's full text as a JSON list of sections."""
    conn.execute(
        "INSERT OR REPLACE INTO fulltexts (paper_id, sections, fetched_at) VALUES (?, ?, ?)",
        (paper_id, json.dumps([s.model_dump() for s in sections]), _now()),
    )
    conn.commit()


def get_fulltext(conn: sqlite3.Connection, paper_id: str) -> list[Section] | None:
    """The stored sections for a paper, or None if no full text was saved."""
    row = conn.execute(
        "SELECT sections FROM fulltexts WHERE paper_id = ?", (paper_id,)
    ).fetchone()
    if row is None:
        return None
    return [Section(**d) for d in json.loads(row["sections"])]


def save_summary(
    conn: sqlite3.Connection,
    paper_id: str,
    result: PaperSummary,
    model: str,
    *,
    prompt_version: str | None = None,
    paper_version: int | None = None,
    num_ctx: int | None = None,
    backend: str | None = None,
    gpu_share: float | None = None,
) -> None:
    """Store (or replace) a paper's summary and critique rows. Does not commit.

    The `summary` row's JSON also carries `basis`, `body_cut`, `needed_ctx`, `dropped`,
    `unverified`, `notes_unverified`, `read_mode` and `parts`, and, when `prompt_version` is given, a `provenance` dict (see `get_provenance`)
    that includes `gpu_share` (share of the model on the GPU, None when unknown).
    """
    now = _now()
    summary_json = {
        **result.summary.model_dump(),
        "basis": result.basis,
        "body_cut": result.body_cut,
        "needed_ctx": result.needed_ctx,
        "dropped": [d.model_dump() for d in result.dropped],
        "unverified": result.unverified,
        "notes_unverified": result.notes_unverified,
        "read_mode": result.read_mode,
        "parts": result.parts,
        "checks": result.checks,
    }
    if prompt_version is not None:
        summary_json["provenance"] = {
            "backend": backend,
            "model": model,
            "prompt_version": prompt_version,
            "paper_version": paper_version,
            "num_ctx": num_ctx,
            "gpu_share": gpu_share,
            "created_at": now,
        }
    conn.executemany(
        "INSERT OR REPLACE INTO summaries (paper_id, kind, text, model, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            (paper_id, "summary", json.dumps(summary_json), model, now),
            (paper_id, "critique", result.critique.model_dump_json(), model, now),
        ],
    )


def get_summary(conn: sqlite3.Connection, paper_id: str) -> PaperSummary | None:
    """The stored summary and critique for a paper, or None unless both rows exist."""
    rows = {
        r["kind"]: json.loads(r["text"])
        for r in conn.execute(
            "SELECT kind, text FROM summaries WHERE paper_id = ? AND kind IN"
            " ('summary', 'critique')",
            (paper_id,),
        )
    }
    if set(rows) != {"summary", "critique"}:
        return None
    s = rows["summary"]
    s.pop("provenance", None)  # read it with get_provenance; not part of PaperSummary
    meta = {k: s.pop(k) for k in ("basis", "unverified")}
    meta["body_cut"] = s.pop("body_cut", s.pop("truncated", False))  # old rows say `truncated`
    meta["dropped"] = s.pop("dropped", [])
    meta["needed_ctx"] = s.pop("needed_ctx", None)  # rows from before B.5 have no such key
    meta["read_mode"] = s.pop("read_mode", "whole")  # rows from before B.6 were read whole
    meta["parts"] = s.pop("parts", 1)
    meta["notes_unverified"] = s.pop("notes_unverified", [])  # only reading in parts has notes
    meta["checks"] = s.pop("checks", [])  # rows from before Phase 9 have none
    return PaperSummary(
        summary=Summary.model_validate(s),
        critique=Critique.model_validate(rows["critique"]),
        **meta,
    )


def save_diagram(
    conn: sqlite3.Connection, paper_id: str, diagram: Diagram | None, model: str
) -> None:
    """Store (or replace) a paper's diagram row; `None` deletes it. Does not commit."""
    if diagram is None:
        conn.execute("DELETE FROM summaries WHERE paper_id = ? AND kind = 'diagram'", (paper_id,))
        return
    conn.execute(
        "INSERT OR REPLACE INTO summaries (paper_id, kind, text, model, created_at)"
        " VALUES (?, 'diagram', ?, ?, ?)",
        (paper_id, diagram.model_dump_json(by_alias=True), model, _now()),
    )


def get_diagram(conn: sqlite3.Connection, paper_id: str) -> Diagram | None:
    """The stored diagram for a paper, or None when there is none."""
    row = conn.execute(
        "SELECT text FROM summaries WHERE paper_id = ? AND kind = 'diagram'", (paper_id,)
    ).fetchone()
    return Diagram.model_validate_json(row["text"]) if row else None


def get_provenance(conn: sqlite3.Connection, paper_id: str) -> dict | None:
    """Which model, prompt and paper version made the summary, or None for older rows."""
    row = conn.execute(
        "SELECT text FROM summaries WHERE paper_id = ? AND kind = 'summary'", (paper_id,)
    ).fetchone()
    return json.loads(row["text"]).get("provenance") if row else None


MAX_SUMMARY_TRIES = 3  # a paper that failed this many times is no longer retried

_SUMMARY_CANDIDATES = (
    "(status IN ('triaged', 'fetched')"
    " OR (status = 'in_digest' AND digest_date = ?"
    "     AND NOT EXISTS (SELECT 1 FROM summaries s"
    "                     WHERE s.paper_id = papers.id AND s.kind = 'summary')))"
)


def papers_to_summarize(conn: sqlite3.Connection, today: date) -> list[sqlite3.Row]:
    """Papers the summarize stage should work on, oldest first.

    That is every `triaged` or `fetched` paper, plus papers listed in *today's* digest that
    still have no summary (so a same-day re-run can fill in an outage), skipping any paper
    that has already failed MAX_SUMMARY_TRIES times. Papers from earlier days' digests are
    never touched.
    """
    return conn.execute(
        f"SELECT * FROM papers WHERE {_SUMMARY_CANDIDATES} AND retry_count < ? ORDER BY rowid",
        (today.isoformat(), MAX_SUMMARY_TRIES),
    ).fetchall()


_BACKLOG = (
    "status = 'in_digest' AND (digest_date IS NULL OR digest_date <> ?)"
    " AND NOT EXISTS (SELECT 1 FROM summaries s"
    "                 WHERE s.paper_id = papers.id AND s.kind = 'summary')"
    " AND retry_count < ?"
)


def papers_missing_summary(
    conn: sqlite3.Connection, today: date, limit: int | None = None
) -> list[sqlite3.Row]:
    """The summary backlog: `in_digest` papers from *earlier* digests with no summary.

    Newest first (digest date, then row), skipping papers that already failed
    MAX_SUMMARY_TRIES times. `limit` caps the list (None means all of them).
    """
    sql = f"SELECT * FROM papers WHERE {_BACKLOG} ORDER BY digest_date DESC, rowid DESC"
    params: list = [today.isoformat(), MAX_SUMMARY_TRIES]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def count_missing_summary(conn: sqlite3.Connection, today: date) -> int:
    """How many papers `papers_missing_summary` would return with no limit."""
    return conn.execute(
        f"SELECT COUNT(*) FROM papers WHERE {_BACKLOG}", (today.isoformat(), MAX_SUMMARY_TRIES)
    ).fetchone()[0]


def papers_by_ids(conn: sqlite3.Connection, paper_ids: list[str]) -> list[sqlite3.Row]:
    """The named papers in the order given; raises UnknownPaperError naming any that do not exist."""
    rows = {pid: get_paper(conn, pid) for pid in paper_ids}
    missing = [pid for pid, row in rows.items() if row is None]
    if missing:
        raise UnknownPaperError(f"No paper with id {', '.join(missing)} in your vault. Nothing was summarized.")
    return [rows[pid] for pid in dict.fromkeys(paper_ids)]


def papers_given_up_on(conn: sqlite3.Connection, today: date) -> list[sqlite3.Row]:
    """Papers that would be summarized except that they hit MAX_SUMMARY_TRIES failures."""
    return conn.execute(
        f"SELECT * FROM papers WHERE {_SUMMARY_CANDIDATES} AND retry_count >= ? ORDER BY rowid",
        (today.isoformat(), MAX_SUMMARY_TRIES),
    ).fetchall()


def record_failure(conn: sqlite3.Connection, paper_id: str, error: str) -> None:
    """Note that a stage failed for this paper: store the message and count a failure.

    The paper keeps its status. `papers_to_summarize` offers it again until it has failed
    MAX_SUMMARY_TRIES times.
    """
    conn.execute(
        "UPDATE papers SET error = ?, retry_count = retry_count + 1, updated_at = ? WHERE id = ?",
        (" ".join(error.split()), _now(), paper_id),
    )
    conn.commit()


def clear_error(conn: sqlite3.Connection, paper_id: str) -> None:
    """Forget an earlier failure message once the paper gets through. Does not commit."""
    conn.execute("UPDATE papers SET error = NULL WHERE id = ?", (paper_id,))
