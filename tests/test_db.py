import json
from datetime import date

from research_pipeline.db import (
    SCHEMA_VERSION,
    finish_run,
    get_paper,
    mark_in_digest,
    open_db,
    papers_with_status,
    set_status,
    start_run,
    upsert_paper,
)
from research_pipeline.models import PaperMeta, Status


def make(**kw) -> PaperMeta:
    base = dict(id="2401.00001", title="T", abstract="A", source="arxiv", url="http://x")
    base.update(kw)
    return PaperMeta(**base)


def test_open_db_creates_all_tables(tmp_path):
    conn = open_db(tmp_path / "p.db")
    names = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table')")
    }
    assert {
        "papers", "summaries", "votes", "saves", "embeddings", "links", "runs", "papers_fts",
    } <= names
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_upsert_inserts_new_as_status_new(tmp_path):
    conn = open_db(tmp_path / "p.db")
    assert upsert_paper(conn, make(authors=["A", "B"], repos=["r1"])) == "inserted"
    row = get_paper(conn, "2401.00001")
    assert row["status"] == "new"
    assert json.loads(row["authors"]) == ["A", "B"]
    assert row["retry_count"] == 0
    assert get_paper(conn, "nope") is None
    assert [r["id"] for r in papers_with_status(conn, Status.new)] == ["2401.00001"]


def test_upsert_same_version_is_unchanged(tmp_path):
    conn = open_db(tmp_path / "p.db")
    upsert_paper(conn, make())
    assert upsert_paper(conn, make()) == "unchanged"
    assert upsert_paper(conn, make(title="other title")) == "unchanged"
    assert get_paper(conn, "2401.00001")["title"] == "T"


def test_upsert_higher_version_resets_in_digest_to_triaged(tmp_path):
    conn = open_db(tmp_path / "p.db")
    upsert_paper(conn, make())
    set_status(conn, "2401.00001", Status.in_digest)
    assert upsert_paper(conn, make(version=2, title="T2", abstract="A2")) == "updated"
    row = get_paper(conn, "2401.00001")
    assert row["status"] == "triaged"
    assert (row["title"], row["abstract"], row["version"]) == ("T2", "A2", 2)


def test_upsert_higher_version_keeps_new_and_skipped(tmp_path):
    conn = open_db(tmp_path / "p.db")
    upsert_paper(conn, make(id="a"))
    upsert_paper(conn, make(id="b"))
    set_status(conn, "b", Status.skipped)
    upsert_paper(conn, make(id="a", version=2))
    upsert_paper(conn, make(id="b", version=2))
    assert get_paper(conn, "a")["status"] == "new"
    assert get_paper(conn, "b")["status"] == "skipped"
    assert get_paper(conn, "b")["version"] == 2


def test_upsert_merges_hf_upvotes_and_repos(tmp_path):
    conn = open_db(tmp_path / "p.db")
    upsert_paper(conn, make(hf_upvotes=3, repos=["r1"]))
    assert upsert_paper(conn, make(hf_upvotes=10, repos=["r2"])) == "updated"
    row = get_paper(conn, "2401.00001")
    assert row["hf_upvotes"] == 10
    assert json.loads(row["repos"]) == ["r1", "r2"]
    assert upsert_paper(conn, make(hf_upvotes=2, repos=["r1"])) == "unchanged"
    assert get_paper(conn, "2401.00001")["hf_upvotes"] == 10


def test_fts_handles_hostile_characters(tmp_path):
    conn = open_db(tmp_path / "p.db")
    upsert_paper(conn, make(title='Attn "is" all* you-need $x^2$ — ünïcode'))
    rows = conn.execute(
        "SELECT rowid FROM papers_fts WHERE papers_fts MATCH ?", ("ünïcode",)
    ).fetchall()
    assert [r[0] for r in rows] == [get_paper(conn, "2401.00001")["rowid"]]


def test_fts_reindexed_on_new_version(tmp_path):
    conn = open_db(tmp_path / "p.db")
    upsert_paper(conn, make(title="oldword"))
    upsert_paper(conn, make(title="newword", version=2))
    q = "SELECT count(*) FROM papers_fts WHERE papers_fts MATCH ?"
    assert conn.execute(q, ("oldword",)).fetchone()[0] == 0
    assert conn.execute(q, ("newword",)).fetchone()[0] == 1


def test_empty_abstract_is_stored(tmp_path):
    conn = open_db(tmp_path / "p.db")
    upsert_paper(conn, make(abstract=""))
    assert get_paper(conn, "2401.00001")["abstract"] == ""


def test_set_status_score_and_reasons_none_leaves_unchanged(tmp_path):
    conn = open_db(tmp_path / "p.db")
    upsert_paper(conn, make())
    set_status(conn, "2401.00001", Status.triaged, score=0.5, reasons=["why"])
    set_status(conn, "2401.00001", Status.in_digest)
    row = get_paper(conn, "2401.00001")
    assert row["status"] == "in_digest"
    assert row["triage_score"] == 0.5
    assert json.loads(row["triage_reasons"]) == ["why"]


def test_run_log_roundtrip(tmp_path):
    conn = open_db(tmp_path / "p.db")
    run_id = start_run(conn)
    finish_run(conn, run_id, {"collected": 5}, ["arxiv: boom"])
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert row["started_at"] and row["finished_at"]
    assert json.loads(row["counts"]) == {"collected": 5}
    assert json.loads(row["errors"]) == ["arxiv: boom"]


def test_mark_in_digest_sets_status_and_date_only_for_given_ids(tmp_path):
    conn = open_db(tmp_path / "p.db")
    upsert_paper(conn, make(id="a"))
    upsert_paper(conn, make(id="b"))
    set_status(conn, "a", Status.triaged, score=0.5)
    mark_in_digest(conn, ["a"], date(2026, 11, 21))
    a, b = get_paper(conn, "a"), get_paper(conn, "b")
    assert (a["status"], a["digest_date"], a["triage_score"]) == ("in_digest", "2026-11-21", 0.5)
    assert (b["status"], b["digest_date"]) == ("new", None)
    mark_in_digest(conn, [], date(2026, 11, 21))  # no-op, no error


def test_fulltext_round_trip_and_unknown(tmp_path):
    from research_pipeline.db import get_fulltext, save_fulltext
    from research_pipeline.models import Section

    conn = open_db(tmp_path / "p.db")
    upsert_paper(conn, make())
    assert get_fulltext(conn, "2401.00001") is None
    secs = [Section(title="Abstract", text="a"), Section(title="1 Intro", text="b")]
    save_fulltext(conn, "2401.00001", secs)
    assert get_fulltext(conn, "2401.00001") == secs
    save_fulltext(conn, "2401.00001", secs[:1])  # overwrite
    assert get_fulltext(conn, "2401.00001") == secs[:1]
    assert get_fulltext(conn, "nope") is None


def test_summary_round_trip_and_absent(tmp_path):
    from research_pipeline.db import get_summary, save_summary
    from research_pipeline.summarize import Claim, Critique, PaperSummary, Summary

    conn = open_db(tmp_path / "p.db")
    upsert_paper(conn, make())
    assert get_summary(conn, "2401.00001") is None
    c = Claim(text="t", sections=["1 Intro"])
    result = PaperSummary(
        summary=Summary(one_line="o", problem=c, approach=c, results=c, limitations=c),
        critique=Critique(strengths=[c], weaknesses=[], questions=["q?"]),
        basis="abstract",
        truncated=True,
        unverified=["t"],
    )
    save_summary(conn, "2401.00001", result, "qwen3:14b")
    assert get_summary(conn, "2401.00001") == result
    rows = conn.execute(
        "SELECT kind, model FROM summaries WHERE paper_id = ? ORDER BY kind", ("2401.00001",)
    ).fetchall()
    assert [(r["kind"], r["model"]) for r in rows] == [
        ("critique", "qwen3:14b"), ("summary", "qwen3:14b")
    ]
    save_summary(conn, "2401.00001", result.model_copy(update={"unverified": []}), "m2")
    assert get_summary(conn, "2401.00001").unverified == []
    assert get_summary(conn, "nope") is None


def test_version_bump_drops_stale_fulltext_summary_and_retries(tmp_path):
    from research_pipeline.db import get_fulltext, get_summary, record_failure, save_fulltext, save_summary
    from research_pipeline.models import Section
    from research_pipeline.summarize import Claim, Critique, PaperSummary, Summary

    conn = open_db(tmp_path / "p.db")
    upsert_paper(conn, make())
    save_fulltext(conn, "2401.00001", [Section(title="Abstract", text="old")])
    c = Claim(text="t", sections=["Abstract"])
    save_summary(
        conn, "2401.00001",
        PaperSummary(
            summary=Summary(one_line="o", problem=c, approach=c, results=c, limitations=c),
            critique=Critique(strengths=[c], weaknesses=[], questions=[]),
            basis="full_text", truncated=False, unverified=[],
        ),
        "m",
    )
    record_failure(conn, "2401.00001", "boom")
    upsert_paper(conn, make(version=2, title="T2", abstract="A2"))
    assert get_fulltext(conn, "2401.00001") is None
    assert get_summary(conn, "2401.00001") is None
    row = get_paper(conn, "2401.00001")
    assert (row["retry_count"], row["error"]) == (0, None)


def _result():
    from research_pipeline.summarize import Claim, Critique, PaperSummary, Summary

    c = Claim(text="t", sections=["1 Intro"])
    return PaperSummary(
        summary=Summary(one_line="o", problem=c, approach=c, results=c, limitations=c),
        critique=Critique(strengths=[c], weaknesses=[], questions=["q?"]),
        basis="full_text", truncated=False, unverified=[],
    )


def test_provenance_saved_and_read(tmp_path):
    from research_pipeline.db import get_provenance, get_summary, save_summary

    conn = open_db(tmp_path / "p.db")
    upsert_paper(conn, make())
    result = _result()
    save_summary(conn, "2401.00001", result, "qwen3:14b",
                 prompt_version="2026-11-p3", paper_version=2, num_ctx=32768)
    prov = get_provenance(conn, "2401.00001")
    assert {k: prov[k] for k in ("model", "prompt_version", "paper_version", "num_ctx")} == {
        "model": "qwen3:14b", "prompt_version": "2026-11-p3", "paper_version": 2, "num_ctx": 32768}
    assert prov["created_at"]
    assert get_summary(conn, "2401.00001") == result  # provenance does not leak into the model
    assert get_provenance(conn, "nope") is None


def test_old_summary_without_provenance_loads(tmp_path):
    from research_pipeline.db import get_provenance, get_summary

    conn = open_db(tmp_path / "p.db")
    upsert_paper(conn, make())
    result = _result()
    old = {**result.summary.model_dump(), "basis": "full_text", "truncated": False, "unverified": []}
    conn.executemany(
        "INSERT INTO summaries (paper_id, kind, text, model, created_at) VALUES (?, ?, ?, ?, ?)",
        [("2401.00001", "summary", json.dumps(old), "m", "2026-01-01"),
         ("2401.00001", "critique", result.critique.model_dump_json(), "m", "2026-01-01")],
    )
    assert get_summary(conn, "2401.00001") == result
    assert get_provenance(conn, "2401.00001") is None


def test_summary_round_trips_needed_ctx(tmp_path):
    from research_pipeline.db import get_summary, save_summary

    conn = open_db(tmp_path / "p.db")
    upsert_paper(conn, make())
    result = _result().model_copy(update={"body_cut": True, "needed_ctx": 65536})
    save_summary(conn, "2401.00001", result, "m")
    assert get_summary(conn, "2401.00001").needed_ctx == 65536
    assert get_summary(conn, "2401.00001") == result
    # An old row has no `needed_ctx` key: it reads as None.
    row = json.loads(conn.execute("SELECT text FROM summaries WHERE kind = 'summary'").fetchone()["text"])
    del row["needed_ctx"]
    conn.execute("UPDATE summaries SET text = ? WHERE kind = 'summary'", (json.dumps(row),))
    assert get_summary(conn, "2401.00001").needed_ctx is None


def test_provenance_has_gpu_share(tmp_path):
    from research_pipeline.db import get_provenance, save_summary

    conn = open_db(tmp_path / "p.db")
    upsert_paper(conn, make())
    save_summary(conn, "2401.00001", _result(), "m", prompt_version="v", gpu_share=0.93)
    assert get_provenance(conn, "2401.00001")["gpu_share"] == 0.93


def test_newer_database_is_refused_and_left_untouched(tmp_path):
    import sqlite3

    import pytest

    from research_pipeline.db import SCHEMA_VERSION, SchemaTooNewError

    path = tmp_path / "p.db"
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    raw.execute("CREATE TABLE marker (x)")
    raw.commit()
    raw.close()
    before = path.read_bytes()
    with pytest.raises(SchemaTooNewError, match="upgrade rpipe"):
        open_db(path)
    assert path.read_bytes() == before
    check = sqlite3.connect(path)
    assert check.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION + 1
    assert [r[0] for r in check.execute("SELECT name FROM sqlite_master")] == ["marker"]
    check.close()


def test_db_does_not_import_summarize():
    import subprocess
    import sys

    code = "import sys, research_pipeline.db; print('research_pipeline.summarize' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_open_db_upgrades_v1_to_v2(tmp_path):
    import sqlite3

    path = tmp_path / "v1.db"
    conn = open_db(path)
    conn.execute("DROP TABLE skips")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    raw = sqlite3.connect(path)
    assert "skips" not in {r[0] for r in raw.execute("SELECT name FROM sqlite_master")}
    raw.close()
    conn = open_db(path)
    assert "skips" in {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_open_db_still_refuses_newer_version(tmp_path):
    import pytest

    from research_pipeline.db import SchemaTooNewError

    path = tmp_path / "too_new.db"
    conn = open_db(path)
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    conn.close()
    with pytest.raises(SchemaTooNewError):
        open_db(path)


def test_record_skip_unknown_paper_raises(tmp_path):
    import pytest

    from research_pipeline.db import UnknownPaperError, record_skip

    conn = open_db(tmp_path / "p.db")
    with pytest.raises(UnknownPaperError):
        record_skip(conn, "nope")
    assert conn.execute("SELECT COUNT(*) FROM skips").fetchone()[0] == 0


def test_record_skip_twice_refreshes_timestamp(tmp_path):
    from research_pipeline.db import record_skip

    conn = open_db(tmp_path / "p.db")
    upsert_paper(conn, make())
    record_skip(conn, "2401.00001", now="2026-01-01T00:00:00+00:00")
    record_skip(conn, "2401.00001", now="2026-02-01T00:00:00+00:00")
    rows = conn.execute("SELECT * FROM skips").fetchall()
    assert len(rows) == 1 and rows[0]["skipped_at"] == "2026-02-01T00:00:00+00:00"


def test_clear_skip_is_idempotent(tmp_path):
    from research_pipeline.db import clear_skip, record_skip

    conn = open_db(tmp_path / "p.db")
    upsert_paper(conn, make())
    clear_skip(conn, "2401.00001")  # nothing there: harmless
    record_skip(conn, "2401.00001")
    clear_skip(conn, "2401.00001")
    clear_skip(conn, "2401.00001")
    assert conn.execute("SELECT COUNT(*) FROM skips").fetchone()[0] == 0


def test_dropped_round_trips_and_old_truncated_loads_as_body_cut(tmp_path):
    from research_pipeline.db import get_summary, save_summary
    from research_pipeline.summary_models import Dropped

    conn = open_db(tmp_path / "p.db")
    upsert_paper(conn, make())
    result = _result().model_copy(update={"dropped": [Dropped(title="References", chars=10, reason="non_paper")]})
    save_summary(conn, "2401.00001", result, "m")
    got = get_summary(conn, "2401.00001")
    assert got.dropped == result.dropped and got.body_cut is False
    old = {**result.summary.model_dump(), "basis": "full_text", "truncated": True, "unverified": []}
    conn.execute("UPDATE summaries SET text = ? WHERE paper_id = '2401.00001' AND kind = 'summary'", (json.dumps(old),))
    got = get_summary(conn, "2401.00001")
    assert got.body_cut is True and got.dropped == []
