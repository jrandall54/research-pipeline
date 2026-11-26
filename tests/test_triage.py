import json

import pytest

from research_pipeline.db import get_paper, open_db, papers_with_status, upsert_paper
from research_pipeline.library import record_vote, set_saved
from research_pipeline.models import PaperMeta, Profile, Status
from research_pipeline.shelf import sync_shelf
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


def sync_and_triage(conn, embedder, profile, my_work):
    """What `rpipe run` and `rpipe triage` do: sync the shelf from my_work/, then triage using it."""
    sync_shelf(conn, my_work, embedder)
    return run_triage(conn, embedder, profile, SEEDS, use_shelf=True)


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


def test_votes_and_saves_reach_ranking(conn, embedder):
    fill(conn)
    profile = Profile(topics=[], seeds=[], top_n=10)
    run_triage(conn, embedder, profile, {})  # embeds everything
    base = get_paper(conn, "2501.00003")["triage_score"]
    record_vote(conn, "2501.00001", 1)
    set_saved(conn, "2501.00001", True)
    conn.execute("UPDATE papers SET status = 'new'")
    conn.commit()
    run_triage(conn, embedder, profile, {})
    row = get_paper(conn, "2501.00003")
    assert row["triage_score"] > base
    assert "liked similar: 2501.00001" in json.loads(row["triage_reasons"])


def test_vote_on_paper_without_embedding_is_ignored(conn, embedder):
    fill(conn)
    add(conn, "9999.99999", "Never embedded")
    conn.execute("UPDATE papers SET status = 'skipped' WHERE id = '9999.99999'")  # triage skips it
    record_vote(conn, "9999.99999", 1)  # a real paper, but never embedded
    counts = run_triage(conn, embedder, Profile(topics=[], seeds=[], top_n=10), {})
    assert counts["triaged"] == 4


def test_triage_syncs_shelf_and_boosts_matching_paper(conn, embedder, tmp_path):
    my_work = tmp_path / "my_work"
    my_work.mkdir()
    (my_work / "cooking_pasta.md").write_text("tomato basil\n", encoding="utf-8")
    fill(conn)
    base = PROFILE.model_copy(update={"top_n": 4})
    sync_and_triage(conn, embedder, base, my_work)
    row = get_paper(conn, "2501.00002")
    assert "near your shelf: cooking_pasta" in json.loads(row["triage_reasons"])
    assert row["triage_score"] > get_paper(conn, "2501.00004")["triage_score"]
    assert [r["item_id"] for r in conn.execute("SELECT item_id FROM embeddings WHERE item_id LIKE 'shelf:%'")]


def test_triage_second_run_does_not_reembed_the_note(conn, embedder, tmp_path):
    my_work = tmp_path / "my_work"
    my_work.mkdir()
    (my_work / "idea.md").write_text("tomato basil sauce experiments\n", encoding="utf-8")
    add(conn, "2501.00001", "A")
    sync_and_triage(conn, embedder, PROFILE, my_work)
    add(conn, "2501.00002", "B", "x")
    before = embedder.calls
    sync_and_triage(conn, embedder, PROFILE, my_work)
    # only the new paper and the profile (1 seed + 1 topic) are embedded; the note is not
    assert embedder.calls - before == 1 + 2


def test_triage_without_my_work_ignores_shelf(conn, embedder):
    fill(conn)
    run_triage(conn, embedder, PROFILE, SEEDS)
    assert not any("shelf" in r for p in ("2501.00001", "2501.00002")
                   for r in json.loads(get_paper(conn, p)["triage_reasons"]))


def _shelved_run(conn, embedder, my_work):
    fill(conn)
    sync_and_triage(conn, embedder, PROFILE.model_copy(update={"top_n": 4}), my_work)
    assert "near your shelf: cooking_pasta" in json.loads(get_paper(conn, "2501.00002")["triage_reasons"])
    conn.execute("UPDATE papers SET status = 'new', triage_score = NULL, triage_reasons = NULL")
    conn.commit()


def _no_shelf_effect(conn, embedder, my_work, baseline):
    sync_and_triage(conn, embedder, PROFILE.model_copy(update={"top_n": 4}), my_work)
    for p in ("2501.00001", "2501.00002", "2501.00003", "2501.00004"):
        row = get_paper(conn, p)
        assert not any("shelf" in r for r in json.loads(row["triage_reasons"]))
        assert row["triage_score"] == pytest.approx(baseline[p])


def _baseline(tmp_path, embedder):
    c = open_db(tmp_path / "base.db")
    fill(c)
    run_triage(c, embedder, PROFILE.model_copy(update={"top_n": 4}), SEEDS)
    out = {p: get_paper(c, p)["triage_score"] for p in ("2501.00001", "2501.00002", "2501.00003", "2501.00004")}
    c.close()
    return out


def test_removed_shelf_note_no_longer_boosts(conn, embedder, tmp_path):
    from research_pipeline.shelf import remove_from_shelf

    my_work = tmp_path / "my_work"
    my_work.mkdir()
    (my_work / "cooking_pasta.md").write_text("tomato basil\n", encoding="utf-8")
    _shelved_run(conn, embedder, my_work)
    remove_from_shelf(conn, my_work, "cooking_pasta")
    _no_shelf_effect(conn, embedder, my_work, _baseline(tmp_path, embedder))


def test_hand_deleted_shelf_note_no_longer_boosts(conn, embedder, tmp_path):
    my_work = tmp_path / "my_work"
    my_work.mkdir()
    (my_work / "cooking_pasta.md").write_text("tomato basil\n", encoding="utf-8")
    _shelved_run(conn, embedder, my_work)
    (my_work / "cooking_pasta.md").unlink()
    _no_shelf_effect(conn, embedder, my_work, _baseline(tmp_path, embedder))


def _shelf_ids(conn):
    return [r["item_id"] for r in conn.execute("SELECT item_id FROM embeddings WHERE item_id LIKE 'shelf:%'")]


def test_triage_does_not_sync_shelf(conn, embedder, tmp_path, monkeypatch):
    """Triage only reads the shelf; the sync is the CLI's job (`rpipe run`, `rpipe triage`)."""
    from typer.testing import CliRunner

    from research_pipeline import cli
    from research_pipeline.cli import Pipeline
    from research_pipeline.vault import init_vault

    my_work = tmp_path / "my_work"
    my_work.mkdir()
    (my_work / "idea.md").write_text("tomato basil sauce experiments\n", encoding="utf-8")
    assert run_triage(conn, embedder, PROFILE, SEEDS, use_shelf=True)["triaged"] == 0
    assert _shelf_ids(conn) == []

    vault = tmp_path / "v"
    init_vault(vault, topics=["adversarial robustness"])
    (vault / "my_work" / "idea.md").write_text("tomato basil sauce experiments\n", encoding="utf-8")
    monkeypatch.setattr(cli, "build_pipeline", lambda config: Pipeline(
        [], type(embedder), lambda ids: [], lambda: None, lambda pid: []))
    res = CliRunner().invoke(cli.app, ["triage", "--vault", str(vault)])  # no new papers: still syncs
    assert res.exit_code == 0, res.output
    c = open_db(vault / "vault.sqlite")
    try:
        assert _shelf_ids(c)
    finally:
        c.close()


def test_progress_zero_new_papers_is_start_and_done_only(conn, embedder):
    from research_pipeline.progress import ListReporter

    rep = ListReporter()
    run_triage(conn, embedder, PROFILE, SEEDS, reporter=rep)
    assert [e.kind for e in rep.events] == ["start", "done"]
    assert rep.events[0].total is None


def test_progress_notes_how_many_papers_are_embedded(conn, embedder):
    from research_pipeline.progress import ListReporter

    fill(conn)
    rep = ListReporter()
    run_triage(conn, embedder, PROFILE, SEEDS, reporter=rep)
    assert [e.kind for e in rep.events] == ["start", "note", "done"]
    n = len(papers_with_status(conn, Status.triaged)) + len(papers_with_status(conn, Status.skipped))
    assert n > 0
    assert rep.events[1].label == f"embedding {n} papers"


def test_progress_embedding_stage_has_no_total(conn, embedder):
    """One embed call means no steps, so a total would leave a live bar stuck at 0/N."""
    from research_pipeline.progress import ListReporter

    fill(conn)
    rep = ListReporter()
    run_triage(conn, embedder, PROFILE, SEEDS, reporter=rep)
    starts = [e for e in rep.events if e.kind == "start" and e.stage == "triage"]
    assert starts and starts[0].total is None


def test_triage_embeds_voted_feed_paper(conn, embedder):
    from research_pipeline.db import insert_feed_paper

    post = {"paper_id": "f1", "title": "Adversarial robustness", "abstract": "adversarial robustness " * 2,
            "authors": "[]", "url": "u", "published": None}
    insert_feed_paper(conn, post)
    record_vote(conn, "f1", 1)
    # No new papers: triage still embeds the voted feed paper, and leaves its status alone.
    assert run_triage(conn, embedder, PROFILE, SEEDS) == {"triaged": 0, "skipped": 0, "embedded": 1}
    assert conn.execute("SELECT 1 FROM embeddings WHERE item_id = 'f1'").fetchone()
    assert get_paper(conn, "f1")["status"] == "skipped"
    add(conn, "2501.00009", "Adversarial robustness of models")
    run_triage(conn, embedder, PROFILE, SEEDS)
    reasons = json.loads(get_paper(conn, "2501.00009")["triage_reasons"])
    assert "liked similar: f1" in reasons
