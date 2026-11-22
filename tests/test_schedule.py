import pytest
from typer.testing import CliRunner

from research_pipeline import cli, schedule
from research_pipeline.vault import init_vault

runner = CliRunner()


def test_timer_has_persistent_and_0300_default():
    text = schedule.render_timer()
    assert "OnCalendar=*-*-* 03:00:00" in text
    assert "Persistent=true" in text
    assert "WantedBy=timers.target" in text
    assert "OnCalendar=*-*-* 07:05:00" in schedule.render_timer("07:05")


@pytest.mark.parametrize("bad", ["25:00", "3am", "3:00", "12:60", ""])
def test_bad_time_rejected(bad):
    with pytest.raises(ValueError):
        schedule.render_timer(bad)


def test_service_sets_vault_env(tmp_path):
    text = schedule.render_service(tmp_path / "my vault", "/usr/bin/rpipe")
    assert "Type=oneshot" in text
    assert f'Environment="RESEARCH_VAULT={tmp_path}/my vault"' in text
    assert 'ExecStart="/usr/bin/rpipe" run' in text


def test_service_quotes_special_characters(tmp_path):
    text = schedule.render_service(tmp_path / 'a"b%c', "/opt/r pipe/$x/rpipe")
    assert 'a\\"b%%c' in text
    assert 'ExecStart="/opt/r pipe/$$x/rpipe" run' in text


def test_install_writes_files_and_calls_systemctl_in_order(tmp_path):
    calls = []
    unit_dir = tmp_path / "units"
    paths = schedule.install(tmp_path / "v", "04:30", unit_dir, lambda c: calls.append(c) or 0, "/bin/rpipe")
    assert [p.name for p in paths] == ["research-pipeline.service", "research-pipeline.timer"]
    assert all(p.exists() for p in paths)
    assert "04:30:00" in paths[1].read_text()
    assert calls == [["systemctl", "--user", "daemon-reload"],
                     ["systemctl", "--user", "enable", "--now", "research-pipeline.timer"]]


def test_install_failure_raises_schedule_error(tmp_path):
    with pytest.raises(schedule.ScheduleError, match="left in place"):
        schedule.install(tmp_path / "v", "03:00", tmp_path / "u", lambda c: 1, "/bin/rpipe")
    assert (tmp_path / "u" / "research-pipeline.timer").exists()


def test_remove_deletes_files(tmp_path):
    calls = []
    schedule.install(tmp_path / "v", "03:00", tmp_path / "u", lambda c: 0, "/bin/rpipe")
    removed = schedule.remove(tmp_path / "u", lambda c: calls.append(c) or 0)
    assert len(removed) == 2 and not list((tmp_path / "u").iterdir())
    assert calls[0][:3] == ["systemctl", "--user", "disable"]


@pytest.fixture
def fake(tmp_path, monkeypatch):
    calls = []
    unit_dir = tmp_path / "units"
    monkeypatch.setattr(schedule, "default_unit_dir", lambda: unit_dir)
    monkeypatch.setattr(schedule, "system_runner", lambda c: calls.append(c) or 0)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/rpipe")
    init_vault(tmp_path / "v", topics=["x"])
    return calls, unit_dir, tmp_path / "v"


def test_cli_show_prints_without_writing(fake):
    calls, unit_dir, vault = fake
    res = runner.invoke(cli.app, ["schedule", "show", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert "OnCalendar" in res.output and "ExecStart" in res.output
    assert not unit_dir.exists() or not list(unit_dir.iterdir())
    assert calls == []


def test_cli_install_prints_linger_hint_and_never_runs_it(fake):
    calls, unit_dir, vault = fake
    res = runner.invoke(cli.app, ["schedule", "install", "--time", "02:15", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert "loginctl enable-linger $USER" in res.output
    assert (unit_dir / "research-pipeline.timer").exists()
    assert all(c[0] == "systemctl" for c in calls)


def test_cli_install_bad_time_and_missing_rpipe(fake, monkeypatch):
    calls, unit_dir, vault = fake
    res = runner.invoke(cli.app, ["schedule", "install", "--time", "3am", "--vault", str(vault)])
    assert res.exit_code == 1 and "HH:MM" in res.output
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    res = runner.invoke(cli.app, ["schedule", "install", "--vault", str(vault)])
    assert res.exit_code == 1 and "not on PATH" in res.output
    assert calls == []


def test_cli_remove_and_status(fake):
    calls, unit_dir, vault = fake
    runner.invoke(cli.app, ["schedule", "install", "--vault", str(vault)])
    assert runner.invoke(cli.app, ["schedule", "status"]).exit_code == 0
    assert runner.invoke(cli.app, ["schedule", "remove"]).exit_code == 0
    assert not list(unit_dir.iterdir())
