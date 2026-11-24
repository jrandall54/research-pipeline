"""Rank catalog models for this machine: where each would run, how much context it gets, and why.

Pure arithmetic on the scan (`MachineInfo`) and the catalog: nothing is loaded, downloaded or
measured here. `rpipe model recommend` and the setup walkthrough print what this returns.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from research_pipeline.bench import Bench
from research_pipeline.catalog import Band, Catalog, CatalogEntry
from research_pipeline.machine import MachineInfo, _generation_models
from research_pipeline.ollama_fit import CONTEXT_CEILING

Placement = Literal["gpu", "spill", "cpu"]
GPU_OVERHEAD_GB = 1.0        # runtime buffers outside weights and KV cache
RAM_SAFETY_GB = 4.0          # never plan to use the last 4 GB of RAM
MIN_USEFUL_CTX = 16384       # same as ollama_fit.choose's min_useful
CPU_CTX = 32768              # context pinned for CPU and spill choices (median paper ~22K tokens)
CPU_MAX_ACTIVE_B = 15.0      # CPU tier: models above this many active billions are too slow for a night
TOP_N = 5

_ORDER: dict[str, int] = {"gpu": 0, "spill": 1, "cpu": 2}


@dataclass
class Candidate:
    entry: CatalogEntry
    placement: Placement
    est_ctx: int              # estimated context (tokens), multiple of 1024, <= trained_ctx and CONTEXT_CEILING
    installed: bool
    reasons: list[str]        # plain sentences
    bench: Bench | None       # measured on this machine, if any


def total_vram(info: MachineInfo) -> float:
    """GPU memory of every card together (GB); the best card alone when only it is known."""
    if info.gpus:
        return sum(g.vram_gb for g in info.gpus)
    return info.gpu.vram_gb if info.gpu is not None else 0.0


def band_for(info: MachineInfo) -> Band:
    vram = total_vram(info)
    if vram < 4:
        return "cpu"
    if vram < 8:
        return "small"
    if vram <= 16.5:
        return "mid"
    if vram <= 24.5:
        return "high"
    return "very_large"


def kv_factor(env: dict[str, str]) -> float:
    """How much smaller the KV cache is than the default 16-bit one, given the server settings.

    A smaller KV cache type only takes effect with flash attention on."""
    flash = env.get("OLLAMA_FLASH_ATTENTION", "").strip().lower() in ("1", "true")
    kv_type = env.get("OLLAMA_KV_CACHE_TYPE", "").strip().lower()
    if flash and kv_type == "q8_0":
        return 0.5
    if flash and kv_type == "q4_0":
        return 0.25
    return 1.0


def _env(info: MachineInfo) -> dict[str, str]:
    return info.ollama.env if info.ollama is not None else {}


def _round_ctx(n: float) -> int:
    return int(n) // 1024 * 1024


def place(entry: CatalogEntry, info: MachineInfo) -> tuple[Placement, int] | None:
    """Where `entry` would run on this machine and with how much context, or None if nowhere.

    1. `vram` = total VRAM - GPU_OVERHEAD_GB; KV cache per token (GB) =
       kv_mib_per_1k x kv_factor / 1000 / 1024.
    2. GPU: if vram > download_gb, the context is what the rest of vram holds
       (floor((vram - download_gb) / kv per token)), rounded down to 1024, capped by the
       model's trained context and CONTEXT_CEILING. At least MIN_USEFUL_CTX -> ("gpu", ctx).
    3. Spill (mixture-of-experts models only, on a machine with a GPU band): if the weights plus
       a CPU_CTX context fit in vram + free RAM - RAM_SAFETY_GB -> ("spill", CPU_CTX).
    4. CPU (machines in the "cpu" band): if the active parameters (all parameters for a dense
       model) are at most CPU_MAX_ACTIVE_B and the weights plus a CPU_CTX context fit in
       total RAM - RAM_SAFETY_GB -> ("cpu", CPU_CTX).
    5. Otherwise None.
    """
    vram = total_vram(info) - GPU_OVERHEAD_GB
    kv_gb_per_tok = entry.kv_mib_per_1k * kv_factor(_env(info)) / 1000 / 1024
    if vram > entry.download_gb:
        room = (vram - entry.download_gb) / kv_gb_per_tok if kv_gb_per_tok > 0 else math.inf
        ctx = _round_ctx(min(room, entry.trained_ctx, CONTEXT_CEILING))
        if ctx >= MIN_USEFUL_CTX:
            return "gpu", ctx
    need = entry.download_gb + CPU_CTX * kv_gb_per_tok
    band = band_for(info)
    if entry.arch == "moe" and band != "cpu":
        if need <= vram + (info.ram_free_gb or 0) - RAM_SAFETY_GB:
            return "spill", CPU_CTX
    if band == "cpu" and info.ram_gb is not None:
        if (entry.active_b or entry.params_b) <= CPU_MAX_ACTIVE_B and need <= info.ram_gb - RAM_SAFETY_GB:
            return "cpu", CPU_CTX
    return None


def _norm(name: str) -> str:
    """`name` -> `name:latest` when there is no tag, as Ollama does (see ollama_fit._same_model)."""
    return name if ":" in name else f"{name}:latest"


def _installed(info: MachineInfo) -> set[str]:
    return {_norm(n) for n in _generation_models(info.ollama_models)}


def _placement_reason(entry: CatalogEntry, placement: Placement, ctx: int, info: MachineInfo) -> str:
    if placement == "gpu":
        return (f"fits fully in {total_vram(info):.1f} GB of GPU memory with about {ctx / 1000:.0f}K tokens "
                "of context (estimate; rpipe model fit measures it)")
    if placement == "spill":
        active = f"{entry.active_b:g}B" if entry.active_b else "a few billion"
        return (f"mixture of experts: {active} of {entry.params_b:g}B parameters work per token, so part of "
                "it can sit in RAM at a modest slowdown")
    return "runs on the CPU: slow but fine overnight"


def _speed_reason(bench: Bench | None) -> str:
    if bench is None:
        return "speed not measured yet: rpipe model bench"
    s = bench.seconds_per_paper
    return f"measured here: about {s:.0f} s per paper, about {s * 15 / 60:.0f} min for 15 papers"


def rank(info: MachineInfo, catalog: Catalog, benches: dict[str, Bench]) -> list[Candidate]:
    """The best TOP_N catalog models for this machine.

    Order: on the GPU, then partly in RAM (spill), then on the CPU; within each, models listed
    for this machine's band first, then bigger models (quality first), then installed ones."""
    band = band_for(info)
    installed = _installed(info)
    measured = {_norm(name): b for name, b in benches.items()}
    out: list[Candidate] = []
    for entry in catalog.entries:
        where = place(entry, info)
        if where is None:
            continue
        placement, ctx = where
        is_installed = _norm(entry.pull) in installed
        bench = measured.get(_norm(entry.pull))
        reasons = [_placement_reason(entry, placement, ctx, info)]
        if placement != "gpu":
            reasons.append(f"the context is set to {ctx:,} tokens, enough for a typical paper")
        reasons.append("installed" if is_installed else f"download {entry.download_gb:g} GB")
        if entry.notes:
            reasons.append(entry.notes)
        reasons.append(_speed_reason(bench))
        out.append(Candidate(entry, placement, ctx, is_installed, reasons, bench))
    out.sort(key=lambda c: (_ORDER[c.placement], band not in c.entry.bands, -c.entry.params_b, not c.installed))
    return out[:TOP_N]


def uncatalogued(info: MachineInfo, catalog: Catalog) -> list[str]:
    """Installed text-writing models the catalog does not list (embedding models are left out)."""
    known = {_norm(e.pull) for e in catalog.entries}
    return [n for n in _generation_models(info.ollama_models) if _norm(n) not in known]
