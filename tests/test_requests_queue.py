"""The pipeline's side of the request list: `drain_requests` works through queued summaries."""

import pytest
from conftest import SECTIONS, AlwaysSummary, FakeLLM

from research_pipeline.db import (
    get_summary,
    insert_request,
    mark_request,
    open_db,
    requests_by_status,
    reset_running_requests,
    upsert_paper,
)
from research_pipeline.llm import LlmError, LlmLimitReached, LlmTimeout
from research_pipeline.models import PaperMeta
from research_pipeline.progress import ListReporter
from research_pipeline.requests_queue import drain_requests
from research_pipeline.summarize import LIMIT_STOPPED, TIMEOUT_STOPPED

A, B = "2611.00001", "2611.00002"


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "v.sqlite")
    for pid in (A, B):
        upsert_paper(c, PaperMeta(id=pid, title=f"Paper {pid}", abstract="An abstract.", source="arxiv",
                                  url=f"https://arxiv.org/abs/{pid}"))
    c.commit()
    yield c
    c.close()


def _row(conn, rid):
    return conn.execute("SELECT * FROM requests WHERE id = ?", (rid,)).fetchone()


def _drain(conn, llm, **kw):
    return drain_requests(conn, llm, lambda pid: SECTIONS, 32768, **kw)


def test_drain_summarizes_and_marks_done(conn):
    rid = insert_request(conn, "summarize", {"paper_id": A})
    counts, errors = _drain(conn, AlwaysSummary([]))
    row = _row(conn, rid)
    assert row["status"] == "done" and row["tries"] == 1 and row["error"] is None
    assert row["result"] == '{"summarized": true}'
    assert get_summary(conn, A) is not None
    assert counts == {"requests_done": 1, "requests_failed": 0, "requests_waiting": 0}
    assert errors == []


def test_drain_resets_stale_running(conn):
    rid = insert_request(conn, "summarize", {"paper_id": A})
    mark_request(conn, rid, "running", add_try=True)  # a run that died mid-request
    _drain(conn, AlwaysSummary([]))
    assert _row(conn, rid)["status"] == "done"


def test_reset_running_requests_counts_rows(conn):
    rid = insert_request(conn, "summarize", {"paper_id": A})
    mark_request(conn, rid, "running")
    assert reset_running_requests(conn) == 1
    assert _row(conn, rid)["status"] == "pending"


def test_drain_failure_retries_then_fails(conn):
    rid = insert_request(conn, "summarize", {"paper_id": A})
    seen = []
    for _ in range(3):
        counts, errors = _drain(conn, FakeLLM([LlmError("model broke")]))
        seen.append(_row(conn, rid)["status"])
    assert seen == ["pending", "pending", "failed"]
    row = _row(conn, rid)
    assert row["tries"] == 3 and "model broke" in row["error"]
    assert counts["requests_failed"] == 1 and counts["requests_waiting"] == 0
    assert any("model broke" in e for e in errors)
    # a failed request is not tried again
    llm = FakeLLM([])
    assert _drain(conn, llm)[0]["requests_failed"] == 0 and llm.calls == []


def test_drain_limit_keeps_pending_without_burning_try(conn):
    r1 = insert_request(conn, "summarize", {"paper_id": A})
    r2 = insert_request(conn, "summarize", {"paper_id": B})
    llm = FakeLLM([LlmLimitReached("weekly limit")])
    counts, errors = _drain(conn, llm)
    assert len(llm.calls) == 1  # second row never tried
    for rid in (r1, r2):
        row = _row(conn, rid)
        assert row["status"] == "pending" and row["tries"] == 0
    assert counts == {"requests_done": 0, "requests_failed": 0, "requests_waiting": 2}
    assert LIMIT_STOPPED in errors


def test_drain_timeout_stops(conn):
    r1 = insert_request(conn, "summarize", {"paper_id": A})
    r2 = insert_request(conn, "summarize", {"paper_id": B})
    llm = FakeLLM([LlmTimeout("slow")])
    counts, errors = _drain(conn, llm)
    assert len(llm.calls) == 1
    first = _row(conn, r1)
    assert first["status"] == "pending" and first["tries"] == 1 and "slow" in first["error"]
    second = _row(conn, r2)
    assert second["status"] == "pending" and second["tries"] == 0 and second["error"] is None
    assert TIMEOUT_STOPPED in errors
    assert counts["requests_waiting"] == 2


def test_drain_unknown_paper_fails_the_request(conn):
    rid = insert_request(conn, "summarize", {"paper_id": "2611.99999"})  # never collected
    _drain(conn, AlwaysSummary([]))
    row = _row(conn, rid)
    assert row["status"] == "pending" and row["tries"] == 1 and "2611.99999" in row["error"]


def test_drain_progress_counts_requests(conn):
    insert_request(conn, "summarize", {"paper_id": A})
    insert_request(conn, "summarize", {"paper_id": B})
    rep = ListReporter()
    _drain(conn, AlwaysSummary([]), reporter=rep)
    assert {e.stage for e in rep.events} == {"requests"}  # the inner summarize stage is hidden
    assert [e.kind for e in rep.events][0] == "start" and rep.events[0].total == 2
    assert rep.events[-1].kind == "done"
    steps = [e for e in rep.events if e.kind == "step"]
    assert [(s.label, s.current, s.total, s.outcome) for s in steps] == [
        (A, 1, 2, "done"), (B, 2, 2, "done")]
    notes = [e.label for e in rep.events if e.kind == "note"]
    assert f"writing the summary for {A}" in notes and f"writing the summary for {B}" in notes


def test_drain_with_nothing_queued_is_silent(conn):
    rep = ListReporter()
    counts, errors = _drain(conn, FakeLLM([]), reporter=rep)
    assert rep.events == [] and errors == []
    assert counts == {"requests_done": 0, "requests_failed": 0, "requests_waiting": 0}


def test_requests_by_status_sees_marks(conn):
    rid = insert_request(conn, "summarize", {"paper_id": A})
    mark_request(conn, rid, "failed", error="x", add_try=True)
    row = requests_by_status(conn, "failed")[0]
    assert row["tries"] == 1 and row["error"] == "x"
    mark_request(conn, rid, "pending", undo_try=True)
    assert _row(conn, rid)["tries"] == 0


def _cut_short_three_times(conn):
    rid = insert_request(conn, "summarize", {"paper_id": A})
    for _ in range(3):  # three runs that each died mid-request
        mark_request(conn, rid, "running", add_try=True)
    return rid


def test_drain_fails_request_whose_last_try_was_cut_short(conn):
    from research_pipeline.requests_queue import GAVE_UP_CUT_SHORT

    rid = _cut_short_three_times(conn)
    llm = FakeLLM([])
    counts, _ = _drain(conn, llm)
    row = _row(conn, rid)
    assert row["status"] == "failed" and row["tries"] == 3 and row["error"] == GAVE_UP_CUT_SHORT
    assert llm.calls == []
    assert counts == {"requests_done": 0, "requests_failed": 1, "requests_waiting": 0}


def test_runnable_requests_skips_rows_without_tries_left(conn):
    from research_pipeline.requests_queue import runnable_requests

    _cut_short_three_times(conn)
    assert runnable_requests(conn) == []
    rid = insert_request(conn, "summarize", {"paper_id": B})
    assert [r["id"] for r in runnable_requests(conn)] == [rid]
