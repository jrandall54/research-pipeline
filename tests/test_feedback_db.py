import sqlite3

import pytest

from research_pipeline.db import (
    SCHEMA_VERSION, UnknownPaperError, current_votes, is_saved, list_saved, open_db, record_vote, set_saved,
    upsert_paper,
)
from research_pipeline.models import PaperMeta


def _paper(conn, pid="2401.00001"):
    upsert_paper(conn, PaperMeta(id=pid, title="T", abstract="A", source="arxiv",
                                 url="http://x", version=1))
    conn.commit()


def test_latest_vote_wins(tmp_path):
    conn = open_db(tmp_path / "p.db")
    _paper(conn)
    record_vote(conn, "2401.00001", 1)
    record_vote(conn, "2401.00001", -1)
    rows = current_votes(conn)
    assert len(rows) == 1 and rows[0]["vote"] == -1


def test_cleared_vote_excluded(tmp_path):
    conn = open_db(tmp_path / "p.db")
    _paper(conn)
    record_vote(conn, "2401.00001", 1)
    record_vote(conn, "2401.00001", 0)
    assert current_votes(conn) == []


def test_invalid_vote_rejected(tmp_path):
    conn = open_db(tmp_path / "p.db")
    _paper(conn)
    with pytest.raises(ValueError):
        record_vote(conn, "2401.00001", 2)


def test_save_toggle(tmp_path):
    conn = open_db(tmp_path / "p.db")
    _paper(conn)
    assert not is_saved(conn, "2401.00001")
    set_saved(conn, "2401.00001", True)
    set_saved(conn, "2401.00001", True)  # idempotent
    assert is_saved(conn, "2401.00001")
    assert [r["paper_id"] for r in list_saved(conn)] == ["2401.00001"]
    set_saved(conn, "2401.00001", False)
    assert not is_saved(conn, "2401.00001") and list_saved(conn) == []


def test_old_vault_upgrades(tmp_path):
    path = tmp_path / "old.db"
    conn = open_db(path)
    conn.execute("DROP TABLE saves")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()
    conn = open_db(path)
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert "saves" in names
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_vote_on_unknown_paper_is_refused_and_writes_nothing(tmp_path):
    conn = open_db(tmp_path / "p.db")
    with pytest.raises(UnknownPaperError, match="9999.99999"):
        record_vote(conn, "9999.99999", 1)
    assert conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0] == 0


def test_save_on_unknown_paper_is_refused_but_unsave_is_harmless(tmp_path):
    conn = open_db(tmp_path / "p.db")
    with pytest.raises(UnknownPaperError):
        set_saved(conn, "9999.99999", True)
    set_saved(conn, "9999.99999", False)  # nothing to remove, no error
    assert list_saved(conn) == []


def test_foreign_keys_are_enforced_on_every_connection(tmp_path):
    conn = open_db(tmp_path / "p.db")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):  # a writer that skips the check is still stopped
        conn.execute("INSERT INTO votes (paper_id, vote, created_at) VALUES ('nope', 1, 'x')")
