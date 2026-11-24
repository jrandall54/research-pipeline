import json
import subprocess

import httpx
import pytest
import respx

from research_pipeline.catalog import Catalog, CatalogEntry
from research_pipeline.machine import (
    APPLE_GPU_FRACTION,
    OLLAMA_INSTALL,
    Gpu,
    MachineInfo,
    OllamaServer,
    detect_gpu,
    detect_machine,
    ollama_tags,
    recommend,
    system_cmd,
)
from research_pipeline.models import Config


def _entry(pull, gb, params, bands, kv=64.0):
    return CatalogEntry(id=pull, family="Test", pull=pull, arch="dense", params_b=params, quant="Q4_K_M",
                        download_gb=gb, trained_ctx=131072, kv_mib_per_1k=kv, bands=bands)


TABLE = Catalog(version=1, updated="2026-11-24", entries=[
    _entry("small:8b", 5.0, 8.0, ["cpu", "small"]),
    _entry("mid:14b", 9.0, 14.0, ["mid"]),
    _entry("big:32b", 20.0, 32.0, ["high"]),
])


NO_SERVER = lambda url, run: OllamaServer(None, "unknown", {}, [], None)  # noqa: E731


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
                       find=lambda recorded: "/x/claude", server=NO_SERVER)
    assert m.gpu.vram_gb == pytest.approx(8.0)
    assert m.ollama_installed and m.ollama_running and m.ollama_models == ["qwen3:8b"]
    assert m.claude_path == "/x/claude" and m.claude_logged_in


def test_detect_machine_nothing_installed(tmp_path):
    m = detect_machine(Config(vault_path=tmp_path), run=cmds({}), which=lambda n: None,
                       tags=lambda url: None, claude_logged_in=lambda p: True, find=lambda r: None, server=NO_SERVER)
    assert not m.ollama_installed and not m.ollama_running and m.ollama_models == []
    assert m.claude_path is None and not m.claude_logged_in


def test_recommend_gpu_with_ollama_no_models_offers_pull():
    rec = recommend(info(gpu=Gpu("x", 8.0, "nvidia"), ollama_installed=True), TABLE)
    assert rec.backend == "ollama" and rec.pull.pull == "small:8b" and not rec.install_ollama
    assert rec.alternatives == ["claude-code", "none"]


def test_recommend_picks_top_ranked_for_the_band():
    rec = recommend(info(gpu=Gpu("x", 16.0, "nvidia"), ollama_installed=True), TABLE)
    assert rec.pull.pull == "mid:14b"


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


def test_recommend_cpu_only_with_claude_picks_claude():
    rec = recommend(info(claude_logged_in=True, ollama_installed=True), TABLE)
    assert rec.backend == "claude-code" and rec.alternatives == ["ollama", "none"]


def test_recommend_cpu_only_without_cloud_offers_a_cpu_model():
    rec = recommend(info(ram_gb=16.0, ollama_installed=True), TABLE)
    assert rec.backend == "ollama" and rec.pull.pull == "small:8b"
    assert rec.reasons == ["no GPU found; a small model runs on the CPU overnight (slow)"]


def test_recommend_nothing_gives_none_with_unlock_reasons():
    rec = recommend(info(ram_gb=4.0), TABLE)
    text = " ".join(rec.reasons)
    assert rec.backend == "none" and rec.pull is None
    assert "GPU" in text and "Claude Code" in text and "CPU" in text
    assert rec.alternatives == ["ollama", "claude-code"]


def test_install_hints_cover_three_systems():
    assert set(OLLAMA_INSTALL) == {"Linux", "Darwin", "Windows"}
