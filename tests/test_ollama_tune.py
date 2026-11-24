"""Guided Ollama settings: advice, drop-in text, and the consent / verify / undo flow. Fully offline."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from research_pipeline import cli, ollama_tune, registry
from research_pipeline.scan import OllamaServer
from research_pipeline.vault import init_vault

runner = CliRunner()
GOOD = {"OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0", "OLLAMA_NUM_PARALLEL": "1"}


def server(env=None, how="systemd", host_matches=True, drop_ins=()):
    return OllamaServer("0.9", how, dict(env or {}), list(drop_ins), host_matches)


def test_advise_skips_settings_already_set():
    assert ollama_tune.advise(server(GOOD)) == []


def test_advise_parallel_only_when_above_one():
    names = [s.name for s in ollama_tune.advise(server({"OLLAMA_NUM_PARALLEL": "4"}))]
    assert "OLLAMA_NUM_PARALLEL" in names
    assert "OLLAMA_NUM_PARALLEL" not in [s.name for s in ollama_tune.advise(server({}))]


def test_render_dropin_quotes_values_and_names_undo():
    text = ollama_tune.render_dropin(ollama_tune.advise(server({})))
    assert text.startswith("# Written by rpipe")
    assert "rpipe model tune --undo" in text
    assert "[Service]\n" in text
    assert 'Environment="OLLAMA_FLASH_ATTENTION=1"' in text
    assert 'Environment="OLLAMA_KV_CACHE_TYPE=q8_0"' in text


def test_commands_are_argv_lists(tmp_path, monkeypatch):
    drop = tmp_path / "rpipe.conf"
    monkeypatch.setattr(ollama_tune, "DROPIN", drop)
    cmds = ollama_tune.apply_commands(Path("/x/t.conf"))
    assert cmds[0] == ["sudo", "install", "-m", "644", "/x/t.conf", str(drop)]
    assert cmds[1:] == [["sudo", "systemctl", "daemon-reload"], ["sudo", "systemctl", "restart", "ollama"]]
    assert ollama_tune.undo_commands()[0] == ["sudo", "rm", "-f", str(drop)]


def test_wait_ready_polls_then_gives_up():
    answers = iter([None, None, "0.9"])
    sleeps = []
    assert ollama_tune.wait_ready("u", 5, lambda u: next(answers), sleeps.append) is True
    assert len(sleeps) == 2
    assert ollama_tune.wait_ready("u", 3, lambda u: None, lambda s: None) is False


def test_manual_instructions_for_hand_started_server():
    text = ollama_tune.manual_instructions(
        server({}, how="manual"), ollama_tune.advise(server({})))
    assert "OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve" in text


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "_entry_points", lambda group: [])
    vault = tmp_path / "vault"
    init_vault(vault, topics=["LLM security"])
    from test_cli_model import set_config
    set_config(vault, llm_backend="none")
    drop = tmp_path / "rpipe.conf"
    monkeypatch.setattr(ollama_tune, "DROPIN", drop)
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    calls = []
    monkeypatch.setattr(ollama_tune, "run_tty", lambda argv: calls.append(argv) or 0)
    monkeypatch.setattr(ollama_tune, "wait_ready", lambda url, *a, **k: True)
    return SimpleNamespace(vault=vault, drop=drop, calls=calls, mp=monkeypatch)


def tune(env, *args, input=None):
    return runner.invoke(cli.app, ["model", "tune", *args, "--vault", str(env.vault)], input=input)


def test_tune_already_set_writes_nothing(env):
    env.mp.setattr(cli, "_scan_server", lambda url: server(GOOD))
    result = tune(env)
    assert result.exit_code == 0
    assert "already has the recommended settings" in result.output
    assert env.calls == []


def test_tune_applies_with_consent(env):
    scans = iter([server({}), server(GOOD)])
    env.mp.setattr(cli, "_scan_server", lambda url: next(scans))
    result = tune(env, input="y\n")
    assert result.exit_code == 0, result.output
    assert "live" in result.output
    assert [c[1:2] for c in env.calls] == [["install"], ["systemctl"], ["systemctl"]]
    assert env.calls[0][:4] == ["sudo", "install", "-m", "644"]
    assert env.calls[0][5] == str(env.drop)
    assert not Path(env.calls[0][4]).exists()  # temp file removed


def test_tune_reports_setting_not_live(env):
    scans = iter([server({}), server({})])
    env.mp.setattr(cli, "_scan_server", lambda url: next(scans))
    result = tune(env, input="y\n")
    assert result.exit_code != 0
    assert "not live" in result.output


def test_tune_declined_runs_nothing(env):
    env.mp.setattr(cli, "_scan_server", lambda url: server({}))
    result = tune(env, input="n\n")
    assert result.exit_code == 0
    assert env.calls == []
    assert "Nothing was changed" in result.output


def test_tune_stops_at_failed_command(env):
    seen = []

    def fail_second(argv):
        seen.append(argv)
        return 1 if len(seen) == 2 else 0

    env.mp.setattr(ollama_tune, "run_tty", fail_second)
    env.mp.setattr(cli, "_scan_server", lambda url: server({}))
    result = tune(env, input="y\n")
    assert result.exit_code == 1
    assert len(seen) == 2
    assert "Failed: sudo systemctl daemon-reload" in result.output


def test_tune_warns_on_host_mismatch(env):
    env.mp.setattr(cli, "_scan_server", lambda url: server({"OLLAMA_HOST": "127.0.0.1:11436"}, host_matches=False))
    result = tune(env, input="\n")
    assert "Warning" in result.output
    assert env.calls == []


def test_tune_non_systemd_prints_instructions(env):
    env.mp.setattr(cli, "_scan_server", lambda url: server({}, how="manual"))
    result = tune(env)
    assert result.exit_code == 0
    assert "OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve" in result.output
    assert env.calls == []


def test_undo_with_nothing_to_undo(env):
    result = tune(env, "--undo")
    assert result.exit_code == 0
    assert "Nothing to undo" in result.output
    assert env.calls == []


def test_undo_removes_only_rpipe_conf(env):
    env.drop.write_text("x")
    env.mp.setattr(cli, "_scan_server", lambda url: server(GOOD))
    result = tune(env, "--undo", input="y\n")
    assert result.exit_code == 0, result.output
    assert env.calls[0] == ["sudo", "rm", "-f", str(env.drop)]
    assert all("kv-cache.conf" not in " ".join(c) for c in env.calls)


def test_tune_refuses_without_terminal(env):
    env.mp.setattr(cli, "_is_tty", lambda: False)
    result = tune(env)
    assert result.exit_code != 0
    assert env.calls == []
