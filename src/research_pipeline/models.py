"""Core data models shared by every stage of the pipeline."""

import re
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CATEGORY_RE = re.compile(r"^[a-z-]+(\.[A-Za-z-]+)?$")
ALL_CATEGORIES = "all"  # sentinel: every computer-science category (cs.*)


class Status(StrEnum):
    """Lifecycle state of a paper. Phase 1 uses new, triaged, skipped, in_digest."""

    new = "new"
    triaged = "triaged"
    fetched = "fetched"
    summarized = "summarized"
    in_digest = "in_digest"
    skipped = "skipped"
    failed = "failed"


class PaperMeta(BaseModel):
    """A paper as returned by a source, before it is stored."""

    id: str
    title: str
    abstract: str = ""
    authors: list[str] = Field(default_factory=list)
    source: str
    url: str
    version: int = 1
    hf_upvotes: int = 0
    repos: list[str] = Field(default_factory=list)
    published: date | None = None


class Profile(BaseModel):
    """What the user cares about; drives triage ranking."""

    model_config = ConfigDict(extra="forbid")

    topics: list[str]
    seeds: list[str]
    followed_authors: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    top_n: int = Field(default=15, ge=1)
    feedback_halflife_days: float = Field(default=60.0, ge=1, le=3650)


NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"


def normalise_deadline(v: Any) -> Any:
    """Canonical spelling of a night deadline, before the HH:MM/off check.

    "OFF" -> "off", "7:00" -> "07:00". An int is minutes after midnight: PyYAML reads an
    unquoted 23:00 as 1380 (base 60), so 1380 -> "23:00". Anything else is returned unchanged
    for the pattern to reject.
    """
    if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 24 * 60:
        return f"{v // 60:02d}:{v % 60:02d}"
    if isinstance(v, str):
        text = v.strip().lower()
        return "0" + text if re.fullmatch(r"\d:\d\d", text) else text
    return v


class Config(BaseModel):
    """Pipeline settings (config.yaml in the vault)."""

    model_config = ConfigDict(extra="forbid")

    vault_path: Path
    arxiv_categories: list[str] = Field(
        default_factory=lambda: ["cs.LG", "cs.CL", "cs.CR", "cs.OS", "cs.SE"]
    )
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    rss_feeds: list[str] = Field(default_factory=list)
    arxiv_max_results: int = Field(default=1000, ge=1)
    # "auto" = pick the best installed model / largest fitting context (see ollama_fit.py).
    llm_model: str = Field(default="auto", min_length=1)
    llm_url: str = "http://localhost:11434"
    llm_num_ctx: Annotated[int, Field(ge=8192)] | Literal["auto"] = "auto"
    # Which backend writes summaries ("ollama", "claude-code" or an installed plug-in).
    llm_backend: str = Field(default="ollama", pattern=NAME_PATTERN)
    # How hard a subscription model thinks per call (claude-code only; see docs/guide/backends.md).
    llm_effort: Literal["low", "medium", "high"] = "low"
    # Full path of a subscription CLI such as `claude`, recorded by `rpipe model setup`.
    llm_cli_path: str | None = None
    # Enabled paper sources, in collection order (see `rpipe sources list`).
    sources: list[str] = Field(default_factory=lambda: ["arxiv", "hf_daily"])
    # Morning deadline (24-hour HH:MM, local time) by which reruns of cut papers must finish,
    # or "off" for no reruns. Normal papers always run. See docs/guide/scheduling.md.
    night_deadline: str = Field(default="07:00", pattern=r"^(off|([01]\d|2[0-3]):[0-5]\d)$")
    # How papers are read: whole (one call), split (front/body notes, then combine) or auto
    # (chosen per run from the backend, GPU share and context). See docs/guide/summaries.md.
    summarize_mode: Literal["auto", "whole", "split"] = "auto"
    # Whether each paper also gets a diagram (one more model call): on, off, or auto (off on a
    # CPU-only Ollama model, on otherwise). See docs/guide/summaries.md.
    diagrams: Literal["auto", "on", "off"] = "auto"

    @field_validator("diagrams", mode="before")
    @classmethod
    def _diagrams_bool(cls, v: Any) -> Any:
        # A hand-edited `diagrams: off` is a YAML boolean; read it as the word.
        return {True: "on", False: "off"}.get(v, v) if isinstance(v, bool) else v

    @field_validator("night_deadline", mode="before")
    @classmethod
    def _normalise_deadline(cls, v: Any) -> Any:
        return normalise_deadline(v)

    @field_validator("sources")
    @classmethod
    def _check_sources(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("enable at least one source")
        if len(set(v)) != len(v):
            raise ValueError("sources must not repeat")
        bad = [n for n in v if not re.match(NAME_PATTERN, n)]
        if bad:
            raise ValueError(f"invalid source names {bad}")
        return v

    @field_validator("arxiv_categories")
    @classmethod
    def _check_categories(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("give at least one arXiv category, or 'all'")
        if ALL_CATEGORIES in v and v != [ALL_CATEGORIES]:
            raise ValueError("'all' cannot be combined with other categories")
        bad = [c for c in v if not CATEGORY_RE.match(c)]
        if bad:
            raise ValueError(f"invalid arXiv categories {bad}")
        return v


class Section(BaseModel):
    """One titled section of a paper's full text."""

    title: str
    text: str
