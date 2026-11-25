from datetime import date

import pytest
from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.db import finish_run, open_db, previous_run_unfinished, start_run
from research_pipeline.run_lock import run_lock
from research_pipeline.vault import init_vault

runner = CliRunner()


def test_runs_command_lists_newest_first(tmp_path):
    init_vault(tmp_path / "v", topics=["x"])
    conn = open_db(tmp_path / "v" / "vault.sqlite")
    first, second = start_run(conn), start_run(conn)
    finish_run(conn, first, {"inserted": 1}, [])
    finish_run(conn, second, {"inserted": 2}, ["boom"])
    conn.close()
    res = runner.invoke(cli.app, ["runs", "--vault", str(tmp_path / "v"), "--limit", "5"])
    assert res.exit_code == 0, res.output
    lines = res.output.strip().splitlines()
    assert len(lines) == 2
    assert "new 2" in lines[0] and "errors 1" in lines[0]
    assert "new 1" in lines[1] and "errors 0" in lines[1]


def test_health_line_flags_unfinished_previous_run():
    base = cli.health_line(date(2026, 11, 21), {}, [])
    assert "previous run did not finish" not in base
    flagged = cli.health_line(date(2026, 11, 21), {}, [], previous_unfinished=True)
    assert flagged.endswith("previous run did not finish")


def test_previous_run_unfinished_ignores_the_current_run(tmp_path):
    conn = open_db(tmp_path / "v.sqlite")
    assert previous_run_unfinished(conn, start_run(conn)) is False  # no earlier run
    crashed = start_run(conn)
    current = start_run(conn)
    assert previous_run_unfinished(conn, current) is True
    finish_run(conn, crashed, {}, [])
    assert previous_run_unfinished(conn, current) is False


def test_runs_shows_unfinished_run_as_did_not_finish(tmp_path):
    init_vault(tmp_path / "v", topics=["x"])
    conn = open_db(tmp_path / "v" / "vault.sqlite")
    start_run(conn)
    conn.close()
    res = runner.invoke(cli.app, ["runs", "--vault", str(tmp_path / "v")])
    assert res.exit_code == 0, res.output
    assert "did not finish" in res.output and "no counts" in res.output


def test_runs_with_no_runs_says_so(tmp_path):
    init_vault(tmp_path / "v", topics=["x"])
    res = runner.invoke(cli.app, ["runs", "--vault", str(tmp_path / "v")])
    assert res.exit_code == 0 and "No runs yet" in res.output


def test_runs_rejects_zero_limit(tmp_path):
    init_vault(tmp_path / "v", topics=["x"])
    res = runner.invoke(cli.app, ["runs", "--vault", str(tmp_path / "v"), "--limit", "0"])
    assert res.exit_code == 2


def _count(vault, table):
    conn = open_db(vault / "vault.sqlite")
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


# Every command that does pipeline work, touches the model or GPU, restarts Ollama, or fetches
# into the vault (the "locked" list in docs/guide/running.md), with the fewest arguments it needs.
LOCKED_COMMANDS = [
    ["run"],
    ["collect"],
    ["triage"],
    ["summarize"],
    ["summarize", "--backlog"],
    ["summarize", "2307.15043", "--refetch"],
    ["digest"],
    ["eval", "run"],
    ["model", "fit"],
    ["model", "check"],
    ["model", "bench"],
    ["model", "tune"],
    ["model", "tune", "--undo"],
    ["model", "recommend"],
    ["model", "setup"],
    ["profile", "suggest", "I study fuzzing."],
    ["gold", "add", "2307.15043"],
    ["gold", "suggest"],
    ["gold", "auto"],
    ["gold", "draft", "2307.15043"],
    ["shelf", "sync"],
    ["shelf", "add", "NOTE"],
]


@pytest.mark.parametrize("args", LOCKED_COMMANDS, ids=" ".join)
def test_locked_commands_stop(tmp_path, monkeypatch, args):
    vault = tmp_path / "v"
    init_vault(vault, topics=["x"])
    note = tmp_path / "idea.md"
    note.write_text("an idea about fuzzing", encoding="utf-8")
    args = [str(note) if a == "NOTE" else a for a in args]

    def forbidden(*a, **k):
        raise AssertionError("reached network, model, embedder or Ollama before the lock")

    # Anything that would leave the process: a missed lock fails loudly instead of exiting 75.
    for target, name in [
        (cli, "build_pipeline"), (cli, "build_llm"), (cli, "make_arxiv"), (cli, "_load_embedder"),
        (cli, "detect_machine"), (cli, "_scan_server"), (cli.ollama_fit, "resolve"),
        (cli.ollama_fit, "cached_fit"), (cli.bench_mod, "run_bench"), (cli.judge, "make_judge"),
        (cli.ollama_tune, "run_tty"), (cli.ollama_tune, "wait_ready"),
    ]:
        monkeypatch.setattr(target, name, forbidden)
    monkeypatch.setattr(cli, "_is_tty", lambda: True)  # tune and setup refuse to run otherwise
    dropin = tmp_path / "rpipe.conf"
    dropin.write_text("[Service]\n", encoding="utf-8")
    monkeypatch.setattr(cli.ollama_tune, "DROPIN", dropin)  # so `tune --undo` would go on to restart

    papers, runs = _count(vault, "papers"), _count(vault, "runs")
    with run_lock(vault, "rpipe run"):
        result = runner.invoke(cli.app, [*args, "--vault", str(vault)], input="y\n" * 5)
    assert result.exit_code == cli.RUN_LOCKED_EXIT == 75, result.output
    assert "rpipe run is already working" in result.stderr
    assert (_count(vault, "papers"), _count(vault, "runs")) == (papers, runs)
    assert not list((vault / "digests").glob("*.md"))  # `digest` wrote nothing
