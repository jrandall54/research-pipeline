import pytest

from research_pipeline.collect import run_collect
from research_pipeline.db import get_paper, open_db, start_run
from research_pipeline.models import PaperMeta
from research_pipeline.sources.base import SourceError


class StubSource:
    def __init__(self, name: str, papers: list[PaperMeta] | None = None, error=None):
        self.name = name
        self._papers = papers or []
        self._error = error

    def fetch(self) -> list[PaperMeta]:
        if self._error is not None:
            raise self._error
        return self._papers


def paper(pid="2501.00001", source="arxiv", **kw) -> PaperMeta:
    return PaperMeta(
        id=pid, title=f"Paper {pid}", source=source,
        url=f"https://arxiv.org/abs/{pid}", **kw,
    )


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "t.db")
    yield c
    c.close()


def test_collect_dedupes_same_id_across_sources_and_merges_upvotes(conn):
    arxiv = StubSource("arxiv", [paper(), paper("2501.00002")])
    hf = StubSource(
        "hf_daily",
        [paper(source="hf_daily", hf_upvotes=9, repos=["https://github.com/a/b"])],
    )
    counts, errors = run_collect(conn, [arxiv, hf], start_run(conn))
    assert errors == []
    assert counts == {"inserted": 2, "updated": 1, "unchanged": 0, "source_errors": 0}
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 2
    row = get_paper(conn, "2501.00001")
    assert row["hf_upvotes"] == 9
    assert row["source"] == "arxiv"
    assert row["repos"] == '["https://github.com/a/b"]'


def test_collect_failing_source_does_not_stop_others_and_is_logged(conn):
    bad = StubSource("arxiv", error=SourceError("arXiv API returned HTTP 500"))
    boom = StubSource("weird", error=RuntimeError("kaboom"))
    good = StubSource("hf_daily", [paper(source="hf_daily")])
    counts, errors = run_collect(conn, [bad, boom, good], start_run(conn))
    assert errors == ["arxiv: arXiv API returned HTTP 500", "weird: kaboom"]
    assert counts == {"inserted": 1, "updated": 0, "unchanged": 0, "source_errors": 2}
    assert get_paper(conn, "2501.00001") is not None


def test_collect_second_run_inserts_nothing(conn):
    src = StubSource("arxiv", [paper(), paper("2501.00002")])
    run_collect(conn, [src], start_run(conn))
    counts, errors = run_collect(conn, [src], start_run(conn))
    assert errors == []
    assert counts == {"inserted": 0, "updated": 0, "unchanged": 2, "source_errors": 0}


def test_collect_version_bump_counts_as_updated(conn):
    run_collect(conn, [StubSource("arxiv", [paper()])], start_run(conn))
    counts, _ = run_collect(
        conn, [StubSource("arxiv", [paper(version=2)])], start_run(conn)
    )
    assert counts["updated"] == 1 and counts["inserted"] == 0
    assert get_paper(conn, "2501.00001")["version"] == 2


class CheckerSource(StubSource):
    last_warning = "arxiv: reached max_results (5) before already-seen papers; some may be missed"

    def set_known_checker(self, fn):
        self.checker = fn


def test_collect_gives_sources_a_db_backed_known_checker_and_surfaces_warning(conn):
    run_collect(conn, [StubSource("hf_daily", [paper("2501.00009")])], start_run(conn))
    src = CheckerSource("arxiv", [paper()])
    counts, errors = run_collect(conn, [src], start_run(conn))
    assert src.checker("2501.00009") is True
    assert src.checker("2501.99999") is False
    assert errors == [CheckerSource.last_warning]
    assert counts["source_errors"] == 0 and counts["inserted"] == 1


def test_source_failing_midway_leaves_no_rows_from_that_source(conn):
    good = StubSource("hf_daily", [paper("2501.00001", source="hf_daily")])
    # second paper violates NOT NULL on insert, after the first was already upserted
    bad_paper = PaperMeta.model_construct(
        id="2501.00003", title=None, abstract="", authors=[], source="arxiv",
        url="u", version=1, hf_upvotes=0, repos=[], published=None,
    )
    flaky = StubSource("arxiv", [paper("2501.00002"), bad_paper])
    counts, errors = run_collect(conn, [good, flaky], start_run(conn))
    assert len(errors) == 1 and errors[0].startswith("arxiv:")
    assert counts["source_errors"] == 1
    ids = {r[0] for r in conn.execute("SELECT id FROM papers")}
    assert ids == {"2501.00001"}
    assert conn.execute("SELECT COUNT(*) FROM papers_fts").fetchone()[0] == 1


def test_collect_reports_each_source(conn):
    from research_pipeline.progress import ListReporter

    rep = ListReporter()
    good = StubSource("fake", [paper(), paper("2501.00002"), paper("2501.00003")])
    bad = StubSource("bad", error=SourceError("HTTP 500"))
    run_collect(conn, [good, bad], reporter=rep)
    steps = [(e.label, e.outcome, e.detail) for e in rep.events if e.kind == "step"]
    assert steps == [("fake", "ok", "3 papers"), ("bad", "failed", "HTTP 500")]
    notes = [e.label for e in rep.events if e.kind == "note"]
    assert notes == ["fake: fetching", "bad: fetching"]
    assert [e.kind for e in rep.events][0] == "start"
    assert [e.kind for e in rep.events].count("done") == 1


def test_collect_zero_papers_single_done(conn):
    from research_pipeline.progress import ListReporter

    rep = ListReporter()
    run_collect(conn, [StubSource("fake", [])], reporter=rep)
    kinds = [e.kind for e in rep.events]
    assert kinds.count("done") == 1
    assert all(e.total is None for e in rep.events)


def test_collect_hands_the_stage_handle_to_sources_that_want_it(conn):
    from research_pipeline.progress import ListReporter

    class Chatty(StubSource):
        def set_reporter(self, handle):
            self.handle = handle

        def fetch(self):
            self.handle.note("page 1")
            return []

    run_collect(conn, [Chatty("chatty")], reporter=(rep := ListReporter()))
    assert "page 1" in [e.label for e in rep.events if e.kind == "note"]
