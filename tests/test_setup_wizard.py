import httpx
import pytest
import respx
from typer.testing import CliRunner

from research_pipeline import catalog as catalog_mod
from research_pipeline import cli, registry
from research_pipeline.catalog import Catalog, CatalogEntry
from research_pipeline.llm import LlmError, pull_model
from research_pipeline.machine import Gpu, MachineInfo
from research_pipeline.vault import init_vault, load_config

runner = CliRunner()


def machine(**kw):
    base = dict(os="Linux", ram_gb=32.0, gpu=None, ollama_installed=False, ollama_running=False,
                ollama_models=[], claude_path=None, claude_logged_in=False)
    base.update(kw)
    return MachineInfo(**base)


GPU16 = Gpu("RTX 5070 Ti", 16.0, "nvidia")
TINY_RAM = 4.0  # too little memory for any model on the CPU


def _entry(pull, gb, params, bands, kv):
    return CatalogEntry(id=pull, family="Qwen3", pull=pull, arch="dense", params_b=params, quant="Q4_K_M",
                        download_gb=gb, trained_ctx=40960, kv_mib_per_1k=kv, bands=bands)


CATALOG = Catalog(version=1, updated="2026-11-24", entries=[
    _entry("qwen3:4b", 2.5, 4.0, ["cpu", "small"], 140.6),
    _entry("qwen3:14b", 9.3, 14.8, ["mid"], 156.2),
])


class Ready:
    def __init__(self, line="ok"):
        self.line = line

    def check(self):
        return self.line


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "_entry_points", lambda group: [])
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    path = tmp_path / "vault"
    init_vault(path, topics=["LLM security"])
    return path


def setup_run(vault, monkeypatch, infos, input, check=None, keep=False):
    """Run `rpipe model setup`; `infos` is the list of machines successive detections return."""
    seq = list(infos)
    monkeypatch.setattr(catalog_mod, "load_catalog", lambda vault: CATALOG)
    monkeypatch.setattr(cli, "detect_machine", lambda config: seq.pop(0) if len(seq) > 1 else seq[0])
    pulled = []
    monkeypatch.setattr(cli, "pull_model", lambda url, tag, progress: (progress("pulling 50%"), pulled.append(tag)))

    def build(config, vault_, log=lambda s: None):
        if isinstance(check, Exception):
            raise check
        return Ready("Ollama is running") if config.llm_backend != "claude-code" else Ready("Claude Code is logged in")

    monkeypatch.setattr(cli, "build_llm", build)
    # The first question is "Keep current backend?"; tests answer n unless they test keeping.
    result = runner.invoke(cli.app, ["model", "setup", "--vault", str(vault)],
                           input=input if keep else "n\n" + input)
    return result, pulled


def cfg(vault):
    return load_config(vault / "config.yaml")


def test_gpu_user_accepts_pull_and_ends_ready(vault, monkeypatch):
    m = machine(gpu=GPU16, ollama_installed=True, ollama_running=True)
    result, pulled = setup_run(vault, monkeypatch, [m], "1\ny\n")
    assert pulled == ["qwen3:14b"] and cfg(vault).llm_backend == "ollama"
    assert "RTX 5070 Ti" in result.output and "Ready:" in result.output and result.exit_code == 0


def test_declining_pull_saves_choice_but_not_ready(vault, monkeypatch):
    m = machine(gpu=GPU16, ollama_installed=True, ollama_running=True)
    result, pulled = setup_run(vault, monkeypatch, [m], "1\nn\n", check=LlmError("model not installed"))
    assert pulled == [] and cfg(vault).llm_backend == "ollama"
    assert result.exit_code == 1
    assert "Not ready yet: model not installed" in result.output and "rpipe model setup" in result.output
    lines = result.output.splitlines()
    assert "Not ready yet: model not installed" in lines
    assert "Fix it, then run rpipe model setup" in lines
    assert "\\n" not in result.output
    assert "" in lines and "Recommended: ollama" in lines


def test_no_ollama_prints_install_command_and_rechecks(vault, monkeypatch):
    first = machine(gpu=GPU16)
    second = machine(gpu=GPU16, ollama_installed=True, ollama_running=True)
    result, pulled = setup_run(vault, monkeypatch, [first, second], "1\ny\ny\n")
    assert "curl -fsSL https://ollama.com/install.sh | sh" in result.output
    assert "Installed it? Check again" in result.output
    assert pulled == ["qwen3:14b"] and result.exit_code == 0


def test_declining_the_install_recheck_moves_on(vault, monkeypatch):
    result, pulled = setup_run(vault, monkeypatch, [machine(gpu=GPU16)], "1\nn\n", check=LlmError("cannot reach Ollama"))
    assert pulled == [] and cfg(vault).llm_backend == "ollama" and result.exit_code == 1


def test_claude_user_picks_haiku_and_cli_path_recorded(vault, monkeypatch):
    m = machine(claude_path="/home/u/.local/bin/claude", claude_logged_in=True)
    result, _ = setup_run(vault, monkeypatch, [m], "1\nhaiku\n")
    c = cfg(vault)
    assert (c.llm_backend, c.llm_model, c.llm_cli_path) == ("claude-code", "haiku", "/home/u/.local/bin/claude")
    assert "Anthropic" in result.output and "30 calls" in result.output and "never leaves" in result.output
    assert result.exit_code == 0


def test_claude_default_model_is_sonnet_and_bad_name_reprompts(vault, monkeypatch):
    m = machine(claude_path="/c/claude", claude_logged_in=True)
    result, _ = setup_run(vault, monkeypatch, [m], "1\ngpt-9\n\n")
    assert cfg(vault).llm_model == "sonnet" and result.exit_code == 0


def test_claude_not_logged_in_rechecks(vault, monkeypatch, capsys):
    out = machine(claude_path="/c/claude")
    inn = machine(claude_path="/c/claude", claude_logged_in=True)
    monkeypatch.setattr(cli, "detect_machine", lambda config: inn)
    monkeypatch.setattr(cli.typer, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(cli.typer, "prompt", lambda *a, **k: "haiku")
    cli._setup_claude(vault, cfg(vault), out, {"claude-code", "ollama", "none"})
    assert "Run `claude` once in a terminal and log in" in capsys.readouterr().out
    c = cfg(vault)
    assert (c.llm_backend, c.llm_model, c.llm_cli_path) == ("claude-code", "haiku", "/c/claude")


def test_nothing_available_saves_none_and_is_ready(vault, monkeypatch):
    result, _ = setup_run(vault, monkeypatch, [machine(ram_gb=TINY_RAM)], "\n")
    assert cfg(vault).llm_backend == "none"
    assert "no GPU found" in result.output and "abstracts" in result.output and "Ready" in result.output
    assert result.exit_code == 0


def test_wizard_cpu_only_without_cloud_recommends_cpu_model(vault, monkeypatch):
    m = machine(ram_gb=16.0, ollama_installed=True, ollama_running=True)
    result, pulled = setup_run(vault, monkeypatch, [m], "1\ny\n")
    assert result.exit_code == 0, result.output
    assert "Recommended: ollama" in result.output
    assert "no GPU found; a small model runs on the CPU overnight (slow)" in result.output
    assert "Download qwen3:4b now (2.5 GB)? [y/N]" in result.output
    c = cfg(vault)
    assert pulled == ["qwen3:4b"] and (c.llm_backend, c.llm_model, c.llm_num_ctx) == ("ollama", "qwen3:4b", 32768)


def test_wizard_refuses_download_without_disk(vault, monkeypatch):
    m = machine(gpu=GPU16, gpus=[GPU16], ollama_installed=True, ollama_running=True, disk_free_gb=10.0)
    result, pulled = setup_run(vault, monkeypatch, [m], "1\n", check=LlmError("model not installed"))
    assert "download 9.3 GB; free disk where Ollama keeps models: 10 GB" in result.output
    assert "not enough free disk" in result.output
    assert pulled == [] and cfg(vault).llm_backend == "ollama"


def test_wizard_download_defaults_to_no(vault, monkeypatch):
    m = machine(gpu=GPU16, gpus=[GPU16], ollama_installed=True, ollama_running=True)
    result, pulled = setup_run(vault, monkeypatch, [m], "1\n\n", check=LlmError("model not installed"))
    assert "[y/N]" in result.output and pulled == []


def test_wizard_gpu_pull_leaves_model_auto(vault, monkeypatch):
    m = machine(gpu=GPU16, gpus=[GPU16], ollama_installed=True, ollama_running=True)
    setup_run(vault, monkeypatch, [m], "1\ny\n")
    c = cfg(vault)
    assert (c.llm_model, c.llm_num_ctx) == ("auto", "auto")


def test_user_overrides_recommendation(vault, monkeypatch):
    m = machine(gpu=GPU16, ollama_installed=True, ollama_running=True, ollama_models=["x"],
                claude_path="/c/claude", claude_logged_in=True)
    result, pulled = setup_run(vault, monkeypatch, [m], "2\nopus\n")
    assert cfg(vault).llm_backend == "claude-code" and cfg(vault).llm_model == "opus" and pulled == []


def test_model_setup_refuses_without_terminal(vault, monkeypatch):
    monkeypatch.setattr(cli, "_is_tty", lambda: False)
    result = runner.invoke(cli.app, ["model", "setup", "--vault", str(vault)])
    assert result.exit_code == 1
    assert "rpipe model setup is interactive; use `rpipe model set-backend NAME` in scripts" in result.output


def test_init_runs_backend_step_before_describe(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_wizard_backend", lambda vault, offer_keep=False: calls.append("backend") or True)
    monkeypatch.setattr(cli, "_wizard_describe", lambda v, c: calls.append("describe"))
    monkeypatch.setattr(cli, "_wizard_topics", lambda v, f: calls.append("topics"))
    monkeypatch.setattr(cli, "_wizard_categories", lambda v, c, f: calls.append("categories"))
    monkeypatch.setattr(cli, "_wizard_seeds", lambda v, c: calls.append("seeds"))
    result = runner.invoke(cli.app, ["init", "--vault", str(tmp_path / "v")])
    assert result.exit_code == 0, result.output
    assert calls == ["backend", "describe", "topics", "categories", "seeds"]


def test_non_tty_init_points_at_model_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_is_tty", lambda: False)
    result = runner.invoke(cli.app, ["init", "--vault", str(tmp_path / "v")])
    assert "rpipe model setup" in result.output


def _stream(lines):
    return httpx.Response(200, content="\n".join(lines).encode())


@respx.mock
def test_pull_model_streams_progress_and_raises_on_error():
    url = "http://localhost:11434"
    respx.post(f"{url}/api/pull").mock(return_value=_stream([
        '{"status": "pulling manifest"}',
        '{"status": "downloading", "total": 100, "completed": 10}',
        '{"status": "downloading", "total": 100, "completed": 10}',
        '{"status": "downloading", "total": 100, "completed": 55}',
        '{"status": "success"}',
    ]))
    seen = []
    pull_model(url, "qwen3:14b", seen.append)
    assert seen == ["pulling manifest", "downloading 10%", "downloading 55%", "success"]

    respx.post(f"{url}/api/pull").mock(return_value=_stream(['{"error": "pull model manifest: file does not exist"}']))
    with pytest.raises(LlmError, match="file does not exist"):
        pull_model(url, "nope", lambda s: None)
    respx.post(f"{url}/api/pull").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(LlmError, match="500"):
        pull_model(url, "x", lambda s: None)
    respx.post(f"{url}/api/pull").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LlmError, match="cannot reach Ollama"):
        pull_model(url, "x", lambda s: None)


def test_menu_always_lists_all_three_even_when_nothing_is_ready(vault, monkeypatch):
    result, _ = setup_run(vault, monkeypatch, [machine(ram_gb=TINY_RAM)], "\n")
    assert "1. no summaries" in result.output and "[recommended]" in result.output
    assert "a model on this computer (Ollama)" in result.output and "Claude Code (your login)" in result.output


def test_choosing_claude_when_not_logged_in_reaches_the_recheck(vault, monkeypatch):
    out = machine(claude_path="/c/claude", ram_gb=TINY_RAM)
    inn = machine(claude_path="/c/claude", claude_logged_in=True, ram_gb=TINY_RAM)
    # menu order with recommendation none: none, ollama, claude-code -> 3; then re-check yes; model default
    result, _ = setup_run(vault, monkeypatch, [out, inn], "3\ny\n\n")
    assert "log in" in result.output and cfg(vault).llm_backend == "claude-code"


def test_keeping_the_backend_keeps_a_pinned_model(vault, monkeypatch):
    from research_pipeline import profile_edit
    profile_edit.set_llm_model(vault, "qwen3:14b")
    result, pulled = setup_run(vault, monkeypatch, [machine(gpu=GPU16)], "\n", keep=True)
    assert "Keep current backend (ollama)? [Y/n]" in result.output
    assert cfg(vault).llm_model == "qwen3:14b" and pulled == [] and "Ready:" in result.output


def test_rechoosing_the_same_backend_keeps_model_but_switching_resets(vault):
    from research_pipeline import profile_edit
    known = {"ollama", "claude-code", "none"}
    profile_edit.set_llm_model(vault, "qwen3:14b")
    profile_edit.set_llm_backend(vault, "ollama", known)
    assert cfg(vault).llm_model == "qwen3:14b"
    profile_edit.set_llm_backend(vault, "none", known)
    assert cfg(vault).llm_model == "auto"


def test_setup_interrupted_says_stopped_and_changes_nothing(vault, monkeypatch):
    m = machine(gpu=GPU16, ollama_installed=True, ollama_running=True)
    before = cfg(vault)
    result, pulled = setup_run(vault, monkeypatch, [m], "")  # input ends at the first question
    assert result.exit_code == 130 and "Stopped. Nothing further was changed" in result.output
    assert pulled == [] and cfg(vault) == before
