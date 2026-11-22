"""Plug-in registry: the built-in sources and backends plus third-party entry points."""

from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any

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


# Hard-coded, not entry points: a stale editable install can lose its entry-point metadata.
_BUILTINS: dict[str, dict[str, Callable[..., Any]]] = {
    SOURCE_GROUP: {"arxiv": make_arxiv, "hf_daily": make_hf_daily},
    BACKEND_GROUP: {},
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
        try:
            found[ep.name] = Plugin(ep.name, ep.load(), dist)
        except Exception as exc:  # a broken plug-in must never stop the pipeline
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
