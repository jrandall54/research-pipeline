"""Reruns: read cut papers whole at a bigger context, with part of the model in ordinary RAM.

The normal summarize pass uses a context window that fits fully in GPU memory. A paper too
long for it has its core text cut (`body_cut`) and records the context it would need
(`needed_ctx`). After the normal pass, on the local Ollama backend only, this stage reads
those papers again at a context big enough for all of the text. That only fits with part of
the model "spilled" into RAM, which is slow, so it runs within the free RAM (minus a safety
margin) and must finish by the morning deadline. Papers left over keep their cut summary and
are found again on a later night: the queue is recomputed from the stored summaries.
See docs/guide/running.md, "Reading cut papers whole: reruns".
"""

import json
import math
import platform
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from research_pipeline.bench import load_benches
from research_pipeline.db import get_fulltext, get_paper, save_summary
from research_pipeline.llm import CHECK_TIMEOUT, LlmError, LlmTimeout, OllamaClient
from research_pipeline.ollama_fit import CONTEXT_CEILING, unload
from research_pipeline.progress import Reporter, stage
from research_pipeline.prompts import PROMPT_VERSION
from research_pipeline.pruning import RESERVED_TOKENS
from research_pipeline.recommender import RAM_SAFETY_GB
from research_pipeline.scan import GIB, ram_free_gb
from research_pipeline.summarize import summarize_paper

RERUN_STEP = 8192  # rerun contexts are multiples of this, so papers of similar size share one load
SPILL_GUESS = 3.0  # a spilled model is guessed this many times slower than the benchmark said
FIRST_RERUN_MARGIN = 3600.0  # seconds: with nothing measured, the first rerun needs an hour left
MIN_CALL_TIMEOUT = 60.0  # seconds: no model call gets less than this, even near the deadline
MEASURE_CTX = 8192  # the second, small load that shows how memory grows with the context


@dataclass(frozen=True)
class Rerun:
    paper_id: str
    needed_ctx: int  # context the paper's core text needs uncut
    ran_ctx: int  # context its stored (cut) summary was written with


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def rerun_candidates(conn: sqlite3.Connection, model_cap: int) -> list[Rerun]:
    """Stored summaries that were cut and could be read whole (or at least at more context).

    A candidate has `body_cut`, a recorded `needed_ctx`, and provenance showing it ran at a
    context below both what it needs and the model's cap (one already run at the cap is not
    retried forever). Rows from before these were recorded are ignored. Sorted by needed
    context, newest digest first among equals.
    """
    rows = conn.execute(
        "SELECT s.paper_id, s.text FROM summaries s JOIN papers p ON p.id = s.paper_id"
        " WHERE s.kind = 'summary'"
        " ORDER BY p.digest_date IS NULL, p.digest_date DESC, s.paper_id"
    ).fetchall()
    out: list[Rerun] = []
    for row in rows:
        try:
            data = json.loads(row["text"])
        except ValueError:
            continue
        if not isinstance(data, dict) or data.get("body_cut") is not True:
            continue
        prov = data.get("provenance")
        needed = _int(data.get("needed_ctx"))
        ran = _int(prov.get("num_ctx")) if isinstance(prov, dict) else None
        if needed is None or ran is None or ran >= needed or ran >= model_cap:
            continue
        out.append(Rerun(row["paper_id"], needed, ran))
    out.sort(key=lambda r: r.needed_ctx)  # stable: newest digest first among equals
    return out


def buckets(reruns: list[Rerun], model_cap: int) -> list[tuple[int, list[Rerun]]]:
    """Group reruns by the context they run at: needed context rounded up to RERUN_STEP,
    at most the model's cap. One model load per group, smallest first."""
    groups: dict[int, list[Rerun]] = {}
    for r in reruns:
        ctx = min(math.ceil(r.needed_ctx / RERUN_STEP) * RERUN_STEP, model_cap)
        groups.setdefault(ctx, []).append(r)
    return [(ctx, sorted(groups[ctx], key=lambda r: r.needed_ctx)) for ctx in sorted(groups)]


def ram_part_bytes(ctx: int, num_ctx: int, s_fit: int, v_fit: int, s_8k: int) -> int:
    """Estimated bytes of the model in RAM at context `ctx`.

    Two measured loads give the growth per token: size `s_fit` at the normal context
    `num_ctx` (of which `v_fit` sat in GPU memory) and size `s_8k` at 8,192 tokens. The GPU
    part is taken to stay `v_fit`; everything above it goes to RAM.
    """
    per_token = max(0.0, (s_fit - s_8k) / (num_ctx - MEASURE_CTX))
    return round(s_fit + (ctx - num_ctx) * per_token - v_fit)


def _model_cap(url: str, model: str) -> int:
    """min(the model's trained context, CONTEXT_CEILING); CONTEXT_CEILING if it cannot be read."""
    try:
        resp = httpx.post(f"{url}/api/show", json={"model": model}, timeout=CHECK_TIMEOUT)
        info = (resp.json().get("model_info") or {}) if resp.status_code < 400 else {}
        trained = next((v for k, v in info.items()
                        if k.endswith(".context_length") and _int(v)), None)
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        trained = None
    return min(trained, CONTEXT_CEILING) if trained else CONTEXT_CEILING


def _tagged(name: str) -> str:
    last = name.rsplit("/", 1)[-1]
    return name if ":" in last else f"{name}:latest"


def _ps(url: str) -> list[dict]:
    """Ollama's list of loaded models (`/api/ps`); LlmError if it cannot be read."""
    try:
        resp = httpx.get(f"{url}/api/ps", timeout=CHECK_TIMEOUT)
        models = resp.json().get("models", []) if resp.status_code < 400 else None
    except (httpx.HTTPError, ValueError, AttributeError) as e:
        raise LlmError(f"could not ask Ollama at {url} which models are loaded ({e})") from e
    if not isinstance(models, list):
        raise LlmError(f"Ollama at {url} did not say which models are loaded")
    return [m for m in models if isinstance(m, dict)]


def _is(entry: dict, model: str) -> bool:
    return any(_tagged(str(entry.get(k))) == _tagged(model) for k in ("name", "model") if entry.get(k))


def _sizes(url: str, model: str) -> tuple[int, int]:
    """(size, size_vram) of `model` as loaded now; LlmError if it is not loaded."""
    for m in _ps(url):
        if _is(m, model):
            size, vram = _int(m.get("size")), _int(m.get("size_vram"))
            if size is not None and vram is not None:
                return size, min(vram, size)
    raise LlmError(f"{model} is not loaded after asking Ollama to load it")


def _load(url: str, model: str, ctx: int, timeout: float) -> None:
    """Load `model` at context `ctx` (an empty prompt only loads it)."""
    try:
        resp = httpx.post(f"{url}/api/generate", timeout=timeout, json={
            "model": model, "prompt": "", "keep_alive": "10m", "options": {"num_ctx": ctx}})
    except httpx.TimeoutException as e:
        raise LlmTimeout(f"loading {model} at {ctx} tokens took longer than {timeout:g}s") from e
    except httpx.HTTPError as e:
        raise LlmError(f"could not load {model} at {ctx} tokens: {e or type(e).__name__}") from e
    error = None
    try:
        error = resp.json().get("error")
    except (ValueError, AttributeError):
        pass
    if resp.status_code >= 400 or error:
        raise LlmError(f"could not load {model} at {ctx} tokens: {error or f'HTTP {resp.status_code}'}")


def _paper_tokens(r: Rerun) -> int:
    return max(1, r.needed_ctx - RESERVED_TOKENS)


def _gib(n: float) -> str:
    return f"{n / GIB:.1f} GiB"


def run_reruns(
    conn: sqlite3.Connection,
    llm: OllamaClient,
    vault: Path,
    deadline: datetime | None,
    *,
    reporter: Reporter | None = None,
    now: Callable[[], datetime] = datetime.now,
    ram_free: Callable[[], float | None] = lambda: ram_free_gb(platform.system()),
) -> tuple[dict[str, int], list[str]]:
    """Rerun cut papers at a bigger context, within free RAM and before `deadline`.

    Returns counts {"rerun": papers rerun, "rerun_waiting": candidates left for a later
    night} and error lines (`rerun ID: ...` for a paper, `rerun: ...` for the stage). A failed
    rerun keeps the old summary and changes no retry count; a timeout stops the stage.
    `now` and `ram_free` are injectable for tests. See docs/guide/running.md.
    """
    url, model, fit_ctx = llm.url, llm.model_name, llm.num_ctx
    cap = _model_cap(url, model)
    candidates = rerun_candidates(conn, cap)
    total = len(candidates)
    errors: list[str] = []
    done = 0
    if not candidates:
        return {"rerun": 0, "rerun_waiting": 0}, errors

    with stage(reporter, "rerun", total=total) as handle:
        def waiting(why: str) -> tuple[dict[str, int], list[str]]:
            handle.note(f"{total} cut paper{'s' if total != 1 else ''} wait for a later night: {why}")
            return {"rerun": 0, "rerun_waiting": total}, errors

        if deadline is None:
            return waiting("no deadline tonight (reruns only start within 12 hours of the "
                           "morning deadline; see `rpipe schedule show`)")
        if fit_ctx <= MEASURE_CTX:
            return waiting(f"the normal context ({fit_ctx} tokens) is too small to measure how "
                           "memory grows with the context")
        if ram_free() is None:
            return waiting("free RAM cannot be read on this system, so a spill could not be kept safe")

        order = [r for _, group in buckets(candidates, cap) for r in group]
        handled: set[str] = set()
        loaded = False
        s_per_token: float | None = None
        bench = load_benches(vault).get(model)
        if bench is not None and bench.paper_tokens > 0:
            s_per_token = bench.seconds_per_paper / bench.paper_tokens * SPILL_GUESS

        def left() -> float:
            return (deadline - now()).total_seconds()

        def time_ok(r: Rerun) -> bool:
            if s_per_token is None:
                return left() >= FIRST_RERUN_MARGIN
            return _paper_tokens(r) * s_per_token <= left()

        if not time_ok(order[0]):
            return waiting(f"not enough time before {deadline:%H:%M} for even the smallest rerun")
        try:
            busy = [str(m.get("name") or m.get("model")) for m in _ps(url) if not _is(m, model)]
            if busy:
                return waiting(f"GPU busy: {', '.join(busy)} is loaded in Ollama")

            handle.note(f"measuring memory use of {model} at {fit_ctx} and {MEASURE_CTX} tokens")
            loaded = True
            _load(url, model, fit_ctx, max(MIN_CALL_TIMEOUT, left()))
            s_fit, v_fit = _sizes(url, model)
            _load(url, model, MEASURE_CTX, max(MIN_CALL_TIMEOUT, left()))
            s_8k, v_now = _sizes(url, model)
            in_ram = s_8k - v_now

            index = 0
            stop = False
            for ctx, group in buckets(candidates, cap):
                first = group[0]
                if not time_ok(first):
                    handle.note(f"not enough time before {deadline:%H:%M} for the next rerun")
                    break
                free = ram_free()
                if free is None:
                    handle.note("free RAM could not be read; stopping reruns")
                    break
                allowed = (free - RAM_SAFETY_GB) * GIB + in_ram
                need = ram_part_bytes(ctx, fit_ctx, s_fit, v_fit, s_8k)
                if need > allowed:
                    handle.note(f"a context of {ctx} tokens needs about {_gib(need)} of RAM, "
                                f"only {_gib(max(0, allowed))} can be spared; stopping reruns")
                    break
                handle.note(f"loading {model} at {ctx} tokens ({_gib(max(0, need))} in RAM)")
                _load(url, model, ctx, max(MIN_CALL_TIMEOUT, left()))
                size, vram = _sizes(url, model)
                in_ram = size - vram
                if in_ram > allowed:
                    handle.note(f"the model put {_gib(in_ram)} in RAM at {ctx} tokens, more than "
                                f"the {_gib(max(0, allowed))} allowed; unloading and stopping reruns")
                    unload(url, model, handle.note)
                    loaded = False
                    break
                for r in group:
                    if not time_ok(r):
                        handle.note(f"not enough time before {deadline:%H:%M} for the next rerun")
                        stop = True
                        break
                    index += 1
                    handled.add(r.paper_id)
                    started = now()
                    try:
                        paper = get_paper(conn, r.paper_id)
                        sections = get_fulltext(conn, r.paper_id)
                        if paper is None or sections is None:
                            raise LlmError("no stored full text")
                        client = OllamaClient(url, model, ctx,
                                              timeout=max(MIN_CALL_TIMEOUT, left()))

                        def on_phase(name: str) -> None:
                            what = {"summary": "writing the summary for",
                                    "critique": "writing the critique for"}
                            handle.note(f"{what.get(name, 'retrying citations for')} {r.paper_id}")  # noqa: B023

                        result = summarize_paper(client, paper["title"], paper["abstract"],
                                                 sections, ctx, on_phase=on_phase)
                        save_summary(conn, r.paper_id, result, model,
                                     prompt_version=PROMPT_VERSION, paper_version=paper["version"],
                                     num_ctx=ctx, backend=client.name,
                                     gpu_share=client.gpu_share())
                        conn.commit()
                    except Exception as exc:  # noqa: BLE001 - one bad paper keeps its old summary
                        msg = " ".join((str(exc) if isinstance(exc, LlmError)
                                        else f"{type(exc).__name__}: {exc}").split())
                        errors.append(f"rerun {r.paper_id}: {msg}")
                        handle.step(r.paper_id, current=index, outcome="failed", detail=msg[:120])
                        if isinstance(exc, LlmTimeout):
                            handle.note("stopped reruns after a timeout")
                            stop = True
                            break
                        continue
                    took = (now() - started).total_seconds()
                    s_per_token = took / _paper_tokens(r)
                    done += 1
                    handle.step(r.paper_id, current=index, outcome="done",
                                detail=f"{ctx} tokens, {took:.0f}s")
                if stop:
                    break
        except LlmError as exc:
            errors.append(f"rerun: {' '.join(str(exc).split())}")
            handle.note(f"stopped reruns: {exc}")
        finally:
            if loaded:
                unload(url, model, handle.note)
        index = len(handled)
        for r in order:
            if r.paper_id not in handled:
                index += 1
                handle.step(r.paper_id, current=index, outcome="waiting",
                            detail=f"needs {r.needed_ctx} tokens")
    return {"rerun": done, "rerun_waiting": total - done}, errors
