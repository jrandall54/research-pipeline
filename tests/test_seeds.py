import json

import pytest

from research_pipeline.db import open_db, upsert_paper
from research_pipeline.models import PaperMeta
from research_pipeline.seeds import SeedFetchError, resolve_seed_texts


def meta(pid: str, title="T", abstract="A") -> PaperMeta:
    return PaperMeta(
        id=pid, title=f"{title} {pid}", abstract=abstract, source="arxiv",
        url=f"https://arxiv.org/abs/{pid}",
    )


class Fetcher:
    def __init__(self, papers=None, error=None):
        self.papers = papers or []
        self.error = error
        self.calls: list[list[str]] = []

    def __call__(self, ids):
        self.calls.append(list(ids))
        if self.error:
            raise self.error
        return [p for p in self.papers if p.id in ids]


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "t.db")
    yield c
    c.close()


def test_seed_text_comes_from_papers_table(conn, tmp_path):
    upsert_paper(conn, meta("2501.00001"))
    fetch = Fetcher()
    out = resolve_seed_texts(conn, tmp_path / "seeds.json", ["2501.00001"], fetch)
    assert out == {"2501.00001": "T 2501.00001\nA"}
    assert fetch.calls == []


def test_seed_text_comes_from_cache_without_fetching(conn, tmp_path):
    cache = tmp_path / "seeds.json"
    cache.write_text(json.dumps({"2501.00002": "cached text"}))
    fetch = Fetcher()
    out = resolve_seed_texts(conn, cache, ["2501.00002"], fetch)
    assert out == {"2501.00002": "cached text"}
    assert fetch.calls == []


def test_missing_seeds_are_fetched_once_and_cached_not_stored_as_papers(conn, tmp_path):
    cache = tmp_path / "cache" / "seeds.json"
    fetch = Fetcher([meta("2501.00003"), meta("2501.00004")])
    out = resolve_seed_texts(conn, cache, ["2501.00003", "2501.00004"], fetch)
    assert set(out) == {"2501.00003", "2501.00004"}
    assert fetch.calls == [["2501.00003", "2501.00004"]]
    assert json.loads(cache.read_text()) == out
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 0
    # second call is served from the cache
    resolve_seed_texts(conn, cache, ["2501.00003"], fetch)
    assert len(fetch.calls) == 1


def test_fetch_failure_raises_with_the_seeds_already_resolved(conn, tmp_path):
    upsert_paper(conn, meta("2501.00001"))
    fetch = Fetcher(error=RuntimeError("arXiv down"))
    with pytest.raises(SeedFetchError, match="arXiv down") as info:
        resolve_seed_texts(conn, tmp_path / "seeds.json", ["2501.00001", "2501.00009"], fetch)
    assert list(info.value.texts) == ["2501.00001"]
    assert not (tmp_path / "seeds.json").exists()


def test_progress_notes_only_seeds_fetched_from_arxiv(conn, tmp_path):
    from research_pipeline.progress import ListReporter

    cache = tmp_path / "seeds.json"
    cache.write_text(json.dumps({"2401.00002": "cached"}))
    rep = ListReporter()
    resolve_seed_texts(
        conn, cache, ["2401.00002", "2401.00001"], Fetcher([meta("2401.00001")]), reporter=rep
    )
    assert [e.label for e in rep.events if e.kind == "note"] == ["looking up seed 2401.00001"]
