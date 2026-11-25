"""library_api: the app's one door. Reads return Pydantic views; writes go through library/shelf."""

import json
from datetime import date

import pytest
from conftest import FakeEmbedder

from research_pipeline import library_api, review
from research_pipeline.db import UnknownPaperError, mark_in_digest, open_db, save_summary, set_status, upsert_paper
from research_pipeline.library import current_votes, record_vote, set_saved
from research_pipeline.library_api import PaperView, QueueProgress, ShelfItemView, VoteView
from research_pipeline.models import PaperMeta, Status
from research_pipeline.summary_models import Claim, Critique, PaperSummary, Summary


def _add(conn, pid, title, day=None, score=None, reasons=None, abstract="An abstract."):
    upsert_paper(conn, PaperMeta(id=pid, title=title, abstract=abstract, authors=["Ada", "Bob"],
                                 source="arxiv", url=f"https://arxiv.org/abs/{pid}"))
    if score is not None:
        set_status(conn, pid, Status.triaged, score=score, reasons=reasons or [])
    conn.commit()
    if day:
        mark_in_digest(conn, [pid], date.fromisoformat(day))


def _summary(conn, pid):
    c = Claim(text="claim", sections=["1 Intro"])
    save_summary(conn, pid, PaperSummary(
        summary=Summary(one_line="one", problem=c, approach=c, results=c, limitations=c),
        critique=Critique(strengths=[c], weaknesses=[], questions=[]),
        basis="full_text", unverified=[], read_mode="split", parts=3), "m")
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "v.sqlite")
    _add(c, "2611.25005", "Fuzzing eBPF Verifiers", "2026-11-02", 0.9, ["close to seed"])
    _add(c, "2611.25006", "Kernel Driver Testing", "2026-11-02", 0.4)
    _add(c, "2611.25007", "Older Paper", "2026-11-01", 0.7)
    _add(c, "2611.25008", "Never In A Digest")
    yield c
    c.close()


def _ids(views):
    return [v.id for v in views]


def test_get_paper_returns_view_with_summary_vote_saved(conn):
    _summary(conn, "2611.25005")
    record_vote(conn, "2611.25005", 1)
    set_saved(conn, "2611.25005", True)
    v = library_api.get_paper(conn, "2611.25005")
    assert isinstance(v, PaperView) and v.vote == 1 and v.saved and v.summary is not None
    assert v.title == "Fuzzing eBPF Verifiers" and v.authors == ["Ada", "Bob"]
    assert v.triage_score == 0.9 and v.triage_reasons == ["close to seed"]
    assert v.digest_date == "2026-11-02" and v.status == Status.in_digest.value
    assert v.summary.read_mode == "split" and v.summary.parts == 3


def test_get_paper_defaults_without_vote_save_summary(conn):
    v = library_api.get_paper(conn, "2611.25008")
    assert v.vote == 0 and not v.saved and v.summary is None
    assert v.triage_score is None and v.triage_reasons == [] and v.digest_date is None


def test_get_paper_unknown(conn):
    with pytest.raises(UnknownPaperError):
        library_api.get_paper(conn, "9999.99999")


def test_digest_papers_latest_in_score_order(conn):
    assert _ids(library_api.digest_papers(conn)) == ["2611.25005", "2611.25006"]
    assert _ids(library_api.digest_papers(conn, "2026-11-01")) == ["2611.25007"]
    assert library_api.latest_digest_date(conn) == "2026-11-02"


def test_digest_papers_empty_database(tmp_path):
    c = open_db(tmp_path / "empty.sqlite")
    try:
        assert library_api.digest_papers(c) == []
        assert library_api.latest_digest_date(c) is None
    finally:
        c.close()


def test_recent_papers_matches_review_module(conn):
    for n in (1, 2):
        assert _ids(library_api.recent_papers(conn, n)) == [r["id"] for r in review.recent_digest_papers(conn, n)]


def test_saved_papers_newest_first(conn):
    set_saved(conn, "2611.25006", True, now="2026-11-20T00:00:00+00:00")
    set_saved(conn, "2611.25005", True, now="2026-11-21T00:00:00+00:00")
    views = library_api.saved_papers(conn)
    assert _ids(views) == ["2611.25005", "2611.25006"] and all(v.saved for v in views)


@pytest.mark.parametrize("q", ['"', "*", "AND", "-", "(", "fuzz* OR (", ""])
def test_search_never_raises(q, conn):
    assert isinstance(library_api.search_papers(conn, q), list)


def test_search_finds_title_word(conn):
    assert _ids(library_api.search_papers(conn, "fuzzing")) == ["2611.25005"]
    assert _ids(library_api.search_papers(conn, "  ")) == []


def test_search_respects_limit(conn):
    assert len(library_api.search_papers(conn, "abstract", limit=2)) == 2


def test_vote_up_down_clear(conn):
    library_api.vote(conn, "2611.25005", "up")
    assert library_api.get_paper(conn, "2611.25005").vote == 1
    library_api.vote(conn, "2611.25005", "down")
    assert library_api.get_paper(conn, "2611.25005").vote == -1


def test_vote_clear_sets_zero(conn):
    library_api.vote(conn, "2611.25005", "up")
    library_api.vote(conn, "2611.25005", "clear")
    assert library_api.get_paper(conn, "2611.25005").vote == 0
    assert current_votes(conn) == []


def test_vote_bad_direction_and_unknown_paper(conn):
    with pytest.raises(ValueError):
        library_api.vote(conn, "2611.25005", "sideways")
    with pytest.raises(UnknownPaperError):
        library_api.vote(conn, "9999.99999", "up")


def test_save_and_unsave(conn):
    library_api.save(conn, "2611.25005", True)
    assert library_api.get_paper(conn, "2611.25005").saved
    library_api.save(conn, "2611.25005", False)
    assert not library_api.get_paper(conn, "2611.25005").saved


def test_review_queue_later_queue_progress(conn):
    assert _ids(library_api.review_queue(conn)) == ["2611.25007", "2611.25005", "2611.25006"]
    library_api.skip(conn, "2611.25007")
    library_api.vote(conn, "2611.25005", "up")
    assert _ids(library_api.review_queue(conn)) == ["2611.25006"]
    assert _ids(library_api.later_queue(conn)) == ["2611.25007"]
    assert library_api.queue_progress(conn, "2026-11-02") == QueueProgress(handled=1, total=2)
    library_api.unskip(conn, "2611.25007")
    assert library_api.later_queue(conn) == []


def test_list_votes_has_titles(conn):
    record_vote(conn, "2611.25006", -1, now="2026-11-20T00:00:00+00:00")
    record_vote(conn, "2611.25005", 1, now="2026-11-21T00:00:00+00:00")
    record_vote(conn, "2611.25007", 1)
    record_vote(conn, "2611.25007", 0)
    assert library_api.list_votes(conn) == [
        VoteView(paper_id="2611.25006", title="Kernel Driver Testing", vote=-1, voted_at="2026-11-20T00:00:00+00:00"),
        VoteView(paper_id="2611.25005", title="Fuzzing eBPF Verifiers", vote=1, voted_at="2026-11-21T00:00:00+00:00"),
    ]


def test_shelf_add_returns_item_view(conn, tmp_path):
    item = library_api.shelf_add(conn, tmp_path / "my_work", FakeEmbedder(), "an idea about fuzzing kernels")
    assert isinstance(item, ShelfItemView)
    assert item.name == "an-idea-about-fuzzing-kernels" and item.chunks == 1 and item.added_at
    assert library_api.shelf_items(conn) == [item]


def test_shelf_remove_and_sync(conn, tmp_path):
    my_work = tmp_path / "my_work"
    library_api.shelf_add(conn, my_work, FakeEmbedder(), "first note text")
    assert library_api.shelf_remove(conn, my_work, "first-note-text") == ["first-note-text.md"]
    assert library_api.shelf_items(conn) == []
    (my_work / "hand.md").write_text("written by hand\n")
    res = library_api.shelf_sync(conn, my_work, FakeEmbedder())
    assert res.added == ["hand"]
    with pytest.raises(KeyError):
        library_api.shelf_remove(conn, my_work, "nope")


def test_views_serialize_to_json(conn, tmp_path):
    _summary(conn, "2611.25005")
    pid = "2611.25005"
    data = json.loads(library_api.get_paper(conn, pid).model_dump_json())
    assert data["id"] == pid and data["summary"]["summary"]["one_line"] == "one"
    record_vote(conn, pid, 1)
    json.loads(library_api.list_votes(conn)[0].model_dump_json())
    json.loads(library_api.queue_progress(conn, "2026-11-02").model_dump_json())
    item = library_api.shelf_add(conn, tmp_path / "w", FakeEmbedder(), "a note")
    json.loads(item.model_dump_json())
