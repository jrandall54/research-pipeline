"""Pydantic shapes of a stored summary and critique.

They live here, below both `db.py` (which stores them) and `summarize.py` (which produces
them), so the database layer never has to import the summarize stage.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Claim(BaseModel):
    """One statement about the paper and the section titles it comes from."""

    text: str
    sections: list[str] = Field(min_length=1)


class Notes(BaseModel):
    """Notes on one part of a paper (or condensed notes): claims with the sections they cite."""

    claims: list[Claim] = Field(min_length=1)


class Dropped(BaseModel):
    """A section left out of what the model read, and why (see pruning.py)."""

    title: str
    chars: int
    reason: Literal["non_paper", "appendix"]


class KeyNumber(BaseModel):
    """A figure worth remembering from the paper, with the sections it comes from."""

    value: str
    label: str
    sections: list[str] = Field(min_length=1)


class Summary(BaseModel):
    one_line: str
    problem: Claim
    approach: Claim
    results: Claim
    limitations: Claim
    hook: Claim | None = None  # the finding in one plain, checkable line (not clickbait); None when absent
    key_numbers: list[KeyNumber] = []


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
    unverified: list[str]  # summary/critique claim texts whose citations could not be verified
    # Reading in parts only: notes (and condensed notes) whose citations could not be verified.
    # Not in the summary itself; their invented titles were dropped before combining.
    notes_unverified: list[str] = []
    read_mode: Literal["whole", "split", "map_reduce"] = "whole"  # how the paper was read (old rows: whole)
    parts: int = 1  # how many parts it was read in (1 when read whole)
    checks: list[str] = []  # plain reasons a hook, key number or diagram was left out

    @model_validator(mode="before")
    @classmethod
    def _read_old_truncated(cls, data):
        """Old rows and callers say `truncated` (it meant "cut", back matter or body): read it as `body_cut`."""
        if isinstance(data, dict) and "truncated" in data:
            data = dict(data)
            data.setdefault("body_cut", data.pop("truncated"))
        return data


class DiagramNode(BaseModel):
    id: str
    label: str
    detail: str | None = None
    sections: list[str] = Field(min_length=1)


class DiagramEdge(BaseModel):
    """An arrow between two nodes. In JSON the keys are `from` and `to`."""

    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(alias="from")
    target: str = Field(alias="to")
    label: str | None = None


class Diagram(BaseModel):
    shape: Literal["parts", "steps", "loop"]
    nodes: list[DiagramNode]
    edges: list[DiagramEdge]
    caption: str


class DiagramAnswer(BaseModel):
    """The model's whole answer for a diagram; `diagram` is None when there is no clear shape."""

    diagram: Diagram | None
