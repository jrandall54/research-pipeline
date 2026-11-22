"""Look at this computer and recommend a summary backend (used by `rpipe model setup`).

Every probe goes through an injected command runner, so tests never run a real tool.
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from typing import Literal

import httpx
import yaml

from research_pipeline.backends.claude_code import find_claude, logged_in
from research_pipeline.models import Config

MIN_GPU_VRAM_GB = 6.0
# Apple Silicon shares one memory pool between CPU and GPU, and macOS lets the GPU use roughly
# two thirds of it by default, so only that share is counted as GPU memory.
APPLE_GPU_FRACTION = 0.66
PROBE_TIMEOUT = 5.0
GIB = 1024**3

CmdRunner = Callable[[list[str], float], tuple[int, str]]

OLLAMA_INSTALL = {
    "Linux": "curl -fsSL https://ollama.com/install.sh | sh",
    "Darwin": "brew install ollama (or download it from https://ollama.com/download)",
    "Windows": "download it from https://ollama.com/download",
}


@dataclass
class Gpu:
    name: str
    vram_gb: float
    kind: Literal["nvidia", "amd", "apple"]


@dataclass
class MachineInfo:
    os: str
    ram_gb: float | None
    gpu: Gpu | None
    ollama_installed: bool
    ollama_running: bool
    ollama_models: list[str]
    claude_path: str | None
    claude_logged_in: bool


@dataclass(frozen=True)
class ModelChoice:
    min_vram_gb: float
    tag: str
    download_gb: float


@dataclass
class Recommendation:
    backend: Literal["ollama", "claude-code", "none"]
    pull: ModelChoice | None
    install_ollama: bool
    alternatives: list[str]
    reasons: list[str]


def system_cmd(argv: list[str], timeout: float) -> tuple[int, str]:
    """Run a command; (127, "") if the binary is missing, (124, "") on timeout. Never raises."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError:
        return 127, ""
    return done.returncode, done.stdout


def ollama_tags(url: str) -> list[str] | None:
    """Names of pulled models, or None if the server does not answer within PROBE_TIMEOUT."""
    try:
        resp = httpx.get(f"{url}/api/tags", timeout=PROBE_TIMEOUT)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _nvidia(run: CmdRunner) -> Gpu | None:
    code, out = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                    PROBE_TIMEOUT)
    if code != 0:
        return None
    best: Gpu | None = None
    for line in out.splitlines():
        name, _, mem = line.rpartition(",")
        try:
            gb = float(mem.strip()) / 1024  # MiB -> GiB
        except ValueError:
            continue
        if name.strip() and (best is None or gb > best.vram_gb):
            best = Gpu(name.strip(), gb, "nvidia")
    return best


def _amd(run: CmdRunner) -> Gpu | None:
    code, out = run(["rocm-smi", "--showmeminfo", "vram", "--json"], PROBE_TIMEOUT)
    if code != 0:
        return None
    try:
        data = json.loads(out)
        items = list(data.items())
    except (ValueError, AttributeError):
        return None
    best: Gpu | None = None
    for card, vals in items:
        try:
            gb = int(vals["VRAM Total Memory (B)"]) / GIB
        except (ValueError, KeyError, TypeError):
            continue  # one unreadable card (or a non-card key) must not hide the others
        if best is None or gb > best.vram_gb:
            best = Gpu(card, gb, "amd")
    return best


def _apple(run: CmdRunner) -> Gpu | None:
    code, out = run(["sysctl", "-n", "hw.memsize"], PROBE_TIMEOUT)
    try:
        total = int(out.strip())
    except ValueError:
        return None
    if code != 0:
        return None
    return Gpu("Apple Silicon (unified memory)", total / GIB * APPLE_GPU_FRACTION, "apple")


def detect_gpu(run: CmdRunner, system: str, arch: str) -> Gpu | None:
    """The best GPU found, trying NVIDIA, AMD, then Apple Silicon. A failing probe is skipped."""
    probes: list[Callable[[CmdRunner], Gpu | None]] = [_nvidia, _amd]
    if system == "Darwin" and arch == "arm64":
        probes.append(_apple)
    for probe in probes:
        gpu = probe(run)
        if gpu is not None:
            return gpu
    return None


def _ram_gb(run: CmdRunner, system: str) -> float | None:
    try:
        if system == "Linux":
            with open("/proc/meminfo", encoding="utf-8") as f:
                kb = int(f.readline().split()[1])
            return kb * 1024 / GIB
        if system == "Darwin":
            code, out = run(["sysctl", "-n", "hw.memsize"], PROBE_TIMEOUT)
            return int(out.strip()) / GIB if code == 0 else None
    except (OSError, ValueError, IndexError):
        pass
    return None


def detect_machine(
    config: Config,
    run: CmdRunner = system_cmd,
    which: Callable[[str], str | None] = shutil.which,
    tags: Callable[[str], list[str] | None] = ollama_tags,
    claude_logged_in: Callable[[str], bool] = logged_in,
    find: Callable[[str | None], str | None] = find_claude,
) -> MachineInfo:
    system = platform.system()
    served = tags(config.llm_url)
    claude = find(config.llm_cli_path)
    return MachineInfo(
        os=system,
        ram_gb=_ram_gb(run, system),
        gpu=detect_gpu(run, system, platform.machine()),
        ollama_installed=which("ollama") is not None or served is not None,
        ollama_running=served is not None,
        ollama_models=served or [],
        claude_path=claude,
        claude_logged_in=bool(claude) and claude_logged_in(claude),
    )


def load_model_table() -> list[ModelChoice]:
    text = resources.files("research_pipeline.defaults").joinpath("ollama_models.yaml").read_text("utf-8")
    rows = [ModelChoice(float(r["min_vram_gb"]), str(r["tag"]), float(r["download_gb"]))
            for r in yaml.safe_load(text)]
    return sorted(rows, key=lambda m: m.min_vram_gb)


BUILTIN_BACKENDS: tuple[Literal["ollama", "claude-code", "none"], ...] = ("ollama", "claude-code", "none")


def _generation_models(names: list[str]) -> list[str]:
    """Pulled models that can write text: embedding-only models (an "embed" in the name) do not count."""
    return [n for n in names if "embed" not in n.lower()]


def _others(first: str) -> list[str]:
    """The other built-in backends, so the menu always offers all three."""
    return [b for b in BUILTIN_BACKENDS if b != first]


def recommend(info: MachineInfo, table: list[ModelChoice]) -> Recommendation:
    """Pick the best backend for this machine. The menu always lists all three built-ins
    (`alternatives`); setup re-checks a choice that is not installed or logged in yet."""
    gpu = info.gpu
    if gpu is not None and gpu.vram_gb >= MIN_GPU_VRAM_GB:
        fitting = [m for m in table if m.min_vram_gb <= gpu.vram_gb]
        pull = fitting[-1] if fitting and not _generation_models(info.ollama_models) else None
        reasons = [f"{gpu.name} has {gpu.vram_gb:.1f} GB of GPU memory, enough to run a model locally"]
        return Recommendation("ollama", pull, not info.ollama_installed, _others("ollama"), reasons)
    if info.claude_logged_in:
        why = ("no GPU with at least 6 GB was found" if gpu is None
               else f"the GPU has only {gpu.vram_gb:.1f} GB (6 GB needed)")
        return Recommendation("claude-code", None, False, _others("claude-code"),
                              [f"{why}, but you are logged in to Claude Code"])
    return Recommendation("none", None, False, _others("none"), [
        "no GPU with at least 6 GB of memory and no Claude Code login were found",
        "a GPU with 6 GB or more plus Ollama, or a Claude Code login, would unlock summaries",
        "a CPU-friendly pipeline is planned",
    ])
