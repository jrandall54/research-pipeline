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
