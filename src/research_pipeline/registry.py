"""Plug-in registry: the built-in sources and backends plus third-party entry points."""

from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any

from pathlib import Path

from research_pipeline import ollama_fit
from research_pipeline.backends.claude_code import ClaudeCodeClient, find_claude
from research_pipeline.llm import LLM, LlmError, OllamaClient
from research_pipeline.models import Config
from research_pipeline.sources.arxiv import ArxivSource
from research_pipeline.sources.base import Source
from research_pipeline.sources.hf_daily import HfDailySource

SOURCE_GROUP = "research_pipeline.sources"
BACKEND_GROUP = "research_pipeline.backends"


@dataclass(frozen=True)
class Plugin:
    name: str
    factory: Callable[..., Any] | None
    origin: str  # "built-in" or the name of the installed package
    error: str | None = None  # set when the entry point failed to load (then factory is None)


def make_arxiv(config: Config) -> ArxivSource:
    return ArxivSource(config.arxiv_categories, max_results=config.arxiv_max_results)


def make_hf_daily(config: Config) -> HfDailySource:
    return HfDailySource()


SETUP_HINT = "run `rpipe model setup`"


def make_ollama(config: Config, vault: Path, log: Callable[[str], None]) -> LLM:
    """Ollama, with `auto` settings resolved to a concrete model and context (cached in the vault)."""
    fit = ollama_fit.resolve(config.llm_url, vault, config.llm_model, config.llm_num_ctx, log=log)
    return OllamaClient(config.llm_url, fit.model, fit.num_ctx)


def make_claude_code(config: Config, vault: Path, log: Callable[[str], None]) -> LLM:
    path = find_claude(config.llm_cli_path)
    if path is None:
        raise LlmError(
            "Claude Code not found (looked at the recorded path, PATH and ~/.local/bin); "
            f"{SETUP_HINT}"
        )
    return ClaudeCodeClient(config.llm_model, path, effort=config.llm_effort)


def make_none(config: Config, vault: Path, log: Callable[[str], None]) -> None:
    """No summary model: the run writes abstracts-only digests."""
    return None


# Hard-coded, not entry points: a stale editable install can lose its entry-point metadata.
_BUILTINS: dict[str, dict[str, Callable[..., Any]]] = {
    SOURCE_GROUP: {"arxiv": make_arxiv, "hf_daily": make_hf_daily},
    BACKEND_GROUP: {"ollama": make_ollama, "claude-code": make_claude_code, "none": make_none},
}


def _entry_points(group: str):
    return entry_points(group=group)


def plugins(group: str) -> dict[str, Plugin]:
    """Built-ins first, then installed entry points of `group`. Never raises."""
    found = {n: Plugin(n, f, "built-in") for n, f in _BUILTINS.get(group, {}).items()}
    for ep in _entry_points(group):
        dist = getattr(getattr(ep, "dist", None), "name", None) or "unknown"
        if ep.name in found and found[ep.name].origin == "built-in":
            found[f"{ep.name} ({dist})"] = Plugin(ep.name, None, dist, "name taken by a built-in")
            continue
        if ep.name in found:  # a second third-party plug-in with the same name: first wins, loudly
            found[f"{ep.name} ({dist})"] = Plugin(ep.name, None, dist, f"name already taken by {found[ep.name].origin}")
            continue
        try:
            found[ep.name] = Plugin(ep.name, ep.load(), dist)
        except (Exception, SystemExit) as exc:  # a broken plug-in must never stop the pipeline
            found[ep.name] = Plugin(ep.name, None, dist, f"{type(exc).__name__}: {exc}")
    return found


def build_sources(config: Config) -> tuple[list[Source], list[str]]:
    """The enabled sources in config order, plus one error string per source that could not start."""
    known = plugins(SOURCE_GROUP)
    sources: list[Source] = []
    errors: list[str] = []
    hint = "see `rpipe sources list`"
    for name in config.sources:
        plugin = known.get(name)
        if plugin is None:
            errors.append(f"source {name}: not installed; {hint}")
        elif plugin.factory is None:
            errors.append(f"source {name}: {plugin.error}; {hint}")
        else:
            try:
                sources.append(plugin.factory(config))
            except Exception as exc:
                errors.append(f"source {name}: {type(exc).__name__}: {exc}; {hint}")
    return sources, errors


def build_backend(config: Config, vault: Path, log: Callable[[str], None]) -> LLM | None:
    """The configured summary backend, or None for `none`. LlmError (naming the fix) if it cannot start."""
    name = config.llm_backend
    plugin = plugins(BACKEND_GROUP).get(name)
    if plugin is None:
        raise LlmError(f"backend {name}: not installed; {SETUP_HINT}")
    if plugin.factory is None:
        raise LlmError(f"backend {name}: {plugin.error}; {SETUP_HINT}")
    try:
        return plugin.factory(config, vault, log)
    except LlmError:
        raise
    except Exception as exc:
        raise LlmError(f"backend {name}: {type(exc).__name__}: {exc}; {SETUP_HINT}") from exc
