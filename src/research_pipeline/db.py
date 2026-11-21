"""SQLite storage: schema, status-aware paper upsert, status queries, run log."""

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from research_pipeline.models import PaperMeta, Section, Status
from research_pipeline.summarize import Critique, PaperSummary, Summary

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

CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts
    USING fts5(title, abstract, tokenize = 'unicode61');
"""

# Statuses that fall back to `triaged` when a new version of the paper arrives.
_RETRIAGE = {Status.in_digest, Status.summarized, Status.fetched, Status.triaged}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def open_db(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the database with all tables, WAL and Row access."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


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
    conn: sqlite3.Connection, paper_id: str, result: PaperSummary, model: str
) -> None:
    """Store (or replace) a paper's summary and critique rows. Does not commit.

    The `summary` row's JSON also carries `basis`, `truncated` and `unverified`.
    """
    now = _now()
    summary_json = {
        **result.summary.model_dump(),
        "basis": result.basis,
        "truncated": result.truncated,
        "unverified": result.unverified,
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
    meta = {k: s.pop(k) for k in ("basis", "truncated", "unverified")}
    return PaperSummary(
        summary=Summary.model_validate(s),
        critique=Critique.model_validate(rows["critique"]),
        **meta,
    )


def record_failure(conn: sqlite3.Connection, paper_id: str, error: str) -> None:
    """Note that a stage failed for this paper: store the message and count a retry.

    The paper keeps its status, so the next run tries it again.
    """
    conn.execute(
        "UPDATE papers SET error = ?, retry_count = retry_count + 1, updated_at = ? WHERE id = ?",
        (" ".join(error.split()), _now(), paper_id),
    )
    conn.commit()


def clear_error(conn: sqlite3.Connection, paper_id: str) -> None:
    """Forget an earlier failure message once the paper gets through."""
    conn.execute("UPDATE papers SET error = NULL WHERE id = ?", (paper_id,))
