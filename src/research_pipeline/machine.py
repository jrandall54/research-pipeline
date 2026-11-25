"""Look at this computer and recommend a summary backend (used by `rpipe model setup`).

Every probe goes through an injected command runner, so tests never run a real tool.
"""
from __future__ import annotations

import platform
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import httpx

from research_pipeline.backends.claude_code import find_claude, logged_in
from research_pipeline.catalog import Catalog, CatalogEntry
from research_pipeline.models import Config
from research_pipeline.scan import (  # re-exported: other modules and tests import these from here
    APPLE_GPU_FRACTION,
    GIB,
    PROBE_TIMEOUT,
    CmdRunner,
    CpuInfo,
    Gpu,
    OllamaServer,
    all_gpus,
    best_gpu,
    detect_cpu,
    detect_ollama_server,
    disk_free_gb,
    ollama_models_dir,
    ram_free_gb,
    system_cmd,
)

__all__ = ["APPLE_GPU_FRACTION", "GIB", "PROBE_TIMEOUT", "CmdRunner", "Gpu", "system_cmd"]

OLLAMA_INSTALL = {
    "Linux": "curl -fsSL https://ollama.com/install.sh | sh",
    "Darwin": "brew install ollama (or download it from https://ollama.com/download)",
    "Windows": "download it from https://ollama.com/download",
}


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
    gpus: list[Gpu] = field(default_factory=list)  # every card; `gpu` is the best one
    cpu: CpuInfo | None = None
    ram_free_gb: float | None = None
    disk_free_gb: float | None = None  # free space where Ollama stores models
    ollama: OllamaServer | None = None


@dataclass
class Recommendation:
    backend: Literal["ollama", "claude-code", "none"]
    pull: CatalogEntry | None  # a catalog model to download, if any
    install_ollama: bool
    alternatives: list[str]
    reasons: list[str]


def ollama_tags(url: str) -> list[str] | None:
    """Names of pulled models, or None if the server does not answer within PROBE_TIMEOUT."""
    try:
        resp = httpx.get(f"{url}/api/tags", timeout=PROBE_TIMEOUT)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return None


def detect_gpu(run: CmdRunner, system: str, arch: str) -> Gpu | None:
    """The best GPU found, trying NVIDIA, AMD, then Apple Silicon. A failing probe is skipped."""
    return best_gpu(run, system, arch)


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
    server: Callable[[str, CmdRunner], OllamaServer] = detect_ollama_server,
) -> MachineInfo:
    system = platform.system()
    served = tags(config.llm_url)
    claude = find(config.llm_cli_path)
    arch = platform.machine()
    gpus = all_gpus(run, system, arch)
    ollama = server(config.llm_url, run)
    return MachineInfo(
        os=system,
        ram_gb=_ram_gb(run, system),
        gpu=detect_gpu(run, system, arch),
        ollama_installed=which("ollama") is not None or served is not None,
        ollama_running=served is not None,
        ollama_models=served or [],
        claude_path=claude,
        claude_logged_in=bool(claude) and claude_logged_in(claude),
        gpus=gpus,
        cpu=detect_cpu(run, system),
        ram_free_gb=ram_free_gb(system),
        disk_free_gb=disk_free_gb(ollama_models_dir(ollama)),
        ollama=ollama,
    )


BUILTIN_BACKENDS: tuple[Literal["ollama", "claude-code", "none"], ...] = ("ollama", "claude-code", "none")


def _generation_models(names: list[str]) -> list[str]:
    """Pulled models that can write text: embedding-only models (an "embed" in the name) do not count."""
    return [n for n in names if "embed" not in n.lower()]


def _others(first: str) -> list[str]:
    """The other built-in backends, so the menu always offers all three."""
    return [b for b in BUILTIN_BACKENDS if b != first]


def recommend(info: MachineInfo, catalog: Catalog) -> Recommendation:
    """Pick the best backend for this machine. The menu always lists all three built-ins
    (`alternatives`); setup re-checks a choice that is not installed or logged in yet.

    1. A catalog model fits on the GPU (fully, or a mixture of experts partly in RAM): `ollama`.
    2. Otherwise a Claude Code login: `claude-code`.
    3. Otherwise a model fits in RAM on the CPU: `ollama` (slow but fine overnight).
    4. Otherwise `none`.
    With `ollama` and no text-writing model installed yet, `pull` is the top-ranked model."""
    from research_pipeline.recommender import rank, total_vram  # recommender imports this module

    ranked = rank(info, catalog, {})
    pull = ranked[0].entry if ranked and not _generation_models(info.ollama_models) else None
    install = not info.ollama_installed
    if ranked and ranked[0].placement in ("gpu", "spill"):
        vram = total_vram(info)
        who = (f"{len(info.gpus)} GPUs together have" if len(info.gpus) > 1
               else f"{info.gpu.name} has" if info.gpu is not None else "the GPU has")
        reasons = [f"{who} {vram:.1f} GB of GPU memory, enough to run a model locally"]
        return Recommendation("ollama", pull, install, _others("ollama"), reasons)
    gpu = info.gpu
    # "free now", not the card's size: a big card that is busy (a game, a loaded model) also lands here
    no_fit = "no catalog model fits in the GPU memory free now"
    if info.claude_logged_in:
        why = "no GPU was found" if gpu is None else no_fit
        return Recommendation("claude-code", None, False, _others("claude-code"),
                              [f"{why}, but you are logged in to Claude Code"])
    if ranked:
        where = "no GPU found" if gpu is None else no_fit
        return Recommendation("ollama", pull, install, _others("ollama"),
                              [f"{where}; a small model runs on the CPU overnight (slow)"])
    return Recommendation("none", None, False, _others("none"), [
        "no model in the catalog fits this computer's GPU memory or its memory (RAM), "
        "and there is no Claude Code login",
        "a bigger GPU plus Ollama, more memory for a model on the CPU, or a Claude Code login would unlock summaries",
    ])
