import sqlite3

import pytest
from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.run_lock import LOCK_NAME, RunLocked, run_lock
from research_pipeline.vault import init_vault

runner = CliRunner()


def test_second_acquisition_fails_at_once_and_release_frees_it(tmp_path):
    with run_lock(tmp_path, "rpipe run"):
        with pytest.raises(RunLocked, match="already"):
            with run_lock(tmp_path, "rpipe run"):
                pass
    with run_lock(tmp_path, "rpipe run"):  # released on exit, so a later run can start
        pass


def test_lock_is_released_when_the_run_raises(tmp_path):
    with pytest.raises(ValueError):
        with run_lock(tmp_path, "rpipe run"):
            raise ValueError("crash")
    with run_lock(tmp_path, "rpipe run"):
        pass


def test_lock_records_holder(tmp_path):
    with run_lock(tmp_path, "rpipe run"):
        with pytest.raises(RunLocked) as exc:
            with run_lock(tmp_path, "rpipe summarize"):
                pass
    assert exc.value.holder == "rpipe run"
    assert "rpipe run" in str(exc.value)


def test_lock_released_holder_cleared(tmp_path):
    with run_lock(tmp_path, "rpipe run"):
        assert (tmp_path / LOCK_NAME).read_text() == "rpipe run"
    assert (tmp_path / LOCK_NAME).read_text() == ""
    with run_lock(tmp_path, "rpipe summarize"):  # no raise
        assert (tmp_path / LOCK_NAME).read_text() == "rpipe summarize"


def test_lock_holder_cleared_after_exception(tmp_path):
    with pytest.raises(ValueError):
        with run_lock(tmp_path, "rpipe run"):
            raise ValueError("crash")
    assert (tmp_path / LOCK_NAME).read_text() == ""
    with run_lock(tmp_path, "rpipe collect"):
        pass


def test_empty_lock_file_names_an_unknown_holder(tmp_path):
    """A lock held by an older rpipe (which wrote nothing) still gives a readable message."""
    import fcntl

    with open(tmp_path / LOCK_NAME, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RunLocked) as exc:
            with run_lock(tmp_path, "rpipe run"):
                pass
    assert exc.value.holder == "unknown"


def test_run_exits_75_with_a_clear_message_when_locked(tmp_path):
    vault = tmp_path / "v"
    init_vault(vault, topics=["x"])
    with run_lock(vault, "rpipe summarize"):
        res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code == cli.RUN_LOCKED_EXIT == 75
    assert (
        f"rpipe summarize is already working on {vault}; nothing was started."
        " Wait for it to finish (see `rpipe runs`)."
    ) in " ".join(res.stderr.split())
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
