"""Choose the Ollama model and context window automatically.

Ported from the owner's `o` script (preload, mem_state, fully_on_gpu, find_max_ctx):
load a model at a given context, ask `/api/ps` whether it sits fully in GPU memory,
and binary-search the largest context that still does. Nothing in Ollama is ever
created, rebuilt or deleted; every probe unloads the model it loaded.
"""

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import httpx
from pydantic import BaseModel

from research_pipeline.llm import LlmError

CONTEXT_CEILING = 131072
UNLOAD_TIMEOUT = 30.0
CACHE_NAME = "llm_fit.json"


class ModelInfo(BaseModel):
    name: str
    digest: str
    params: int
    max_context: int | None
    capabilities: list[str]


class Fit(BaseModel):
    model: str
    num_ctx: int


def _request(method: str, url: str, path: str, timeout: float = 30.0, **kwargs) -> httpx.Response:
    try:
        resp = httpx.request(method, f"{url}{path}", timeout=timeout, **kwargs)
    except httpx.ConnectError as e:
        raise LlmError(
            f"cannot reach Ollama at {url}: start it with `ollama serve` or set the "
            "address with `rpipe model set-url`"
        ) from e
    except httpx.HTTPError as e:
        raise LlmError(f"Ollama request to {url}{path} failed: {e}") from e
    if resp.status_code >= 400:
        raise LlmError(f"Ollama returned HTTP {resp.status_code} for {path}: {resp.text[:200]}")
    return resp


def list_models(url: str, log: Callable[[str], None] = lambda s: None) -> list[ModelInfo]:
    """Installed models with parameter count, trained context and capabilities.

    A model whose `/api/show` answer is broken is skipped with a note, so one bad
    entry cannot stop discovery of the others.
    """
    tags = _request("GET", url, "/api/tags").json().get("models", [])
    out: list[ModelInfo] = []
    for t in tags:
        try:
            name = t["name"]
            show = _request("POST", url, "/api/show", json={"model": name}).json()
            mi = show.get("model_info") or {}
            ctx = next(
                (v for k, v in mi.items() if k.endswith(".context_length") and isinstance(v, int)),
                None,
            )
            out.append(ModelInfo(
                name=name,
                digest=t.get("digest", ""),
                params=int(mi.get("general.parameter_count") or 0),
                max_context=ctx,
                capabilities=list(show.get("capabilities") or []),
            ))
        except (LlmError, ValueError, KeyError, TypeError, AttributeError) as e:
            log(f"skipping {t.get('name', '?') if isinstance(t, dict) else '?'}: "
                f"could not read its details ({e})")
    return out


def _unload(url: str, model: str) -> None:
    try:
        httpx.post(f"{url}/api/generate", json={"model": model, "keep_alive": 0},
                   timeout=UNLOAD_TIMEOUT)
    except httpx.HTTPError:
        pass


def _same_model(a: str, b: str) -> bool:
    def norm(n: str) -> str:
        return n if ":" in n else f"{n}:latest"
    return norm(a) == norm(b)


def fits(url: str, model: str, num_ctx: int, timeout: float = 300.0) -> bool:
    """Load `model` at `num_ctx` and report whether it sits fully in GPU memory.

    Any error, error field or timeout counts as "does not fit". The model is
    always unloaded afterwards.
    """
    try:
        resp = httpx.post(
            f"{url}/api/generate",
            json={"model": model, "prompt": "", "keep_alive": "5m",
                  "options": {"num_ctx": num_ctx}},
            timeout=timeout,
        )
        if resp.status_code >= 400:
            return False
        try:
            if resp.json().get("error"):
                return False
        except ValueError:
            return False
        ps = httpx.get(f"{url}/api/ps", timeout=30.0)
        if ps.status_code >= 400:
            return False
        for m in ps.json().get("models", []):
            if _same_model(m.get("name", ""), model) or _same_model(m.get("model", ""), model):
                return m.get("size_vram", 0) >= m.get("size", 1)
        return False
    except (httpx.HTTPError, ValueError):
        return False
    finally:
        _unload(url, model)


def max_context(url: str, model: str, ceiling: int, floor: int = 8192, step: int = 1024) -> int | None:
    """Largest multiple of `step` in [floor, ceiling] that fits fully on the GPU, else None."""
    if ceiling < floor or not fits(url, model, floor):
        return None
    lo = -(-floor // step) + 1  # in steps; floor itself already fits
    hi = ceiling // step
    best = -(-floor // step)
    while lo <= hi:
        mid = (lo + hi) // 2
        if fits(url, model, mid * step):
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best * step


def _ceiling(m: ModelInfo) -> int:
    return min(m.max_context or CONTEXT_CEILING, CONTEXT_CEILING)


def choose(
    url: str,
    models: list[ModelInfo],
    pinned_model: str | None,
    pinned_ctx: int | None,
    min_useful: int = 16384,
    log: Callable[[str], None] = lambda s: None,
) -> Fit:
    """Pick the largest completion model that fits fully in GPU memory, with its best context."""
    usable = [m for m in models if "completion" in m.capabilities and "embedding" not in m.capabilities]
    if pinned_model is not None:
        candidates = [m for m in models if _same_model(m.name, pinned_model)]
        if not candidates:
            raise LlmError(
                f"model {pinned_model!r} is not installed. Install it with "
                f"`ollama pull {pinned_model}` or choose another with `rpipe model set`."
            )
    else:
        candidates = sorted(usable, key=lambda m: m.params, reverse=True)
        if not candidates:
            raise LlmError(
                "no usable model is installed in Ollama (embedding-only models cannot write "
                "summaries). Install one with `ollama pull NAME`, e.g. `ollama pull qwen3:14b`."
            )

    tried: list[str] = []
    for m in candidates:
        if pinned_ctx is not None:
            if m.max_context is not None and m.max_context < pinned_ctx:
                tried.append(f"{m.name} (trained context only {m.max_context} tokens)")
                continue
            log(f"trying {m.name} at {pinned_ctx} tokens")
            if fits(url, m.name, pinned_ctx):
                return Fit(model=m.name, num_ctx=pinned_ctx)
            tried.append(f"{m.name} (does not fit fully in GPU memory at {pinned_ctx} tokens)")
            continue
        needed = 8192 if pinned_model is not None else min_useful
        ceiling = _ceiling(m)
        if ceiling < needed:
            tried.append(f"{m.name} (trained context only {ceiling} tokens)")
            continue
        log(f"trying {m.name} at {needed} tokens")
        if not fits(url, m.name, needed):
            tried.append(f"{m.name} (does not fit fully in GPU memory at {needed} tokens)")
            continue
        log(f"searching the largest context for {m.name}")
        best = max_context(url, m.name, ceiling)
        if best is not None and best >= needed:
            return Fit(model=m.name, num_ctx=best)
        tried.append(f"{m.name} (no context of {needed} tokens or more fits)")
    raise LlmError(
        "no installed model fits fully in GPU memory. Tried: " + "; ".join(tried)
        + ". Free GPU memory, or choose a model or context yourself with "
        "`rpipe model set` and `rpipe model set-context`."
    )


def _write_cache(path: Path, data: dict) -> None:
    """Write the cache atomically: temp file in the same folder, then rename over."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_cache(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def last_choice(vault: Path) -> Fit | None:
    """The choice `resolve` last cached for this vault, or None if there is none."""
    cached = _read_cache(vault / "cache" / CACHE_NAME)
    try:
        return Fit(model=cached["model"], num_ctx=cached["num_ctx"])
    except (KeyError, ValueError):
        return None


def resolve(
    url: str,
    vault: Path,
    model: str,
    num_ctx: int | str,
    force: bool = False,
    log: Callable[[str], None] = lambda s: None,
) -> Fit:
    """Turn the config values (`auto` or explicit) into a concrete model and context."""
    if model != "auto" and num_ctx != "auto":
        return Fit(model=model, num_ctx=int(num_ctx))
    models = list_models(url, log=log)
    key = json.dumps(
        {"url": url, "model": model, "num_ctx": num_ctx,
         "installed": sorted([m.name, m.digest] for m in models)},
        sort_keys=True,
    )
    path = vault / "cache" / CACHE_NAME
    if not force:
        cached = _read_cache(path)
        if cached.get("key") == key and any(m.name == cached.get("model") for m in models):
            try:
                return Fit(model=cached["model"], num_ctx=cached["num_ctx"])
            except (KeyError, ValueError):
                pass
    fit = choose(
        url, models,
        None if model == "auto" else model,
        None if num_ctx == "auto" else int(num_ctx),
        log=log,
    )
    _write_cache(path, {"key": key, **fit.model_dump()})
    return fit
