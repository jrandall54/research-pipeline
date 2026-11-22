from datetime import date

from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.db import finish_run, open_db, start_run
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
    from research_pipeline.db import previous_run_unfinished

    conn = open_db(tmp_path / "v.sqlite")
    assert previous_run_unfinished(conn, start_run(conn)) is False  # no earlier run
    crashed = start_run(conn)
    current = start_run(conn)
    assert previous_run_unfinished(conn, current) is True
    finish_run(conn, crashed, {}, [])
    assert previous_run_unfinished(conn, current) is False
