import pytest
from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.db import open_db, upsert_paper
from research_pipeline.library import current_votes, is_saved
from research_pipeline.models import PaperMeta
from research_pipeline.vault import init_vault

runner = CliRunner()


def _add(vault, pid, title):
    conn = open_db(vault / "vault.sqlite")
    upsert_paper(conn, PaperMeta(id=pid, title=title, abstract="a", authors=["A"], source="arxiv",
                                 url=f"https://arxiv.org/abs/{pid}"))
    conn.commit()
    conn.close()


@pytest.fixture
def vault(tmp_path):
    path = tmp_path / "vault"
    init_vault(path, topics=["LLM security"])
    _add(path, "2307.15043", "Universal Adversarial Attacks on LLMs")
    _add(path, "2401.00001", "Fuzzing eBPF Verifiers")
    _add(path, "2401.00002", "Fuzzing Kernel Drivers")
    return path


def run(vault, *args, input=None):
    return runner.invoke(cli.app, [*args, "--vault", str(vault)], input=input)


def votes(vault):
    conn = open_db(vault / "vault.sqlite")
    try:
        return [(r["paper_id"], r["vote"]) for r in current_votes(conn)]
    finally:
        conn.close()


def saved(vault, pid):
    conn = open_db(vault / "vault.sqlite")
    try:
        return is_saved(conn, pid)
    finally:
        conn.close()


def test_vote_up_records_row(vault):
    res = run(vault, "vote", "2307.15043", "up")
    assert res.exit_code == 0 and "Recorded vote up for 2307.15043." in res.output
    assert votes(vault) == [("2307.15043", 1)]


def test_vote_down_and_clear(vault):
    run(vault, "vote", "2307.15043", "down")
    assert votes(vault) == [("2307.15043", -1)]
    run(vault, "vote", "2307.15043", "clear")
    assert votes(vault) == []


def test_vote_bad_direction_fails(vault):
    res = run(vault, "vote", "2307.15043", "sideways")
    assert res.exit_code != 0
    assert votes(vault) == []


def test_vote_unknown_id_fails_and_writes_nothing(vault):
    res = run(vault, "vote", "9999.99999", "up")
    assert res.exit_code == 1
    assert votes(vault) == []


def test_vote_by_title_fragment_unique(vault):
    res = run(vault, "vote", "universal adversarial", "up")
    assert res.exit_code == 0
    assert votes(vault) == [("2307.15043", 1)]


def test_vote_ambiguous_prompts(vault):
    res = run(vault, "vote", "fuzzing", "up", input="1\n")
    assert res.exit_code == 0
    assert len(votes(vault)) == 1
    assert votes(vault)[0][0] in ("2401.00001", "2401.00002")


def test_vote_ambiguous_cancel_writes_nothing(vault):
    run(vault, "vote", "fuzzing", "up", input="0\n")
    assert votes(vault) == []


def test_save_and_unsave(vault):
    res = run(vault, "save", "2401.00001")
    assert res.exit_code == 0 and "Saved 2401.00001." in res.output
    assert saved(vault, "2401.00001")
    res = run(vault, "unsave", "2401.00001")
    assert res.exit_code == 0 and "Unsaved 2401.00001." in res.output
    assert not saved(vault, "2401.00001")


def test_save_unknown_fails(vault):
    res = run(vault, "save", "nonexistent paper")
    assert res.exit_code == 1
    assert not saved(vault, "nonexistent paper")


def test_feedback_lists(vault):
    run(vault, "vote", "2307.15043", "up")
    run(vault, "vote", "2401.00001", "down")
    run(vault, "save", "2401.00002")
    res = run(vault, "feedback")
    assert res.exit_code == 0
    out = res.output
    assert "2307.15043" in out and "Universal Adversarial" in out
    assert "2401.00001" in out and "Fuzzing eBPF" in out
    assert "2401.00002" in out and "Fuzzing Kernel Drivers" in out


def test_feedback_empty(vault):
    res = run(vault, "feedback")
    assert res.exit_code == 0
    assert "No votes" in res.output


@pytest.mark.parametrize("cmd", ["vote", "save"])
def test_unknown_paper_id_exits_1_with_message(vault, cmd):
    args = [cmd, "9999.99999"] + (["up"] if cmd == "vote" else []) + ["--vault", str(vault)]
    res = runner.invoke(cli.app, args)
    assert res.exit_code == 1
    assert "No paper in your vault matches '9999.99999'" in res.output


def _make_too_new(vault):
    import sqlite3

    from research_pipeline.db import SCHEMA_VERSION

    raw = sqlite3.connect(vault / "vault.sqlite")
    raw.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    raw.commit()
    raw.close()


def test_open_exits_cleanly_on_too_new_database(vault):
    _make_too_new(vault)
    res = runner.invoke(cli.app, ["feedback", "--vault", str(vault)])
    assert res.exit_code == 1 and "upgrade rpipe" in res.output
    assert "Traceback" not in res.output


def test_init_exits_cleanly_on_too_new_database(vault):
    _make_too_new(vault)
    res = runner.invoke(cli.app, ["init", "--vault", str(vault)])
    assert res.exit_code == 1 and "upgrade rpipe" in res.output
    assert isinstance(res.exception, SystemExit) and res.exception.code == 1
