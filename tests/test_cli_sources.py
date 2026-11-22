from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from research_pipeline import cli, registry
from research_pipeline.vault import init_vault, load_config

runner = CliRunner()


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "_entry_points", lambda group: [])
    path = tmp_path / "vault"
    init_vault(path, topics=["LLM security"])
    return path


def run(vault, *args):
    return runner.invoke(cli.app, [*args, "--vault", str(vault)])


def test_cli_sources_list_shows_on_off(vault):
    assert run(vault, "sources", "disable", "hf_daily").exit_code == 0
    res = run(vault, "sources", "list")
    assert res.exit_code == 0, res.output
    lines = {l.split()[0]: l for l in res.output.splitlines() if l.strip()}
    assert " on " in lines["arxiv"] and "built-in" in lines["arxiv"]
    assert " off " in lines["hf_daily"]


def test_cli_sources_enable_and_unknown(vault):
    run(vault, "sources", "disable", "hf_daily")
    assert run(vault, "sources", "enable", "hf_daily").exit_code == 0
    assert load_config(vault / "config.yaml").sources == ["arxiv", "hf_daily"]
    res = run(vault, "sources", "enable", "nope")
    assert res.exit_code == 1 and "arxiv, hf_daily" in res.output


def test_cli_disable_last_refused(vault):
    run(vault, "sources", "disable", "hf_daily")
    res = run(vault, "sources", "disable", "arxiv")
    assert res.exit_code == 1 and "at least one source must stay enabled" in res.output


def test_cli_plugins_reports_broken(vault, monkeypatch):
    def bad():
        raise ImportError("gone")

    ep = SimpleNamespace(name="broken", load=bad, dist=SimpleNamespace(name="d"))
    monkeypatch.setattr(registry, "_entry_points", lambda group: [ep] if group == registry.SOURCE_GROUP else [])
    res = runner.invoke(cli.app, ["plugins"])
    assert res.exit_code == 0, res.output
    assert "research_pipeline.sources" in res.output and "arxiv" in res.output and "built-in" in res.output
    assert "broken" in res.output and "ImportError" in res.output
