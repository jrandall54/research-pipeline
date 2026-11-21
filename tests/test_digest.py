from datetime import date

import pytest

from research_pipeline.db import get_paper, open_db, set_status, upsert_paper
from research_pipeline.digest import write_digest
from research_pipeline.models import PaperMeta, Status

DAY = date(2026, 11, 21)
HEALTH = "Collected 100, kept 2, skipped 98. Source errors: none."


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "d.db")
    yield c
    c.close()


def add(conn, pid, score, reasons=("closest seed: s1 (0.71)",), status=Status.triaged, **kw):
    base = dict(id=pid, title=f"Title {pid}", abstract=f"Abstract {pid}", source="arxiv",
                url=f"https://arxiv.org/abs/{pid}", authors=["Ann A", "Bob B"])
    base.update(kw)
    upsert_paper(conn, PaperMeta(**base))
    set_status(conn, pid, status, score=score, reasons=list(reasons))


def test_digest_file_named_by_date_and_ranked_descending(conn, tmp_path):
    add(conn, "2501.00002", 0.5)
    add(conn, "2501.00001", 0.9)
    add(conn, "2501.00003", 0.5)
    path = write_digest(conn, tmp_path, DAY, HEALTH)
    assert path == tmp_path / "digests" / "2026-11-21.md"
    text = path.read_text()
    order = [text.index(f"Title {i}") for i in ("2501.00001", "2501.00002", "2501.00003")]
    assert order == sorted(order)
    assert "## 1. [Title 2501.00001](https://arxiv.org/abs/2501.00001)" in text
    assert "0.90" in text


def test_digest_contains_reasons_and_abstract(conn, tmp_path):
    add(conn, "2501.00001", 0.9, reasons=["closest seed: s1 (0.71)", "HF upvotes: 12"],
        hf_upvotes=12, repos=["https://github.com/a/b"])
    add(conn, "2501.00002", 0.8, abstract="")
    text = write_digest(conn, tmp_path, DAY, HEALTH).read_text()
    assert "**Why it matched**" in text
    assert "- closest seed: s1 (0.71)" in text
    assert "- HF upvotes: 12" in text
    assert "Abstract 2501.00001" in text
    assert "_No abstract available._" in text
    assert "https://arxiv.org/pdf/2501.00001" in text
    assert "https://github.com/a/b" in text
    assert "12" in text


def test_authors_truncated_after_five(conn, tmp_path):
    add(conn, "2501.00001", 0.9, authors=[f"A{i}" for i in range(7)])
    text = write_digest(conn, tmp_path, DAY, HEALTH).read_text()
    assert "A0, A1, A2, A3, A4 et al." in text
    assert "A5" not in text


def test_title_brackets_escaped(conn, tmp_path):
    add(conn, "2501.00001", 0.9, title="Fuzzing [the] kernel:\n a study")
    text = write_digest(conn, tmp_path, DAY, HEALTH).read_text()
    assert r"## 1. [Fuzzing \[the\] kernel: a study](https://arxiv.org/abs/2501.00001)" in text


def test_digest_first_line_is_health_summary_with_errors(conn, tmp_path):
    add(conn, "2501.00001", 0.9)
    summary = "Collected 5, kept 1, skipped 4. Source errors: arxiv: boom"
    text = write_digest(conn, tmp_path, DAY, summary).read_text()
    assert text.splitlines()[0] == summary
    assert "# Research digest — 2026-11-21" in text.splitlines()[1:4]


def test_digest_sets_status_in_digest(conn, tmp_path):
    add(conn, "2501.00001", 0.9)
    add(conn, "2501.00002", 0.1, status=Status.skipped)
    write_digest(conn, tmp_path, DAY, HEALTH)
    row = get_paper(conn, "2501.00001")
    assert row["status"] == "in_digest"
    assert row["digest_date"] == "2026-11-21"
    assert get_paper(conn, "2501.00002")["status"] == "skipped"


def test_empty_triaged_writes_digest_saying_nothing_new(conn, tmp_path):
    path = write_digest(conn, tmp_path, DAY, HEALTH)
    text = path.read_text()
    assert text.splitlines()[0] == HEALTH
    assert "# Research digest — 2026-11-21" in text
    assert "Nothing new matched your profile today." in text


def test_same_day_rerun_keeps_earlier_papers_and_adds_new(conn, tmp_path):
    add(conn, "2501.00001", 0.9)
    write_digest(conn, tmp_path, DAY, HEALTH)
    add(conn, "2501.00002", 0.5)
    text = write_digest(conn, tmp_path, DAY, HEALTH).read_text()
    assert "Title 2501.00001" in text and "Title 2501.00002" in text
    assert "Nothing new" not in text


def test_other_days_papers_not_included(conn, tmp_path):
    add(conn, "2501.00001", 0.9)
    write_digest(conn, tmp_path, date(2026, 11, 20), HEALTH)
    text = write_digest(conn, tmp_path, DAY, HEALTH).read_text()
    assert "Title 2501.00001" not in text
    assert get_paper(conn, "2501.00001")["digest_date"] == "2026-11-20"
