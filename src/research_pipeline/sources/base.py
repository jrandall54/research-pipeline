"""Common source interface and error type."""

from typing import Protocol

from research_pipeline.models import PaperMeta


class SourceError(Exception):
    """A source could not deliver papers (network failure, bad response, ...)."""


class Source(Protocol):
    """Something that can list recent papers."""

    name: str

    def fetch(self) -> list[PaperMeta]: ...
