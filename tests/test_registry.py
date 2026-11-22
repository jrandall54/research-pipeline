from pathlib import Path
from types import SimpleNamespace

import pytest

from research_pipeline import registry
from research_pipeline.llm import LlmError
from research_pipeline.models import Config


def fake_ep(name, load, dist="fake-dist"):
    return SimpleNamespace(name=name, load=load, dist=SimpleNamespace(name=dist))


def raise_import():
    raise ImportError("no module named gone")


def f(config):
    return None


def test_plugins_merges_entry_points_and_reports_broken(monkeypatch):
    good, bad, clash = fake_ep("mine", lambda: f), fake_ep("broken", raise_import), fake_ep("arxiv", lambda: f)
    monkeypatch.setattr(registry, "_entry_points", lambda group: [good, bad, clash])
    p = registry.plugins(registry.SOURCE_GROUP)
    assert p["arxiv"].origin == "built-in" and p["mine"].factory is f
    assert p["mine"].origin == "fake-dist"
    assert p["broken"].factory is None and "ImportError" in p["broken"].error
    assert p["arxiv (fake-dist)"].error == "name taken by a built-in"
    assert p["arxiv (fake-dist)"].factory is None


def test_builtin_sources_present(monkeypatch):
    monkeypatch.setattr(registry, "_entry_points", lambda group: [])
    assert list(registry.plugins(registry.SOURCE_GROUP)) == ["arxiv", "hf_daily"]
    assert list(registry.plugins(registry.BACKEND_GROUP)) == ["ollama", "claude-code", "none"]


def test_build_backend_unknown_plugin_raises_with_setup_hint(monkeypatch):
    monkeypatch.setattr(registry, "_entry_points", lambda group: [])
    config = Config(vault_path="/tmp/x", llm_backend="gone")
    with pytest.raises(LlmError, match="backend gone: not installed; run `rpipe model setup`"):
        registry.build_backend(config, Path("/tmp/x"), lambda s: None)


def test_build_backend_broken_plugin_and_none(monkeypatch):
    monkeypatch.setattr(
        registry, "_entry_points", lambda group: [fake_ep("flaky", raise_import)] if group == registry.BACKEND_GROUP else []
    )
    with pytest.raises(LlmError, match="backend flaky: .*rpipe model setup"):
        registry.build_backend(Config(vault_path="/tmp/x", llm_backend="flaky"), Path("/tmp/x"), print)
    assert registry.build_backend(Config(vault_path="/tmp/x", llm_backend="none"), Path("/tmp/x"), print) is None


def test_build_sources_skips_unknown_and_broken_with_messages(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "_entry_points", lambda group: [fake_ep("broken", raise_import)])
    cfg = Config(vault_path=tmp_path, sources=["hf_daily", "gone", "broken"])
    sources, errors = registry.build_sources(cfg)
    assert [s.name for s in sources] == ["hf_daily"]
    assert errors[0] == "source gone: not installed; see `rpipe sources list`"
    assert errors[1].startswith("source broken: ") and "ImportError" in errors[1]
    assert errors[1].endswith("; see `rpipe sources list`")


def test_build_sources_factory_raising_is_reported(tmp_path, monkeypatch):
    def boom(config):
        raise RuntimeError("bad setup")

    monkeypatch.setattr(registry, "_entry_points", lambda group: [fake_ep("mine", lambda: boom)])
    cfg = Config(vault_path=tmp_path, sources=["mine", "hf_daily"])
    sources, errors = registry.build_sources(cfg)
    assert [s.name for s in sources] == ["hf_daily"]
    assert "source mine: RuntimeError: bad setup" in errors[0]


def test_plugin_that_calls_sys_exit_on_import_is_reported_not_fatal(monkeypatch):
    def exits():
        raise SystemExit(2)

    monkeypatch.setattr(registry, "_entry_points", lambda group: [fake_ep("quitter", exits)])
    p = registry.plugins(registry.SOURCE_GROUP)
    assert p["quitter"].factory is None and "SystemExit" in p["quitter"].error
