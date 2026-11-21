import json

import pytest

from research_pipeline.db import get_paper, open_db, papers_with_status, upsert_paper
from research_pipeline.models import PaperMeta, Profile, Status
from research_pipeline.triage import run_triage


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "t.db")
    yield c
    c.close()


def add(conn, pid, title, abstract="adversarial robustness " * 2, **kw):
    upsert_paper(conn, PaperMeta(id=pid, title=title, abstract=abstract, source="arxiv",
                                 url=f"https://arxiv.org/abs/{pid}", **kw))


PROFILE = Profile(topics=["adversarial robustness"], seeds=["s1"], top_n=2)
SEEDS = {"s1": "adversarial robustness of models"}


def fill(conn):
    add(conn, "2501.00001", "Adversarial robustness")
    add(conn, "2501.00002", "Cooking pasta", "tomato basil")
    add(conn, "2501.00003", "Robustness of models")
    add(conn, "2501.00004", "Gardening", "soil water")


def test_triage_keeps_top_n_and_skips_rest(conn, embedder):
    fill(conn)
    counts = run_triage(conn, embedder, PROFILE, SEEDS)
    assert counts == {"triaged": 2, "skipped": 2, "embedded": 4}
    kept = {r["id"] for r in papers_with_status(conn, Status.triaged)}
    assert kept == {"2501.00001", "2501.00003"}
    row = get_paper(conn, "2501.00001")
    assert row["triage_score"] > 0
    assert json.loads(row["triage_reasons"])[0].startswith(("closest seed", "topic:"))
    assert get_paper(conn, "2501.00002")["status"] == "skipped"
    assert papers_with_status(conn, Status.new) == []


def test_triage_n_larger_than_papers_keeps_all(conn, embedder):
    add(conn, "2501.00001", "A")
    add(conn, "2501.00002", "B")
    counts = run_triage(conn, embedder, PROFILE.model_copy(update={"top_n": 15}), SEEDS)
    assert counts["triaged"] == 2 and counts["skipped"] == 0


def test_triage_title_only_paper_is_ranked(conn, embedder):
    add(conn, "2501.00001", "Adversarial robustness", abstract="")
    add(conn, "2501.00002", "Cooking pasta", abstract="")
    run_triage(conn, embedder, PROFILE.model_copy(update={"top_n": 1}), SEEDS)
    assert get_paper(conn, "2501.00001")["status"] == "triaged"
    assert get_paper(conn, "2501.00002")["status"] == "skipped"


def test_triage_rerun_does_not_reembed(conn, embedder):
    fill(conn)
    run_triage(conn, embedder, PROFILE, SEEDS)
    # Put one paper back to `new`; its vector is already stored.
    conn.execute("UPDATE papers SET status='new' WHERE id='2501.00002'")
    conn.commit()
    before = embedder.calls
    counts = run_triage(conn, embedder, PROFILE, {})  # no seeds -> only topic embedded
    assert counts["embedded"] == 0
    assert embedder.calls - before == len(PROFILE.topics)


def test_vectors_stored_per_model(conn, embedder):
    add(conn, "2501.00001", "A")
    run_triage(conn, embedder, PROFILE, SEEDS)
    row = conn.execute("SELECT * FROM embeddings WHERE item_id='2501.00001'").fetchone()
    assert row["embedding_model"] == "fake-bow"
    assert row["dim"] == 256 and len(row["vector"]) == 256 * 4


def test_only_new_papers_are_triaged(conn, embedder):
    fill(conn)
    run_triage(conn, embedder, PROFILE, SEEDS)
    add(conn, "2501.00009", "Another")
    counts = run_triage(conn, embedder, PROFILE, SEEDS)
    assert counts["triaged"] + counts["skipped"] == 1


def test_no_new_papers(conn, embedder):
    assert run_triage(conn, embedder, PROFILE, SEEDS) == {
        "triaged": 0, "skipped": 0, "embedded": 0}


def test_skipped_papers_still_in_fts(conn, embedder):
    fill(conn)
    run_triage(conn, embedder, PROFILE, SEEDS)
    assert get_paper(conn, "2501.00004")["status"] == "skipped"
    hits = conn.execute("SELECT rowid FROM papers_fts WHERE papers_fts MATCH 'gardening'").fetchall()
    assert len(hits) == 1
