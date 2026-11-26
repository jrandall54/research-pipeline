import json

import pytest

from research_pipeline import library_api
from research_pipeline.db import (
    REQUEST_KINDS,
    SCHEMA_VERSION,
    UnknownPaperError,
    insert_request,
    open_db,
    requests_by_status,
    upsert_paper,
)
from research_pipeline.models import PaperMeta

PID = "2611.25005"


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "v.sqlite")
    upsert_paper(c, PaperMeta(id=PID, title="T", abstract="a", authors=["A"], source="arxiv",
                              url=f"https://arxiv.org/abs/{PID}"))
    c.commit()
    yield c
    c.close()


def test_request_kinds():
    assert REQUEST_KINDS == ("summarize",)


def test_request_inserts_pending(conn):
    rid = library_api.request(conn, "summarize", {"paper_id": PID})
    rows = requests_by_status(conn, "pending")
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == rid and row["kind"] == "summarize" and row["tries"] == 0
    assert json.loads(row["payload"]) == {"paper_id": PID}
    assert row["error"] is None and row["result"] is None
    assert row["created_at"] and row["updated_at"]


def test_request_stores_normalised_id(conn):
    library_api.request(conn, "summarize", {"paper_id": f"https://arxiv.org/abs/{PID}v3"})
    row = requests_by_status(conn, "pending")[0]
    assert json.loads(row["payload"]) == {"paper_id": PID}


def test_request_dedupes_pending(conn):
    a = library_api.request(conn, "summarize", {"paper_id": PID})
    b = library_api.request(conn, "summarize", {"paper_id": f"arXiv:{PID}v2"})
    assert a == b and len(requests_by_status(conn, "pending")) == 1


def test_request_dedupes_running(conn):
    a = library_api.request(conn, "summarize", {"paper_id": PID})
    conn.execute("UPDATE requests SET status = 'running' WHERE id = ?", (a,))
    conn.commit()
    assert library_api.request(conn, "summarize", {"paper_id": PID}) == a


def test_request_unknown_paper_refused(conn):
    with pytest.raises(UnknownPaperError):
        library_api.request(conn, "summarize", {"paper_id": "9999.99999"})
    with pytest.raises(UnknownPaperError):
        library_api.request(conn, "summarize", {"paper_id": "hello"})
    assert requests_by_status(conn, "pending", "running", "done", "failed") == []


def test_request_unknown_kind_refused(conn):
    with pytest.raises(ValueError):
        library_api.request(conn, "fetch_url", {"url": "https://x"})
    assert requests_by_status(conn, "pending") == []


@pytest.mark.parametrize("payload", [{}, {"paper_id": 2611}, {"paper_id": None}])
def test_request_bad_summarize_payload_refused(conn, payload):
    with pytest.raises(ValueError):
        library_api.request(conn, "summarize", payload)
    assert requests_by_status(conn, "pending") == []


@pytest.mark.parametrize("status", ["done", "failed"])
def test_finished_request_can_be_asked_again(conn, status):
    a = library_api.request(conn, "summarize", {"paper_id": PID})
    conn.execute("UPDATE requests SET status = ? WHERE id = ?", (status, a))
    conn.commit()
    b = library_api.request(conn, "summarize", {"paper_id": PID})
    assert b != a
    assert [r["id"] for r in requests_by_status(conn, "pending")] == [b]


def test_requests_by_status_oldest_first(conn):
    ids = [insert_request(conn, "summarize", {"paper_id": str(i)}) for i in range(3)]
    assert [r["id"] for r in requests_by_status(conn, "pending")] == ids
    assert requests_by_status(conn) == []


def test_insert_request_canonical_payload(conn):
    a = insert_request(conn, "summarize", {"paper_id": PID, "b": 1})
    b = insert_request(conn, "summarize", {"b": 1, "paper_id": PID})
    assert a == b
    row = requests_by_status(conn, "pending")[0]
    assert row["payload"] == json.dumps({"b": 1, "paper_id": PID}, sort_keys=True)


def test_fresh_db_is_current_version(tmp_path):
    conn = open_db(tmp_path / "v.sqlite")
    assert SCHEMA_VERSION == 5
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5


def test_old_vault_gains_requests_table(tmp_path):
    path = tmp_path / "v.sqlite"
    conn = open_db(path)
    conn.execute("DROP TABLE requests")
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()
    conn = open_db(path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "requests" in names
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    conn.close()


@pytest.mark.parametrize("payload", [None, "2611.25005", ["2611.25005"], 7])
def test_request_with_a_non_dict_payload_is_a_value_error(conn, payload):
    with pytest.raises(ValueError, match="payload"):
        library_api.request(conn, "summarize", payload)
    assert requests_by_status(conn, "pending") == []


def test_open_request_ids(conn):
    assert library_api.open_request_ids(conn) == set()
    rid = library_api.request(conn, "summarize", {"paper_id": PID})
    assert library_api.open_request_ids(conn) == {rid}
