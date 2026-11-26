"""Choose the Ollama model and context window automatically.

Ported from the owner's `o` script (preload, mem_state, fully_on_gpu, find_max_ctx):
load a model at a given context, ask `/api/ps` whether it sits fully in GPU memory,
and binary-search the largest context that still does. Nothing in Ollama is ever
created, rebuilt or deleted; every probe unloads the model it loaded.
"""

import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from pydantic import BaseModel

from research_pipeline.llm import LlmError
from research_pipeline.scan import machine_key

CONTEXT_CEILING = 131072
UNLOAD_TIMEOUT = 30.0
CACHE_NAME = "llm_fit.json"
SAFETY_MARGIN = 1024  # one step taken off the measured maximum: the nightly run has less free memory
MIN_CONTEXT = 8192
FIT_DEADLINE = 900.0  # seconds one probing session (`rpipe model fit`, `run`, `check`) may take in all
SHOW_DEADLINE = 10.0  # seconds `rpipe model show` waits for Ollama before giving up
_clock = time.monotonic  # replaced in tests


class NoFitError(LlmError):
    """Nothing installed fits fully in GPU memory (the one failure worth caching)."""


class ProbeTimeout(LlmError):
    """The overall time limit for a probing session ran out (never cached)."""


class ProbeSession:
    """One probing session: an overall deadline and a place to warn once about failed unloads."""

    def __init__(self, seconds: float | None = None, log: Callable[[str], None] = lambda s: None):
        self.seconds = seconds
        self.log = log
        self._end = None if seconds is None else _clock() + seconds
        self._warned = False

    def expired(self) -> bool:
        return self._end is not None and _clock() >= self._end

    def check(self) -> None:
        if self.expired():
            raise ProbeTimeout(
                f"probing Ollama took longer than {self.seconds:g} seconds and was stopped "
                "(is Ollama hung or the model very slow to load?). Restart Ollama and try "
                "again; to skip probing, pin both settings with `rpipe model set NAME` and "
                "`rpipe model set-context N`."
            )

    def cap(self, timeout: float) -> float:
        """A request timeout that never runs past the deadline."""
        if self._end is None:
            return timeout
        return max(0.001, min(timeout, self._end - _clock()))

    def warn_unload(self, model: str, why: str) -> None:
        if not self._warned:
            self._warned = True
            self.log(f"! could not unload {model} after probing ({why}); it may stay "
                     "in GPU memory until Ollama unloads it (about 5 minutes)")


class ModelInfo(BaseModel):
    name: str
    digest: str
    params: int
    max_context: int | None
    capabilities: list[str]


class Fit(BaseModel):
    model: str
    num_ctx: int


def _request(method: str, url: str, path: str, timeout: float = 30.0,
             session: ProbeSession | None = None, **kwargs) -> httpx.Response:
    session = session or ProbeSession()
    session.check()
    try:
        resp = httpx.request(method, f"{url}{path}", timeout=session.cap(timeout), **kwargs)
    except httpx.ConnectError as e:
        raise LlmError(
            f"cannot reach Ollama at {url}: start it with `ollama serve` or set the "
            "address with `rpipe model set-url`"
        ) from e
    except httpx.HTTPError as e:
        session.check()
        raise LlmError(f"Ollama request to {url}{path} failed: {e}") from e
    if resp.status_code >= 400:
        raise LlmError(f"Ollama returned HTTP {resp.status_code} for {path}: {resp.text[:200]}")
    return resp


def list_models(
    url: str, log: Callable[[str], None] = lambda s: None, session: ProbeSession | None = None,
) -> list[ModelInfo]:
    """Installed models with parameter count, trained context and capabilities.

    A model whose `/api/show` answer is broken is skipped with a note, so one bad
    entry cannot stop discovery of the others.
    """
    try:
        tags = _request("GET", url, "/api/tags", session=session).json().get("models", [])
        if not isinstance(tags, list):
            raise ValueError("'models' is not a list")
    except ValueError as e:
        raise LlmError(f"Ollama at {url} sent an answer that is not valid ({e})") from e
    out: list[ModelInfo] = []
    for t in tags:
        try:
            name = t["name"]
            show = _request("POST", url, "/api/show", session=session, json={"model": name}).json()
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
        except ProbeTimeout:
            raise
        except (LlmError, ValueError, KeyError, TypeError, AttributeError) as e:
            log(f"skipping {t.get('name', '?') if isinstance(t, dict) else '?'}: "
                f"could not read its details ({e})")
    return out


def _unload(url: str, model: str, session: ProbeSession) -> None:
    try:
        resp = httpx.post(f"{url}/api/generate", json={"model": model, "keep_alive": 0},
                          timeout=UNLOAD_TIMEOUT)
        if resp.status_code >= 400:
            session.warn_unload(model, f"HTTP {resp.status_code}")
    except httpx.HTTPError as e:
        session.warn_unload(model, str(e) or type(e).__name__)


def unload(url: str, model: str, log: Callable[[str], None] = lambda s: None) -> None:
    """Ask Ollama to drop `model` from memory now; a failure is reported to `log`, never raised."""
    _unload(url, model, ProbeSession(log=log))


def loaded_models(url: str) -> list[str]:
    """Names of the models Ollama currently holds in memory (`/api/ps`); [] if unknown."""
    try:
        resp = httpx.get(f"{url}/api/ps", timeout=30.0)
        if resp.status_code >= 400:
            return []
        return [str(m.get("name") or m.get("model")) for m in resp.json().get("models", [])]
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        return []


def _same_model(a: str, b: str) -> bool:
    def norm(n: str) -> str:
        return n if ":" in n else f"{n}:latest"
    return norm(a) == norm(b)


def fits(url: str, model: str, num_ctx: int, timeout: float = 300.0, preloaded: bool = False,
         session: ProbeSession | None = None) -> bool:
    """Load `model` at `num_ctx` and report whether it sits fully in GPU memory.

    Any error, error field or timeout counts as "does not fit". The model is
    unloaded afterwards, unless it was already loaded before probing began
    (`preloaded`): that one belongs to the user and is left alone. A failed unload is
    reported once per session; the session's deadline raises `ProbeTimeout`.
    """
    session = session or ProbeSession()
    session.check()
    try:
        resp = httpx.post(
            f"{url}/api/generate",
            json={"model": model, "prompt": "", "keep_alive": "5m",
                  "options": {"num_ctx": num_ctx}},
            timeout=session.cap(timeout),
        )
        if resp.status_code >= 400:
            return False
        try:
            if resp.json().get("error"):
                return False
        except ValueError:
            return False
        ps = httpx.get(f"{url}/api/ps", timeout=session.cap(30.0))
        if ps.status_code >= 400:
            return False
        for m in ps.json().get("models", []):
            if _same_model(m.get("name", ""), model) or _same_model(m.get("model", ""), model):
                return m.get("size_vram", 0) >= m.get("size", 1)
        return False
    except (httpx.HTTPError, ValueError):
        session.check()  # a timeout caused by the deadline is not "does not fit"
        return False
    finally:
        if not preloaded:
            _unload(url, model, session)


def max_context(
    url: str, model: str, ceiling: int, floor: int = 8192, step: int = 1024,
    preloaded: bool = False, floor_fits: bool = False, session: ProbeSession | None = None,
) -> int | None:
    """Largest multiple of `step` in [floor, ceiling] that fits fully on the GPU, else None.

    `floor_fits` says the caller already measured that `floor` fits, so it is not probed again.
    """
    if ceiling < floor or not (floor_fits or fits(url, model, floor, preloaded=preloaded, session=session)):
        return None
    lo = -(-floor // step) + 1  # in steps; floor itself already fits
    hi = ceiling // step
    best = -(-floor // step)
    while lo <= hi:
        mid = (lo + hi) // 2
        if fits(url, model, mid * step, preloaded=preloaded, session=session):
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
    loaded: list[str] | None = None,
    session: ProbeSession | None = None,
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

    loaded = loaded or []
    tried: list[str] = []
    for m in candidates:
        pre = any(_same_model(m.name, n) for n in loaded)
        if pinned_ctx is not None:
            if m.max_context is not None and m.max_context < pinned_ctx:
                tried.append(f"{m.name} (trained context only {m.max_context} tokens)")
                continue
            log(f"trying {m.name} at {pinned_ctx} tokens")
            if fits(url, m.name, pinned_ctx, preloaded=pre, session=session):
                return Fit(model=m.name, num_ctx=pinned_ctx)
            tried.append(f"{m.name} (does not fit fully in GPU memory at {pinned_ctx} tokens)")
            continue
        needed = 8192 if pinned_model is not None else min_useful
        ceiling = _ceiling(m)
        if ceiling < needed:
            tried.append(f"{m.name} (trained context only {ceiling} tokens)")
            continue
        log(f"trying {m.name} at {needed} tokens")
        if not fits(url, m.name, needed, preloaded=pre, session=session):
            tried.append(f"{m.name} (does not fit fully in GPU memory at {needed} tokens)")
            continue
        log(f"searching the largest context for {m.name}")
        best = max_context(url, m.name, ceiling, floor=needed, preloaded=pre, floor_fits=True,
                          session=session)
        if best is not None and best >= needed:
            # One step of headroom: the nightly run may have a little less memory free.
            return Fit(model=m.name, num_ctx=max(best - SAFETY_MARGIN, MIN_CONTEXT))
        tried.append(f"{m.name} (no context of {needed} tokens or more fits)")
    raise NoFitError(
        "no installed model fits fully in GPU memory. Tried: " + "; ".join(tried)
        + ". Free GPU memory and run `rpipe model fit`, or pin both a model and a context "
        "yourself: `rpipe model set NAME` and `rpipe model set-context N`."
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


def _default_machine(url: str) -> dict:
    # Looked up at call time so tests can replace `machine_key` and never run real commands.
    return machine_key(url)


def _cache_key(url: str, model: str, num_ctx: int | str, models: list[ModelInfo],
               machine: dict | None = None) -> str:
    return json.dumps(
        {"url": url, "model": model, "num_ctx": num_ctx,
         "installed": sorted([m.name, m.digest] for m in models),
         "machine": machine or {}},
        sort_keys=True,
    )


def _in_bounds(fit: Fit, models: list[ModelInfo], floor: bool = True) -> bool:
    """A cached context must still be within the model's trained context, and (when it was
    chosen automatically, `floor=True`) at least MIN_CONTEXT. A context pinned by hand is
    the user's own choice, so the floor does not apply to it."""
    if floor and fit.num_ctx < MIN_CONTEXT:
        return False
    m = next((m for m in models if m.name == fit.model), None)
    return m is not None and (m.max_context is None or fit.num_ctx <= m.max_context)


def _cached(vault: Path, key: str, models: list[ModelInfo],
            log: Callable[[str], None] = lambda s: None, floor: bool = True) -> Fit | LlmError | None:
    """The cached outcome for this exact situation: a Fit, the cached failure, or None."""
    cached = _read_cache(vault / "cache" / CACHE_NAME)
    if cached.get("key") != key:
        return None
    if isinstance(cached.get("error"), str):
        return NoFitError(cached["error"])
    if any(m.name == cached.get("model") for m in models):
        try:
            fit = Fit(model=cached["model"], num_ctx=cached["num_ctx"])
        except (KeyError, ValueError):
            return None
        if _in_bounds(fit, models, floor):
            return fit
        log(f"the saved context of {fit.num_ctx} tokens for {fit.model} is outside what it "
            f"supports (at least {MIN_CONTEXT}, at most its trained context); probing again")
    return None


def cached_fit(url: str, vault: Path, model: str, num_ctx: int | str,
               deadline: float | None = SHOW_DEADLINE,
               machine: Callable[[str], dict] = _default_machine) -> Fit | None:
    """The cached choice, only if it is still valid for these settings and installed models.

    Returns None when there is none, when it is stale, or when Ollama cannot be asked.
    Raises `ProbeTimeout` if Ollama does not answer within `deadline` seconds.
    """
    try:
        models = list_models(url, session=ProbeSession(deadline))
    except ProbeTimeout:
        raise
    except LlmError:
        return None
    hit = _cached(vault, _cache_key(url, model, num_ctx, models, machine(url)), models,
                  floor=num_ctx == "auto")
    return hit if isinstance(hit, Fit) else None


def resolve(
    url: str,
    vault: Path,
    model: str,
    num_ctx: int | str,
    force: bool = False,
    log: Callable[[str], None] = lambda s: None,
    deadline: float | None = FIT_DEADLINE,
    machine: Callable[[str], dict] = _default_machine,
) -> Fit:
    """Turn the config values (`auto` or explicit) into a concrete model and context.

    A "nothing fits" outcome is cached too (so a small machine does not re-probe every
    night) and re-raised until the settings or installed models change or `force` is set.
    Nothing is cached when other models were loaded while probing: the card was busy.
    The whole session (discovery and every probe) must finish within `deadline` seconds,
    else `ProbeTimeout` is raised and nothing is cached.
    """
    if model != "auto" and num_ctx != "auto":
        return Fit(model=model, num_ctx=int(num_ctx))
    session = ProbeSession(deadline, log)
    models = list_models(url, log=log, session=session)
    key = _cache_key(url, model, num_ctx, models, machine(url))
    path = vault / "cache" / CACHE_NAME
    if not force:
        hit = _cached(vault, key, models, log, floor=num_ctx == "auto")
        if isinstance(hit, LlmError):
            raise hit
        if hit is not None:
            return hit
    loaded = loaded_models(url)
    if loaded:
        log(f"probed while {', '.join(loaded)} was loaded; re-run `rpipe model fit` "
            "when the GPU is idle")
    try:
        fit = choose(
            url, models,
            None if model == "auto" else model,
            None if num_ctx == "auto" else int(num_ctx),
            log=log, loaded=loaded, session=session,
        )
    except NoFitError as e:
        if not loaded:
            _write_cache(path, {"key": key, "error": str(e)})
        raise
    if not loaded:
        _write_cache(path, {"key": key, **fit.model_dump()})
    return fit
