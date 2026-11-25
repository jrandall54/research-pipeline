"""SQLite storage: schema, status-aware paper upsert, status queries, run log."""

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from research_pipeline.models import PaperMeta, Section, Status
from research_pipeline.summary_models import Critique, PaperSummary, Summary

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

CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts
    USING fts5(title, abstract, tokenize = 'unicode61');
"""

# Statuses that fall back to `triaged` when a new version of the paper arrives.
_RETRIAGE = {Status.in_digest, Status.summarized, Status.fetched, Status.triaged}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


SCHEMA_VERSION = 3


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


def open_db(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the database with all tables, WAL and Row access."""
    conn = sqlite3.connect(path)
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


def _require_paper(conn: sqlite3.Connection, paper_id: str) -> None:
    if get_paper(conn, paper_id) is None:
        raise UnknownPaperError(f"No paper with id {paper_id!r} in the database.")


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
    `unverified`, `read_mode` and `parts`, and, when `prompt_version` is given, a `provenance` dict (see `get_provenance`)
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
        "read_mode": result.read_mode,
        "parts": result.parts,
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
    return PaperSummary(
        summary=Summary.model_validate(s),
        critique=Critique.model_validate(rows["critique"]),
        **meta,
    )


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
