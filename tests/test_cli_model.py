"""Backend selection: ollama, claude-code, none."""

from datetime import date
from types import SimpleNamespace

import pytest
from conftest import FakeEmbedder
from ruamel.yaml import YAML
from typer.testing import CliRunner

from research_pipeline import cli, profile_edit, registry
from research_pipeline.backends import claude_code
from research_pipeline.cli import Pipeline
from research_pipeline.llm import NO_MODEL, LlmError, LlmLimitReached
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

    def propose(llm, description):
        seen.append(llm)
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


def test_set_backend_rejects_plugin_that_failed_to_load(vault, monkeypatch):
    def boom():
        raise ImportError("gone")

    ep = SimpleNamespace(name="flaky", load=boom, dist=SimpleNamespace(name="d"))
    monkeypatch.setattr(registry, "_entry_points", lambda group: [ep] if group == registry.BACKEND_GROUP else [])
    result = run(vault, "model", "set-backend", "flaky")
    assert result.exit_code != 0 and load_config(vault / "config.yaml").llm_backend == "ollama"


def test_nightly_usage_limit_in_check_reads_as_summaries_stopped(vault, monkeypatch):
    class Limited:
        name, local, model_name, num_ctx = "x", False, "m", 1000

        def check(self):
            raise LlmLimitReached("Claude Code usage limit reached")

    monkeypatch.setattr(cli, "build_pipeline", lambda c: Pipeline(
        [], FakeEmbedder, lambda ids: [], lambda: Limited(), lambda pid: []))
    assert run(vault, "run").exit_code == 0
    text = digest_text(vault)
    assert "summaries stopped: usage limit reached" in text and "model unavailable" not in text


def test_startup_errors_survive_an_embedder_crash(vault, monkeypatch):
    def crash(*a, **k):
        raise RuntimeError("no gpu")

    pipe = Pipeline([], FakeEmbedder, lambda ids: [], lambda: None, lambda pid: [],
                    startup_errors=["source gone: not installed; see `rpipe sources list`"])
    monkeypatch.setattr(cli, "build_pipeline", lambda c: pipe)
    monkeypatch.setattr(cli, "_load_embedder", crash)
    run(vault, "run")
    assert "source gone" in digest_text(vault)


def test_collect_reports_startup_errors(vault, monkeypatch):
    pipe = Pipeline([], FakeEmbedder, lambda ids: [], lambda: None, lambda pid: [],
                    startup_errors=["source gone: not installed"])
    monkeypatch.setattr(cli, "build_pipeline", lambda c: pipe)
    result = run(vault, "collect")
    assert "source gone: not installed" in result.output


def test_old_config_without_effort_key_behaves_as_low(vault):
    text = "\n".join(ln for ln in (vault / "config.yaml").read_text().splitlines() if not ln.startswith("llm_effort"))
    (vault / "config.yaml").write_text(text + "\n")
    assert load_config(vault / "config.yaml").llm_effort == "low"


def test_new_vault_config_template_has_effort(vault):
    assert "llm_effort: low" in (vault / "config.yaml").read_text()


def test_set_effort_valid_writes_and_preserves_other_keys(vault):
    set_config(vault, llm_backend="claude-code", llm_model="haiku")
    result = run(vault, "model", "set-effort", "high")
    assert result.exit_code == 0, result.output
    cfg = load_config(vault / "config.yaml")
    assert cfg.llm_effort == "high" and cfg.llm_model == "haiku" and cfg.llm_backend == "claude-code"
    assert "effort is now high" in result.output


@pytest.mark.parametrize("bad", ["max", "turbo", "LOWEST"])
def test_set_effort_invalid_is_refused_and_changes_nothing(vault, bad):
    before = (vault / "config.yaml").read_bytes()
    result = run(vault, "model", "set-effort", bad)
    assert result.exit_code == 1
    assert "low, medium, high" in result.output
    assert (vault / "config.yaml").read_bytes() == before


def test_set_effort_accepts_capitals_and_spaces(vault):
    assert run(vault, "model", "set-effort", " Medium ").exit_code == 0
    assert load_config(vault / "config.yaml").llm_effort == "medium"


def test_model_show_lists_effort_under_claude_code(vault):
    set_config(vault, llm_backend="claude-code", llm_effort="medium")
    assert "effort: medium" in run(vault, "model", "show").output


def test_model_show_says_effort_only_applies_to_claude_code(vault):
    out = run(vault, "model", "show").output  # default backend ollama
    assert "effort: low (only applies to the claude-code backend)" in out


def test_claude_code_factory_passes_effort(vault, monkeypatch):
    monkeypatch.setattr(registry, "find_claude", lambda p: "/opt/claude")
    cfg = load_config(vault / "config.yaml").model_copy(update={"llm_backend": "claude-code", "llm_effort": "high"})
    client = registry.make_claude_code(cfg, vault, lambda s: None)
    argv = client.argv("S", claude_code._Ping)
    assert argv[argv.index("--effort") + 1] == "high"


def test_ollama_backend_ignores_effort(vault, monkeypatch):
    monkeypatch.setattr(registry.ollama_fit, "resolve", lambda *a, **k: SimpleNamespace(model="m", num_ctx=8192))
    cfg = load_config(vault / "config.yaml").model_copy(update={"llm_effort": "high"})
    client = registry.make_ollama(cfg, vault, lambda s: None)
    assert client.name == "ollama" and not hasattr(client, "effort")


def test_summarize_command_prints_one_line_per_paper(vault, monkeypatch):
    def fake(conn, llm, fetch, num_ctx, today=None, backlog_limit=None, paper_ids=None, *, reporter=None, refetch=False,
             mode="whole", fallback_above=None):
        from research_pipeline.progress import ProgressEvent
        for pid, n, outcome, detail in (("2611.26408", 1, "done", "41s"), ("2611.26409", 2, "failed", "bad json"),
                                        ("2611.26410", 2, "stopped", "")):
            reporter.emit(ProgressEvent("summarize", "step", label=pid, current=n, total=2, outcome=outcome, detail=detail))
        return {}, []
    monkeypatch.setattr(cli, "run_summarize", fake)
    llm = SimpleNamespace(name="claude-code", local=False, model_name="haiku", num_ctx=1000)
    monkeypatch.setattr(cli, "_ready_llm", lambda p: llm)
    set_config(vault, llm_backend="claude-code")
    from research_pipeline.db import open_db, upsert_paper
    from research_pipeline.models import PaperMeta
    conn = open_db(vault / "vault.sqlite")
    upsert_paper(conn, PaperMeta(id="2611.26408", title="T", source="arxiv", url="http://x"))
    conn.commit()
    conn.close()
    result = run(vault, "summarize", "2611.26408")
    assert "[1/2] 2611.26408 done (summary + critique, 41s)" in result.stderr
    assert "[2/2] 2611.26410 stopped: usage limit reached" in result.stderr
    assert "[2/2] 2611.26409 failed: bad json" in result.stderr


def test_refetch_needs_ids_and_rejects_backlog(vault):
    for args in (["summarize", "--refetch"], ["summarize", "--backlog", "--refetch"]):
        result = run(vault, *args)
        assert result.exit_code != 0
        assert "--refetch" in result.output and "paper id" in result.output
    mixed = run(vault, "summarize", "2611.26408", "--backlog", "--refetch")
    assert mixed.exit_code != 0


def test_summarize_refetch_is_passed_through(vault, monkeypatch):
    seen = {}

    def fake(conn, llm, fetch, num_ctx, **kw):
        seen.update(kw)
        return {}, []
    monkeypatch.setattr(cli, "run_summarize", fake)
    llm = SimpleNamespace(name="claude-code", local=False, model_name="haiku", num_ctx=1000)
    monkeypatch.setattr(cli, "_ready_llm", lambda p: llm)
    set_config(vault, llm_backend="claude-code")
    from research_pipeline.db import open_db, upsert_paper
    from research_pipeline.models import PaperMeta
    conn = open_db(vault / "vault.sqlite")
    upsert_paper(conn, PaperMeta(id="2611.26408", title="T", source="arxiv", url="http://x"))
    conn.commit()
    conn.close()
    assert run(vault, "summarize", "2611.26408", "--refetch").exit_code == 0
    assert seen["refetch"] is True and seen["paper_ids"] == ["2611.26408"]


def test_set_backend_message_says_reset_only_when_backend_changes(vault):
    run(vault, "model", "set", "qwen3:14b")
    same = run(vault, "model", "set-backend", "ollama", "--force")
    assert "model unchanged" in same.output and "reset" not in same.output
    assert load_config(vault / "config.yaml").llm_model == "qwen3:14b"
    changed = run(vault, "model", "set-backend", "none", "--force")
    assert "model reset to auto" in changed.output


def test_model_fit_progress_notes_appear_once_on_stderr(vault, monkeypatch):
    def fake_resolve(url, vault_, model, num_ctx, force=False, log=lambda s: None, **kw):
        log("trying fake-model at 8192 tokens")
        return SimpleNamespace(model="fake-model", num_ctx=8192)

    monkeypatch.setattr(cli.ollama_fit, "resolve", fake_resolve)
    result = runner.invoke(cli.app, ["model", "fit", "--vault", str(vault)])
    assert result.exit_code == 0, result.output
    assert result.stderr.count("trying fake-model at 8192 tokens") == 1
    assert "model fit done (" in result.stderr
    assert "Using model fake-model with a context of 8192 tokens" in result.stdout


def test_pull_model_progress_emits_steps_when_percent_changes():
    from research_pipeline.progress import ListReporter, stage

    rep = ListReporter()
    with stage(rep, "model pull") as h:
        cb = cli._pull_progress(h, "m:1")
        for line in ("pulling manifest", "downloading 10%", "downloading 10%", "downloading 43%"):
            cb(line)
    steps = [(e.label, e.detail) for e in rep.events if e.kind == "step"]
    assert steps == [("m:1", "10%"), ("m:1", "43%")]


def test_set_embedding_saves_and_explains_consequences(vault):
    res = run(vault, "model", "set-embedding", "all-MiniLM-L6-v2")
    assert res.exit_code == 0, res.output
    assert load_config(vault / "config.yaml").embedding_model == "all-MiniLM-L6-v2"
    out = " ".join(res.output.split())
    assert "downloads the model" in out and "re-embeds your shelf" in out
    assert "stop boosting rankings until you switch back" in out
    assert "rpipe model set-embedding BAAI/bge-small-en-v1.5" in out


def test_set_embedding_blank_exits_1(vault):
    before = (vault / "config.yaml").read_bytes()
    res = run(vault, "model", "set-embedding", "  ")
    assert res.exit_code == 1
    assert (vault / "config.yaml").read_bytes() == before


def test_set_mode_command_validates(vault):
    result = run(vault, "model", "set-mode", "SPLIT")
    assert result.exit_code == 0, result.output
    assert load_config(vault / "config.yaml").summarize_mode == "split"
    before = (vault / "config.yaml").read_text()
    bad = run(vault, "model", "set-mode", "fast")
    assert bad.exit_code == 1
    assert "auto, whole, split" in bad.output
    assert (vault / "config.yaml").read_text() == before


def test_set_mode_works_for_claude_code(vault):
    set_config(vault, llm_backend="claude-code")
    assert run(vault, "model", "set-mode", "split").exit_code == 0
    assert load_config(vault / "config.yaml").summarize_mode == "split"


def test_model_show_prints_summarize_mode(vault):
    out = run(vault, "model", "show").output
    assert "summarize mode: auto" in out and "split" in out
    set_config(vault, llm_backend="claude-code")
    run(vault, "model", "set-mode", "whole")
    out = run(vault, "model", "show").output
    assert "summarize mode: whole" in out
