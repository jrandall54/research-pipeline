import json
import subprocess

import httpx
import pytest
import respx

from research_pipeline.machine import (
    APPLE_GPU_FRACTION, OLLAMA_INSTALL, Gpu, MachineInfo, ModelChoice, detect_gpu,
    detect_machine, load_model_table, ollama_tags, recommend, system_cmd,
)
from research_pipeline.models import Config

TABLE = [ModelChoice(6, "small:8b", 5.0), ModelChoice(12, "mid:14b", 9.0), ModelChoice(24, "big:32b", 20.0)]


def cmds(table):
    def run(argv, timeout):
        return table.get(argv[0], (127, ""))
    return run


def info(**kw):
    base = dict(os="Linux", ram_gb=32.0, gpu=None, ollama_installed=False, ollama_running=False,
                ollama_models=[], claude_path=None, claude_logged_in=False)
    base.update(kw)
    return MachineInfo(**base)


def test_nvidia_largest_gpu_wins():
    run = cmds({"nvidia-smi": (0, "RTX 3060, 12288\nRTX 5070 Ti, 16303\n")})
    assert detect_gpu(run, "Linux", "x86_64") == Gpu("RTX 5070 Ti", pytest.approx(15.92, 0.01), "nvidia")


def test_failing_nvidia_smi_falls_through_to_none():
    out = "Failed to initialize NVML: Driver/library version mismatch"
    assert detect_gpu(cmds({"nvidia-smi": (9, out)}), "Linux", "x86_64") is None


def test_unparsable_nvidia_output_falls_through():
    assert detect_gpu(cmds({"nvidia-smi": (0, "garbage")}), "Linux", "x86_64") is None


def test_apple_silicon_uses_fraction_of_unified_memory():
    gpu = detect_gpu(cmds({"sysctl": (0, str(16 * 1024**3))}), "Darwin", "arm64")
    assert gpu.kind == "apple" and gpu.vram_gb == pytest.approx(16 * APPLE_GPU_FRACTION)


def test_amd_rocm_json_parsed():
    out = json.dumps({"card0": {"VRAM Total Memory (B)": str(8 * 1024**3)}})
    gpu = detect_gpu(cmds({"rocm-smi": (0, out)}), "Linux", "x86_64")
    assert gpu.kind == "amd" and gpu.vram_gb == pytest.approx(8.0)


def test_amd_one_bad_card_is_skipped_not_fatal():
    out = json.dumps({
        "card0": {"VRAM Total Memory (B)": "not-a-number"},
        "card1": {"VRAM Total Memory (B)": str(16 * 1024**3)},
        "system": "driver 6.1",
        "card2": {},
    })
    gpu = detect_gpu(cmds({"rocm-smi": (0, out)}), "Linux", "x86_64")
    assert gpu.name == "card1" and gpu.vram_gb == pytest.approx(16.0)


def test_nvidia_fails_then_amd_used():
    out = json.dumps({"card0": {"VRAM Total Memory (B)": str(8 * 1024**3)}})
    run = cmds({"nvidia-smi": (9, "boom"), "rocm-smi": (0, out)})
    assert detect_gpu(run, "Linux", "x86_64").kind == "amd"


def test_no_tools_no_gpu():
    assert detect_gpu(cmds({}), "Linux", "x86_64") is None


def test_system_cmd_missing_binary_is_127():
    assert system_cmd(["definitely-not-a-real-binary-xyz"], 1.0) == (127, "")


def test_system_cmd_timeout_is_124(monkeypatch):
    def slow(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(subprocess, "run", slow)
    assert system_cmd(["anything"], 0.1) == (124, "")


@respx.mock
def test_ollama_tags_lists_model_names():
    respx.get("http://x:11434/api/tags").respond(json={"models": [{"name": "qwen3:14b"}, {"name": "a:1b"}]})
    assert ollama_tags("http://x:11434") == ["qwen3:14b", "a:1b"]


@respx.mock
def test_ollama_tags_empty_server_is_empty_list():
    respx.get("http://x:11434/api/tags").respond(json={})
    assert ollama_tags("http://x:11434") == []


@respx.mock
def test_ollama_tags_none_when_down_or_garbled():
    respx.get("http://down:11434/api/tags").mock(side_effect=httpx.ConnectError("refused"))
    assert ollama_tags("http://down:11434") is None
    respx.get("http://err:11434/api/tags").respond(500)
    assert ollama_tags("http://err:11434") is None
    respx.get("http://bad:11434/api/tags").respond(text="not json")
    assert ollama_tags("http://bad:11434") is None
    respx.get("http://odd:11434/api/tags").respond(json={"models": [{"nope": 1}]})
    assert ollama_tags("http://odd:11434") is None


def test_detect_machine_with_fakes(tmp_path):
    run = cmds({"nvidia-smi": (0, "RTX, 8192\n")})
    cfg = Config(vault_path=tmp_path)
    m = detect_machine(cfg, run=run, which=lambda n: "/bin/ollama" if n == "ollama" else None,
                       tags=lambda url: ["qwen3:8b"], claude_logged_in=lambda p: True,
                       find=lambda recorded: "/x/claude")
    assert m.gpu.vram_gb == pytest.approx(8.0)
    assert m.ollama_installed and m.ollama_running and m.ollama_models == ["qwen3:8b"]
    assert m.claude_path == "/x/claude" and m.claude_logged_in


def test_detect_machine_nothing_installed(tmp_path):
    m = detect_machine(Config(vault_path=tmp_path), run=cmds({}), which=lambda n: None,
                       tags=lambda url: None, claude_logged_in=lambda p: True, find=lambda r: None)
    assert not m.ollama_installed and not m.ollama_running and m.ollama_models == []
    assert m.claude_path is None and not m.claude_logged_in


def test_recommend_gpu_with_ollama_no_models_offers_pull():
    rec = recommend(info(gpu=Gpu("x", 8.0, "nvidia"), ollama_installed=True), TABLE)
    assert rec.backend == "ollama" and rec.pull.tag == "small:8b" and not rec.install_ollama
    assert rec.alternatives == ["claude-code", "none"]


def test_recommend_picks_largest_fitting():
    rec = recommend(info(gpu=Gpu("x", 16.0, "nvidia"), ollama_installed=True), TABLE)
    assert rec.pull.tag == "mid:14b"


def test_recommend_gpu_with_models_pulls_nothing_and_offers_claude():
    rec = recommend(info(gpu=Gpu("x", 16.0, "nvidia"), ollama_installed=True, ollama_models=["a"],
                         claude_logged_in=True), TABLE)
    assert rec.pull is None and rec.alternatives == ["claude-code", "none"]


def test_embedding_only_models_do_not_suppress_the_pull():
    gpu = Gpu("x", 16.0, "nvidia")
    only_embed = recommend(info(gpu=gpu, ollama_installed=True, ollama_models=["nomic-embed-text:latest"]), TABLE)
    assert only_embed.pull is not None
    mixed = recommend(info(gpu=gpu, ollama_installed=True, ollama_models=["nomic-embed-text", "qwen3:14b"]), TABLE)
    assert mixed.pull is None


def test_recommend_gpu_without_ollama_says_install():
    rec = recommend(info(gpu=Gpu("x", 8.0, "nvidia")), TABLE)
    assert rec.backend == "ollama" and rec.install_ollama


def test_recommend_small_gpu_with_claude_picks_claude():
    rec = recommend(info(gpu=Gpu("x", 4.0, "nvidia"), claude_logged_in=True), TABLE)
    assert rec.backend == "claude-code" and rec.alternatives == ["ollama", "none"]


def test_recommend_claude_lists_ollama_if_installed():
    rec = recommend(info(claude_logged_in=True, ollama_installed=True), TABLE)
    assert rec.alternatives == ["ollama", "none"]


def test_recommend_nothing_gives_none_with_unlock_reasons():
    rec = recommend(info(), TABLE)
    text = " ".join(rec.reasons)
    assert rec.backend == "none" and rec.pull is None
    assert "6 GB" in text and "Claude Code" in text and "CPU" in text
    assert rec.alternatives == ["ollama", "claude-code"]


def test_model_table_loads_sorted_and_nonempty():
    table = load_model_table()
    assert table and [m.min_vram_gb for m in table] == sorted(m.min_vram_gb for m in table)


def test_install_hints_cover_three_systems():
    assert set(OLLAMA_INSTALL) == {"Linux", "Darwin", "Windows"}
