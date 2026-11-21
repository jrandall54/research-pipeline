"""Core data models shared by every stage of the pipeline."""

from datetime import date
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


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

    topics: list[str]
    seeds: list[str]
    followed_authors: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    top_n: int = Field(default=15, ge=1)


class Config(BaseModel):
    """Pipeline settings (config.yaml in the vault)."""

    vault_path: Path
    arxiv_categories: list[str] = Field(
        default_factory=lambda: ["cs.LG", "cs.CL", "cs.CR", "cs.OS", "cs.SE"]
    )
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    rss_feeds: list[str] = Field(default_factory=list)
