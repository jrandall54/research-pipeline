import pytest

from research_pipeline.db import (
    current_votes, is_saved, list_saved, open_db, record_vote, set_saved, upsert_paper,
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
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
