import json
import sqlite3

import pytest
from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.run_lock import RunLocked, run_lock
from research_pipeline.vault import init_vault

runner = CliRunner()


def test_second_acquisition_fails_at_once_and_release_frees_it(tmp_path):
    with run_lock(tmp_path):
        with pytest.raises(RunLocked, match="already"):
            with run_lock(tmp_path):
                pass
    with run_lock(tmp_path):  # released on exit, so a later run can start
        pass


def test_lock_is_released_when_the_run_raises(tmp_path):
    with pytest.raises(ValueError):
        with run_lock(tmp_path):
            raise ValueError("crash")
    with run_lock(tmp_path):
        pass


def test_run_exits_75_with_a_clear_message_when_locked(tmp_path):
    vault = tmp_path / "v"
    init_vault(vault, topics=["x"])
    with run_lock(vault):
        res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code == cli.RUN_LOCKED_EXIT == 75
    assert "already using" in res.output and "nothing was started" in res.output
    # nothing was started: no runs row was written
    conn = sqlite3.connect(vault / "vault.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_failing_finish_run_does_not_mask_the_original_error(tmp_path, monkeypatch):
    vault = tmp_path / "v"
    init_vault(vault, topics=["x"])

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk full")

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "finish_run", boom)
    monkeypatch.setattr(cli, "_load_embedder", interrupted)
    res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code != 0  # the original interrupt wins; the secondary failure is only logged
    assert not isinstance(res.exception, sqlite3.OperationalError)
    assert "could not record the run: disk full" in res.output


def test_failing_finish_run_alone_still_surfaces(tmp_path, monkeypatch):
    vault = tmp_path / "v"
    init_vault(vault, topics=["x"])
    monkeypatch.setattr(cli, "finish_run", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("disk full")))
    monkeypatch.setattr(cli, "_load_embedder", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    # a crash in a stage is recorded, finish_run then fails with nothing else in flight... but the stage
    # crash was swallowed into `errors`, so finish_run's failure is the only error left and must surface
    assert isinstance(res.exception, sqlite3.OperationalError)
