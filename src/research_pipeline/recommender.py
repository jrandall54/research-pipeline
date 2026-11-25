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
from research_pipeline.scan import flash_on

Placement = Literal["gpu", "spill", "cpu"]
# Ollama leaves part of the GPU's free memory unused (its own buffers and a safety margin).
# Measured on the owner's 16 GB RTX 5070 Ti on 2026-11-24 with /api/ps: with 15.0 GiB free, the
# largest load ever placed on the GPU was about 13.0 GiB, so about 2.0 GiB stays unused.
OLLAMA_RESERVE_GIB = 2.0
UNKNOWN_FREE_MARGIN_GIB = 0.5  # when free GPU memory cannot be read: assume the desktop holds this much
GIB_PER_GB = 1e9 / 2**30       # catalog sizes are GB (10^9 bytes); GPU memory is read in GiB (2^30)
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


def usable_vram(info: MachineInfo) -> float:
    """GPU memory (GiB) a model can use: free memory at scan time (else total - 0.5), minus
    OLLAMA_RESERVE_GIB, never below 0."""
    gpus = info.gpus or ([info.gpu] if info.gpu is not None else [])
    if not gpus:
        return 0.0
    if all(g.free_gb is not None for g in gpus):
        free = sum(g.free_gb or 0.0 for g in gpus)
    else:
        free = total_vram(info) - UNKNOWN_FREE_MARGIN_GIB
    return max(0.0, free - OLLAMA_RESERVE_GIB)


def weights_gib(entry: CatalogEntry) -> float:
    """Memory the model's weights take, GiB: the download size (GB) converted to GiB."""
    return entry.download_gb * GIB_PER_GB


def kv_factor(env: dict[str, str]) -> float:
    """How much smaller the KV cache is than the default 16-bit one, given the server settings.

    A smaller KV cache type only takes effect with flash attention on."""
    flash = flash_on(env.get("OLLAMA_FLASH_ATTENTION"))
    kv_type = env.get("OLLAMA_KV_CACHE_TYPE", "").strip().lower()
    if flash and kv_type == "q8_0":
        return 0.5
    if flash and kv_type == "q4_0":
        return 0.25
    return 1.0


def server_kv_factor(info: MachineInfo) -> float:
    """kv_factor for the server rpipe talks to. When the service rpipe can read listens on another
    port (host_matches False), its settings say nothing about rpipe's server: assume the default, 1."""
    if info.ollama is None or info.ollama.host_matches is False:
        return 1.0
    return kv_factor(info.ollama.env)


def _round_ctx(n: float) -> int:
    return int(n) // 1024 * 1024


def place(entry: CatalogEntry, info: MachineInfo, allow_cpu: bool | None = None) -> tuple[Placement, int] | None:
    """Where `entry` would run on this machine and with how much context, or None if nowhere.

    All sizes in GiB.
    1. `vram` = usable_vram (free GPU memory at scan time, else total - 0.5, minus the 2 GiB
       Ollama leaves unused); weights = download_gb converted to GiB; KV cache per token =
       kv_mib_per_1k x server_kv_factor / 1000 / 1024.
    2. GPU: if vram > weights, the context is what the rest of vram holds
       (floor((vram - weights) / kv per token)), rounded down to 1024, capped by the
       model's trained context and CONTEXT_CEILING. At least MIN_USEFUL_CTX -> ("gpu", ctx).
    3. Spill (mixture-of-experts models only, on a machine with a GPU band): if the weights plus
       a CPU_CTX context fit in vram + free RAM - RAM_SAFETY_GB -> ("spill", CPU_CTX).
    4. CPU (when `allow_cpu` is None: only machines in the "cpu" or "small" band, i.e. under 8 GB
       of GPU memory; True allows it on any machine, False on none): if the active parameters
       (all parameters for a dense model) are at most CPU_MAX_ACTIVE_B and the weights plus a
       CPU_CTX context fit in total RAM - RAM_SAFETY_GB -> ("cpu", CPU_CTX). `rank` passes True
       when no catalog model fits on the GPU, so a big card with little free memory still gets options.
    5. Otherwise None.
    """
    vram = usable_vram(info)
    weights = weights_gib(entry)
    kv_gib_per_tok = entry.kv_mib_per_1k * server_kv_factor(info) / 1000 / 1024
    if vram > weights:
        room = (vram - weights) / kv_gib_per_tok if kv_gib_per_tok > 0 else math.inf
        ctx = _round_ctx(min(room, entry.trained_ctx, CONTEXT_CEILING))
        if ctx >= MIN_USEFUL_CTX:
            return "gpu", ctx
    need = weights + CPU_CTX * kv_gib_per_tok
    band = band_for(info)
    if entry.arch == "moe" and band != "cpu":
        if need <= vram + (info.ram_free_gb or 0) - RAM_SAFETY_GB:
            return "spill", CPU_CTX
    cpu_ok = band in ("cpu", "small") if allow_cpu is None else allow_cpu
    if cpu_ok and info.ram_gb is not None:
        if (entry.active_b or entry.params_b) <= CPU_MAX_ACTIVE_B and need <= info.ram_gb - RAM_SAFETY_GB:
            return "cpu", CPU_CTX
    return None


def dense_equivalent_b(entry: CatalogEntry) -> float:
    """Roughly how big a dense model this one writes like, in billions of parameters.

    Dense: its own size. Mixture of experts: sqrt(total x active), a common rule of thumb
    (35B with 3B active is about 10.2B). The owner's 10-paper test agreed: a 35B-A3B model
    covered fewer key facts than a 27B dense one. Measuring with `rpipe eval` stays the final word."""
    if entry.arch == "moe" and entry.active_b:
        return math.sqrt(entry.params_b * entry.active_b)
    return entry.params_b


def _norm(name: str) -> str:
    """`name` -> `name:latest` when there is no tag, as Ollama does (see ollama_fit._same_model)."""
    return name if ":" in name else f"{name}:latest"


def _installed(info: MachineInfo) -> set[str]:
    return {_norm(n) for n in _generation_models(info.ollama_models)}


def _placement_reason(entry: CatalogEntry, placement: Placement, ctx: int, info: MachineInfo) -> str:
    if placement == "gpu":
        return (f"fits fully in the {usable_vram(info):.1f} GB of GPU memory free for models with about "
                f"{ctx / 1000:.0f}K tokens of context (estimate; rpipe model fit measures it)")
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


def rank(info: MachineInfo, catalog: Catalog, benches: dict[str, Bench], top: int | None = TOP_N) -> list[Candidate]:
    """The best `top` catalog models for this machine (all that fit when `top` is None).

    Order: on the GPU, then partly in RAM (spill), then on the CPU; within each, models listed
    for this machine's band first, then bigger models by dense-equivalent size (quality first),
    then the bigger download (a higher-quality quantization of the same model), then installed ones.

    CPU choices normally appear only on machines under 8 GB of GPU memory. When no catalog model
    fits on the GPU (say an 8 GB card with most of its memory taken), every model is placed again
    with CPU allowed, so the machine still gets options."""
    band = band_for(info)
    installed = _installed(info)
    measured = {_norm(name): b for name, b in benches.items()}
    places = [(entry, place(entry, info)) for entry in catalog.entries]
    if not any(where is not None and where[0] == "gpu" for _, where in places):
        places = [(entry, place(entry, info, allow_cpu=True)) for entry in catalog.entries]
    out: list[Candidate] = []
    for entry, where in places:
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
    out.sort(key=lambda c: (_ORDER[c.placement], band not in c.entry.bands, -dense_equivalent_b(c.entry),
                            -c.entry.download_gb, not c.installed))
    return out if top is None else out[:top]


def uncatalogued(info: MachineInfo, catalog: Catalog) -> list[str]:
    """Installed text-writing models the catalog does not list (embedding models are left out)."""
    known = {_norm(e.pull) for e in catalog.entries}
    return [n for n in _generation_models(info.ollama_models) if _norm(n) not in known]
