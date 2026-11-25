"""Pydantic shapes of a stored summary and critique.

They live here, below both `db.py` (which stores them) and `summarize.py` (which produces
them), so the database layer never has to import the summarize stage.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Claim(BaseModel):
    """One statement about the paper and the section titles it comes from."""

    text: str
    sections: list[str] = Field(min_length=1)


class Dropped(BaseModel):
    """A section left out of what the model read, and why (see pruning.py)."""

    title: str
    chars: int
    reason: Literal["non_paper", "appendix"]


class Summary(BaseModel):
    one_line: str
    problem: Claim
    approach: Claim
    results: Claim
    limitations: Claim


class Critique(BaseModel):
    strengths: list[Claim]
    weaknesses: list[Claim]
    questions: list[str]


class PaperSummary(BaseModel):
    summary: Summary
    critique: Critique
    basis: Literal["full_text", "abstract"]
    body_cut: bool = False  # core paper text was trimmed to fit (not just non-paper text dropped)
    needed_ctx: int | None = None  # context (tokens) the core text needs uncut; only when body_cut
    dropped: list[Dropped] = []  # sections left out, with sizes (see pruning.py)
    unverified: list[str]  # claim texts whose citations could not be verified

    @model_validator(mode="before")
    @classmethod
    def _read_old_truncated(cls, data):
        """Old rows and callers say `truncated` (it meant "cut", back matter or body): read it as `body_cut`."""
        if isinstance(data, dict) and "truncated" in data:
            data = dict(data)
            data.setdefault("body_cut", data.pop("truncated"))
        return data
