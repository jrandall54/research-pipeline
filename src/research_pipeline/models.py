"""Core data models shared by every stage of the pipeline."""

import re
from datetime import date
from enum import StrEnum
from pathlib import Path

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
    llm_model: str = Field(default="qwen3:14b", min_length=1)
    llm_url: str = "http://localhost:11434"
    llm_num_ctx: int = Field(default=32768, ge=2048)

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
