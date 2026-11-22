"""Backend selection: ollama, claude-code, none."""

from datetime import date
from types import SimpleNamespace

import pytest
from ruamel.yaml import YAML
from typer.testing import CliRunner

from conftest import FakeEmbedder
from research_pipeline import cli, profile_edit, registry
from research_pipeline.backends import claude_code
from research_pipeline.cli import Pipeline
from research_pipeline.llm import NO_MODEL, LlmError
from research_pipeline.vault import init_vault, load_config

runner = CliRunner()
KNOWN = {"ollama", "claude-code", "none"}


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "_entry_points", lambda group: [])
    path = tmp_path / "vault"
    init_vault(path, topics=["LLM security"])
    return path


def run(vault, *args, input=None):
    return runner.invoke(cli.app, [*args, "--vault", str(vault)], input=input)


def set_config(vault, **values):
    path = vault / "config.yaml"
    yaml = YAML()
    data = yaml.load(path.read_text())
    data.update(values)
    with path.open("w") as f:
        yaml.dump(data, f)


def digest_text(vault):
    (path,) = (vault / "digests").glob("*.md")
    return path.read_text()


def install_real_backend(monkeypatch, vault):
    """A run with no network: real backend factory, fake embedder, no sources."""
    config = load_config(vault / "config.yaml")
    monkeypatch.setattr(
        cli,
        "build_pipeline",
        lambda c: Pipeline(
            [], FakeEmbedder, lambda ids: [], lambda: cli.build_llm(config, vault), lambda pid: []
        ),
    )


def test_none_backend_run_writes_abstract_digest_without_errors(vault, monkeypatch):
    set_config(vault, llm_backend="none")
    install_real_backend(monkeypatch, vault)
    result = run(vault, "run")
    assert result.exit_code == 0, result.output
    text = digest_text(vault)
    assert "summaries off (abstracts-only mode)" in text
    assert "errors: none" in text


def test_claude_code_missing_binary_run_still_writes_digest(vault, tmp_path, monkeypatch):
    set_config(vault, llm_backend="claude-code", llm_cli_path="/gone/claude")
    monkeypatch.setattr(claude_code.shutil, "which", lambda name: None)
    monkeypatch.setattr(claude_code.Path, "home", lambda: tmp_path / "nohome")
    install_real_backend(monkeypatch, vault)
    result = run(vault, "run")
    assert result.exit_code == 0, result.output
    text = digest_text(vault)
    assert "summaries skipped, model unavailable: Claude Code not found" in text
    assert "rpipe model setup" in text


def test_set_backend_resets_model_to_auto_and_keeps_ollama_keys(vault):
    set_config(vault, llm_model="qwen3:8b", llm_url="http://example:1", llm_num_ctx=16384)
    profile_edit.set_llm_backend(vault, "claude-code", KNOWN, "/opt/claude")
    cfg = load_config(vault / "config.yaml")
    assert (cfg.llm_backend, cfg.llm_model, cfg.llm_cli_path) == ("claude-code", "auto", "/opt/claude")
    assert (cfg.llm_url, cfg.llm_num_ctx) == ("http://example:1", 16384)
    profile_edit.set_llm_backend(vault, "none", KNOWN)  # cli_path omitted: left as is
    assert load_config(vault / "config.yaml").llm_cli_path == "/opt/claude"


def test_set_backend_unknown_name_lists_known(vault):
    result = run(vault, "model", "set-backend", "bogus")
    assert result.exit_code == 1
    assert "claude-code, none, ollama" in result.output
    assert load_config(vault / "config.yaml").llm_backend == "ollama"


def patch_claude(monkeypatch, path, logged):
    monkeypatch.setattr(cli, "find_claude", lambda recorded: path)
    monkeypatch.setattr(cli, "logged_in", lambda p: logged)


def test_set_backend_claude_refuses_when_logged_out_unless_force(vault, monkeypatch):
    patch_claude(monkeypatch, "/opt/claude", False)
    result = run(vault, "model", "set-backend", "claude-code")
    assert result.exit_code == 1 and "not logged in" in result.output
    assert load_config(vault / "config.yaml").llm_backend == "ollama"
    assert run(vault, "model", "set-backend", "claude-code", "--force").exit_code == 0
    cfg = load_config(vault / "config.yaml")
    assert (cfg.llm_backend, cfg.llm_cli_path) == ("claude-code", "/opt/claude")


def test_set_backend_claude_refuses_when_not_found(vault, monkeypatch):
    patch_claude(monkeypatch, None, False)
    result = run(vault, "model", "set-backend", "claude-code")
    assert result.exit_code == 1 and "not found" in result.output


def test_set_backend_claude_ok_records_path(vault, monkeypatch):
    patch_claude(monkeypatch, "/opt/claude", True)
    assert run(vault, "model", "set-backend", "claude-code").exit_code == 0
    assert load_config(vault / "config.yaml").llm_cli_path == "/opt/claude"


def test_set_backend_ollama_refuses_when_unreachable(vault, monkeypatch):
    set_config(vault, llm_backend="none")

    def down(self):
        raise LlmError("cannot reach Ollama at x")

    monkeypatch.setattr(cli.OllamaClient, "ping", down)
    result = run(vault, "model", "set-backend", "ollama")
    assert result.exit_code == 1 and "cannot reach Ollama" in result.output
    assert load_config(vault / "config.yaml").llm_backend == "none"
    monkeypatch.setattr(cli.OllamaClient, "ping", lambda self: None)
    assert run(vault, "model", "set-backend", "ollama").exit_code == 0
    assert load_config(vault / "config.yaml").llm_backend == "ollama"


def test_model_set_validates_per_backend(vault):
    set_config(vault, llm_backend="claude-code")
    profile_edit.set_llm_model(vault, "haiku")
    assert load_config(vault / "config.yaml").llm_model == "haiku"
    with pytest.raises(ValueError, match="sonnet, haiku, opus"):
        profile_edit.set_llm_model(vault, "qwen3:8b")
    set_config(vault, llm_backend="none")
    with pytest.raises(ValueError, match="abstracts-only"):
        profile_edit.set_llm_model(vault, "haiku")
    set_config(vault, llm_backend="ollama")
    profile_edit.set_llm_model(vault, "qwen3:8b")
    assert load_config(vault / "config.yaml").llm_model == "qwen3:8b"


@pytest.mark.parametrize("args", [["fit"], ["set-url", "http://x:1"], ["set-context", "16384"]])
def test_ollama_only_commands_refuse_on_claude_code(vault, args):
    set_config(vault, llm_backend="claude-code")
    before = (vault / "config.yaml").read_bytes()
    result = run(vault, "model", *args)
    assert result.exit_code == 1
    assert f"`rpipe model {args[0]}` only applies to the ollama backend (current: claude-code)" in result.output
    assert "Nothing was changed." in result.output
    assert (vault / "config.yaml").read_bytes() == before


def test_summarize_eval_and_suggest_refuse_under_none(vault):
    set_config(vault, llm_backend="none")
    for args in (["summarize"], ["eval", "run"], ["profile", "suggest", "I study LLM agent security."], ["model", "check"]):
        result = run(vault, *args)
        assert result.exit_code == 1, args
        assert NO_MODEL in result.output, args


def test_model_show_first_line_names_backend(vault):
    set_config(vault, llm_backend="none")
    assert run(vault, "model", "show").output.splitlines()[0] == "backend: none"


def test_suggest_announces_non_local_backend(vault, monkeypatch):
    llm = SimpleNamespace(name="claude-code", local=False, model_name="sonnet", num_ctx=1000)
    monkeypatch.setattr(cli, "_onboarding_llm", lambda config: llm)
    seen = []

    def propose(l, description):
        seen.append(l)
        raise LlmError("stop here")

    monkeypatch.setattr(cli.onboarding, "propose", propose)
    result = run(vault, "profile", "suggest", "I study LLM agent security and prompt injection.")
    first = "Your description will be sent to claude-code (model sonnet) to suggest topics."
    assert first in result.output and seen == [llm]


def test_init_describe_step_under_none_prints_notice_and_moves_on(vault, capsys, monkeypatch):
    set_config(vault, llm_backend="none")
    monkeypatch.setattr(cli, "_ask", lambda prompt: pytest.fail("must not ask for a description"))
    cli._wizard_describe(vault, load_config(vault / "config.yaml"))
    assert NO_MODEL in capsys.readouterr().out


def test_health_line_summaries_off():
    line = cli.health_line(date(2026, 1, 1), {"summaries_off": 1}, [])
    assert "summaries off (abstracts-only mode)" in line
