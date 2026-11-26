from datetime import date

import pytest
from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.db import mark_in_digest, open_db, save_summary, set_status, upsert_paper
from research_pipeline.models import PaperMeta, Status
from research_pipeline.summarize import Claim, Critique, PaperSummary, Summary
from research_pipeline.vault import init_vault

runner = CliRunner()


def _add(vault, pid, title, day, score, abstract="Plain abstract text.", reasons=None):
    conn = open_db(vault / "vault.sqlite")
    upsert_paper(conn, PaperMeta(id=pid, title=title, abstract=abstract, authors=["A"],
                                 source="arxiv", url=f"https://arxiv.org/abs/{pid}"))
    set_status(conn, pid, Status.triaged, score=score, reasons=reasons or [])
    conn.commit()
    mark_in_digest(conn, [pid], date.fromisoformat(day))
    conn.close()


@pytest.fixture
def vault(tmp_path):
    path = tmp_path / "vault"
    init_vault(path, topics=["LLM security"])
    _add(path, "2401.00001", "Old Paper", "2026-11-01", 0.5)
    _add(path, "2401.00002", "Newer Low", "2026-11-02", 0.2)
    _add(path, "2401.00003", "Newer High", "2026-11-02", 0.9, reasons=["close to seed"])
    return path


def run(vault, *args):
    return runner.invoke(cli.app, [*args, "--vault", str(vault)])


def counts(vault):
    conn = open_db(vault / "vault.sqlite")
    try:
        return [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("votes", "saves", "skips")]
    finally:
        conn.close()


def test_show_by_id_prints_header_fields(vault):
    r = run(vault, "show", "2401.00003")
    assert r.exit_code == 0, r.output
    assert "Newer High" in r.output and "id: 2401.00003" in r.output
    assert "why: close to seed" in r.output


def test_show_without_summary_prints_abstract(vault):
    r = run(vault, "show", "Old Paper")
    assert r.exit_code == 0
    assert "Plain abstract text." in r.output


def test_show_unknown_exits_1(vault):
    r = run(vault, "show", "nothing-like-this")
    assert r.exit_code == 1
    assert "No paper in your vault matches" in r.output


def test_recent_one_shows_only_latest_date_in_digest_order(vault):
    r = run(vault, "show", "--recent", "1")
    assert r.exit_code == 0
    assert "Old Paper" not in r.output
    assert r.output.index("Newer High") < r.output.index("Newer Low")


def test_recent_two_newest_date_first(vault):
    r = run(vault, "show", "--recent", "2")
    assert r.exit_code == 0
    assert r.output.index("Newer Low") < r.output.index("Old Paper")


def test_recent_zero_rejected(vault):
    assert run(vault, "show", "--recent", "0").exit_code == 1


def test_no_args_exits_1_with_hint(vault):
    r = run(vault, "show")
    assert r.exit_code == 1
    assert "--recent" in r.output


def test_show_writes_nothing(vault):
    before = counts(vault)
    run(vault, "show", "2401.00003")
    run(vault, "show", "--recent", "2")
    assert counts(vault) == before


def test_show_prints_summary_and_critique(vault):
    conn = open_db(vault / "vault.sqlite")
    c = Claim(text="Distinctive claim text", sections=["1 Intro"])
    save_summary(conn, "2401.00003", PaperSummary(
        summary=Summary(one_line="Summary line here", problem=c, approach=c, results=c, limitations=c),
        critique=Critique(strengths=[c], weaknesses=[], questions=[]),
        basis="full_text", truncated=False, unverified=[]), "fake")
    conn.commit()
    conn.close()
    r = run(vault, "show", "2401.00003")
    assert "Summary line here" in r.output and "Distinctive claim text" in r.output


def _save(vault, pid, **kw):
    c = Claim(text="t", sections=["Abstract"])
    conn = open_db(vault / "vault.sqlite")
    save_summary(conn, pid, PaperSummary(
        summary=Summary(one_line="o", problem=c, approach=c, results=c, limitations=c),
        critique=Critique(strengths=[c], weaknesses=[], questions=[]),
        basis="full_text", unverified=[], **kw), "m")
    conn.commit()
    conn.close()


@pytest.mark.parametrize(("mode", "parts", "line"), [
    ("split", 3, "read in parts: split, 3 notes calls"),
    ("map_reduce", 5, "read in parts: map-reduce, 5 notes calls"),
])
def test_show_says_when_a_paper_was_read_in_parts(vault, mode, parts, line):
    _save(vault, "2401.00003", read_mode=mode, parts=parts)
    r = run(vault, "show", "2401.00003")
    assert r.exit_code == 0, r.output
    assert line in r.output


def test_show_prints_hook(vault):
    conn = open_db(vault / "vault.sqlite")
    c = Claim(text="Cuts memory use by 28%.", sections=["5.1 Efficiency"])
    save_summary(conn, "2401.00003", PaperSummary(
        summary=Summary(one_line="Summary line here", problem=c, approach=c, results=c, limitations=c,
                        hook=c),
        critique=Critique(strengths=[c], weaknesses=[], questions=[]),
        basis="full_text", unverified=[]), "fake")
    conn.commit()
    conn.close()
    r = run(vault, "show", "2401.00003")
    assert r.exit_code == 0, r.output
    assert "**Hook:** Cuts memory use by 28%. [§ 5.1 Efficiency]" in r.output
    assert "Summary line here" in r.output


def test_show_is_silent_about_whole_reads(vault):
    _save(vault, "2401.00003")
    r = run(vault, "show", "2401.00003")
    assert r.exit_code == 0, r.output
    assert "read in parts" not in r.output
