from datetime import date

import pytest
from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.db import (
    current_votes,
    mark_in_digest,
    open_db,
    record_skip,
    record_vote,
    save_summary,
    set_status,
    upsert_paper,
)
from research_pipeline.models import PaperMeta, Status
from research_pipeline.summarize import Claim, Critique, PaperSummary, Summary
from research_pipeline.vault import init_vault

runner = CliRunner()

NOT_TTY = "rpipe review needs a terminal. Use `rpipe vote ID up|down` instead."


def _add(vault, pid, title, day, score, abstract="An abstract about " + "x", reasons=None):
    conn = open_db(vault / "vault.sqlite")
    upsert_paper(conn, PaperMeta(id=pid, title=title, abstract=abstract, authors=["A"],
                                 source="arxiv", url=f"https://arxiv.org/abs/{pid}"))
    conn.execute("UPDATE papers SET triage_score = ? WHERE id = ?", (score, pid))
    if reasons is not None:
        set_status(conn, pid, Status.triaged, score=score, reasons=reasons)
    conn.commit()
    mark_in_digest(conn, [pid], date.fromisoformat(day))
    conn.close()


@pytest.fixture
def vault(tmp_path):
    path = tmp_path / "vault"
    init_vault(path, topics=["LLM security"])
    _add(path, "2401.00001", "Old Paper", "2026-11-01", 0.5, reasons=["close to seed", "followed author"])
    _add(path, "2401.00002", "Old Second", "2026-11-01", 0.2)
    _add(path, "2401.00003", "New Paper", "2026-11-02", 0.9)
    return path


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(cli, "_is_tty", lambda: True)


def run(vault, *args, input=None):
    return runner.invoke(cli.app, [*args, "--vault", str(vault)], input=input)


def db(vault):
    return open_db(vault / "vault.sqlite")


def votes(vault):
    conn = db(vault)
    try:
        return [(r["paper_id"], r["vote"]) for r in current_votes(conn)]
    finally:
        conn.close()


def skips(vault):
    conn = db(vault)
    try:
        return [r["paper_id"] for r in conn.execute("SELECT paper_id FROM skips ORDER BY skipped_at, paper_id")]
    finally:
        conn.close()


def test_review_votes_and_skips_in_order(vault, tty):
    res = run(vault, "review", input="u\nd\ns\n")
    assert res.exit_code == 0, res.output
    assert votes(vault) == [("2401.00001", 1), ("2401.00002", -1)]
    assert skips(vault) == ["2401.00003"]
    out = res.output
    assert out.index("Old Paper") < out.index("Old Second") < out.index("New Paper")
    assert "Digest 2026-11-01 · paper 1 of 2 · 0 voted, 0 skipped" in out
    assert "Digest 2026-11-01 · paper 2 of 2 · 1 voted, 0 skipped" in out
    assert "Digest 2026-11-02 · paper 1 of 1 · 2 voted, 0 skipped" in out
    assert "why: close to seed; followed author" in out
    assert "id: 2401.00001" in out and "https://arxiv.org/abs/2401.00001" in out
    assert "Voted 2, skipped 1 this session. 0 left in the queue." in out


def test_review_quit_leaves_rest(vault, tty):
    res = run(vault, "review", input="q\n")
    assert res.exit_code == 0
    assert votes(vault) == [] and skips(vault) == []
    assert "Voted 0, skipped 0 this session. 3 left in the queue." in res.output


def test_review_invalid_key_reprompts(vault, tty):
    res = run(vault, "review", input="x\nu\nq\n")
    assert res.output.count("Please answer u, d, s or q.") == 1
    assert votes(vault) == [("2401.00001", 1)]


def test_review_uppercase_and_first_letter(vault, tty):
    run(vault, "review", input="UP\nDown\nq\n")
    assert votes(vault) == [("2401.00001", 1), ("2401.00002", -1)]


def test_review_requires_terminal(vault):
    res = run(vault, "review")
    assert res.exit_code == 1
    assert NOT_TTY in res.output
    assert votes(vault) == []


def test_review_empty_queue(tmp_path, tty):
    path = tmp_path / "v"
    init_vault(path, topics=["x"])
    res = run(path, "review")
    assert res.exit_code == 0 and "Nothing to review." in res.output
    res = run(path, "review", "--later")
    assert res.exit_code == 0 and "Nothing waiting in review later." in res.output


def test_review_shows_summary_else_abstract(vault, tty):
    conn = db(vault)
    c = Claim(text="Distinctive claim text", sections=["1 Intro"])
    save_summary(conn, "2401.00001", PaperSummary(
        summary=Summary(one_line="Summary line here", problem=c, approach=c, results=c, limitations=c),
        critique=Critique(strengths=[c], weaknesses=[], questions=[]),
        basis="full_text", truncated=False, unverified=[]), "fake")
    conn.execute("UPDATE papers SET abstract = '' WHERE id = '2401.00002'")
    conn.commit()
    conn.close()
    res = run(vault, "review", input="q\n")
    assert "Summary line here" in res.output and "Distinctive claim text" in res.output
    assert "An abstract about x" not in res.output  # paper 1 shows the summary, not the abstract
    res = run(vault, "review", input="u\nq\n")  # second paper: empty abstract, no summary
    assert "No abstract available." in res.output
    res = run(vault, "review", input="u\nu\nq\n")  # third paper: abstract shown
    assert "An abstract about x" in res.output


def test_review_later_votes_remove_skip(vault, tty):
    conn = db(vault)
    record_skip(conn, "2401.00001", now="2026-11-05T00:00:00+00:00")
    record_skip(conn, "2401.00002", now="2026-11-06T00:00:00+00:00")
    conn.close()
    res = run(vault, "review", "--later", input="u\ns\n")
    assert res.exit_code == 0, res.output
    assert "Review later · 1 of 2 · 0 voted, 0 skipped" in res.output
    assert "Review later · 2 of 2 · 1 voted, 0 skipped" in res.output
    assert votes(vault) == [("2401.00001", 1)]
    assert skips(vault) == ["2401.00002"]
    assert "Voted 1, skipped 1 this session. 1 left in the queue." in res.output


def test_review_later_skip_again_moves_to_back(vault, tty):
    conn = db(vault)
    record_skip(conn, "2401.00001", now="2026-11-05T00:00:00+00:00")
    record_skip(conn, "2401.00002", now="2026-11-06T00:00:00+00:00")
    conn.close()
    res = run(vault, "review", "--later", input="s\nq\n")
    assert res.exit_code == 0
    assert skips(vault) == ["2401.00002", "2401.00001"]


def test_review_cleared_vote_requeues(vault, tty):
    conn = db(vault)
    record_vote(conn, "2401.00001", 1)
    record_vote(conn, "2401.00001", 0)
    conn.close()
    res = run(vault, "review", input="q\n")
    assert "Old Paper" in res.output  # cleared vote is back in the queue


def test_review_later_vote_deletes_skip_row(vault, tty):
    conn = db(vault)
    record_skip(conn, "2401.00001")
    conn.close()
    assert skips(vault) == ["2401.00001"]
    res = run(vault, "review", "--later", input="d\n")
    assert res.exit_code == 0, res.output
    assert votes(vault) == [("2401.00001", -1)]
    assert skips(vault) == []


def test_review_eof_keeps_votes_and_prints_tally(vault, tty):
    res = run(vault, "review", input="u\n")  # input ends at the second prompt
    assert res.exit_code == 1
    assert votes(vault) == [("2401.00001", 1)]
    assert "Voted 1, skipped 0 this session. 2 left in the queue." in res.output


def test_feedback_shows_review_later_count(vault):
    res = run(vault, "feedback")
    assert "Review later" in res.output and "  Nothing waiting." in res.output
    conn = db(vault)
    record_skip(conn, "2401.00001")
    record_skip(conn, "2401.00002")
    conn.close()
    res = run(vault, "feedback")
    assert "  2 papers waiting. Run `rpipe review --later`." in res.output


def test_feedback_review_later_singular(vault):
    conn = db(vault)
    record_skip(conn, "2401.00001")
    conn.close()
    res = run(vault, "feedback")
    assert "  1 paper waiting. Run `rpipe review --later`." in res.output
