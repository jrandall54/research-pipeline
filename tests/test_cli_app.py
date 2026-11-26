import subprocess
import sys
import types

import pytest
from typer.testing import CliRunner

from research_pipeline import cli, netguard, schedule
from research_pipeline.vault import init_vault
from research_pipeline.webapp import service

runner = CliRunner()


@pytest.fixture
def fake(tmp_path, monkeypatch):
    calls = []
    unit_dir = tmp_path / "units"
    monkeypatch.setattr(schedule, "default_unit_dir", lambda: unit_dir)
    monkeypatch.setattr(schedule, "system_runner", lambda c: calls.append(c) or 0)
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli, "_platform", lambda: "Linux")
    monkeypatch.setattr(cli, "_app_available", lambda: True)
    monkeypatch.setattr(cli, "_embed_available", lambda: True)
    monkeypatch.setattr(netguard, "system_runner",
                        lambda argv: subprocess.CompletedProcess(argv, 0, "100.64.0.5\n", ""))
    init_vault(tmp_path / "v", topics=["x"])
    return calls, unit_dir, tmp_path / "v"


def test_app_run_without_extra_explains_install(fake, monkeypatch):
    _, _, vault = fake
    monkeypatch.setattr(cli, "_app_available", lambda: False)
    res = runner.invoke(cli.app, ["app", "run", "--host", "127.0.0.1", "--vault", str(vault)])
    assert res.exit_code == 1
    assert "The app is not installed. Install it with: uv sync --extra embed --extra serve --extra app" in res.output
    assert "uv tool install --editable '.[embed,serve,app]' --force" in res.output


def test_app_run_starts_uvicorn(fake, monkeypatch):
    _, _, vault = fake
    # A stand-in uvicorn: the offline suite (and CI) runs without the `app` extra.
    got = {}
    fake_uvicorn = types.ModuleType("uvicorn")
    fake_uvicorn.run = lambda application, **kw: got.update(app=application, **kw)
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(cli, "_app_available", lambda: True)
    res = runner.invoke(cli.app, ["app", "run", "--host", "127.0.0.1", "--port", "8123", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert "Your feed is at http://127.0.0.1:8123/ (only your own devices can reach it)" in res.output
    assert got["host"] == "127.0.0.1" and got["port"] == 8123 and got["log_level"] == "warning"
    assert got["app"].state.vault_db == vault / "vault.sqlite"
    assert got["app"].state.allowed_hosts == service.allowed_hosts("127.0.0.1")
    assert got["app"].state.address == "http://127.0.0.1:8123/"
    assert "Open exactly this address" in res.output


@pytest.mark.parametrize("cmd", ["run", "install"])
@pytest.mark.parametrize("port", ["0", "70000"])
def test_app_port_out_of_range_refused_before_anything_is_written(fake, cmd, port):
    calls, unit_dir, vault = fake
    res = runner.invoke(cli.app, ["app", cmd, "--host", "127.0.0.1", "--port", port, "--vault", str(vault)])
    assert res.exit_code == 2 and "port" in res.output.lower()
    assert calls == [] and not unit_dir.exists()


def test_app_run_refuses_public_host(fake):
    _, _, vault = fake
    res = runner.invoke(cli.app, ["app", "run", "--host", "0.0.0.0", "--vault", str(vault)])
    assert res.exit_code == 1 and "Refusing to listen" in res.output


def test_app_install_refuses_public_host(fake):
    calls, unit_dir, vault = fake
    res = runner.invoke(cli.app, ["app", "install", "--host", "0.0.0.0", "--vault", str(vault)])
    assert res.exit_code == 1 and "Refusing to listen" in res.output
    assert calls == [] and not unit_dir.exists()


def test_app_install_refuses_without_extra(fake, monkeypatch):
    calls, unit_dir, vault = fake
    monkeypatch.setattr(cli, "_app_available", lambda: False)
    res = runner.invoke(cli.app, ["app", "install", "--vault", str(vault)])
    assert res.exit_code == 1 and "The app is not installed" in res.output
    assert calls == [] and not unit_dir.exists()


def test_app_install_writes_unit_and_prints_address(fake):
    calls, unit_dir, vault = fake
    res = runner.invoke(cli.app, ["app", "install", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert (unit_dir / service.SERVICE_NAME).exists()
    assert "--host 100.64.0.5 --port 8002" in (unit_dir / service.SERVICE_NAME).read_text()
    assert "http://100.64.0.5:8002/" in res.output
    assert "Add to Home Screen" in res.output
    assert ["systemctl", "--user", "enable", "--now", service.SERVICE_NAME] in calls
    assert calls[-1] == ["systemctl", "--user", "restart", service.SERVICE_NAME]


def test_app_install_tailscale_down_refuses(fake, monkeypatch):
    calls, unit_dir, vault = fake
    monkeypatch.setattr(netguard, "system_runner", lambda argv: subprocess.CompletedProcess(argv, 1, "", ""))
    res = runner.invoke(cli.app, ["app", "install", "--vault", str(vault)])
    assert res.exit_code == 1 and "Tailscale not running" in res.output
    assert not unit_dir.exists()


def test_app_install_not_linux(fake, monkeypatch):
    monkeypatch.setattr(cli, "_platform", lambda: "Darwin")
    res = runner.invoke(cli.app, ["app", "install", "--host", "127.0.0.1"])
    assert res.exit_code == 1 and "Linux" in res.output


def test_app_status_three_states(fake, monkeypatch):
    calls, unit_dir, vault = fake
    res = runner.invoke(cli.app, ["app", "status"])
    assert res.exit_code == 0 and "Not installed. Set it up with: rpipe app install" in res.output
    runner.invoke(cli.app, ["app", "install", "--vault", str(vault)])
    res = runner.invoke(cli.app, ["app", "status"])
    assert "Running at http://100.64.0.5:8002/" in res.output
    monkeypatch.setattr(schedule, "system_runner", lambda c: 3 if "is-active" in c else 0)
    res = runner.invoke(cli.app, ["app", "status"])
    assert "Installed but not running. Check: journalctl --user -u research-pipeline-app" in res.output


def test_app_uninstall(fake):
    calls, unit_dir, vault = fake
    res = runner.invoke(cli.app, ["app", "uninstall"])
    assert res.exit_code == 0 and "nothing was installed" in res.output
    runner.invoke(cli.app, ["app", "install", "--vault", str(vault)])
    res = runner.invoke(cli.app, ["app", "uninstall"])
    assert res.exit_code == 0 and "removed" in res.output
    assert not (unit_dir / service.SERVICE_NAME).exists()
