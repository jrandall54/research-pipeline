"""Pydantic shapes of a stored summary and critique.

They live here, below both `db.py` (which stores them) and `summarize.py` (which produces
them), so the database layer never has to import the summarize stage.
"""

from typing import Literal

from pydantic import BaseModel, Field


class Claim(BaseModel):
    """One statement about the paper and the section titles it comes from."""

    text: str
    sections: list[str] = Field(min_length=1)


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
    truncated: bool
    unverified: list[str]  # claim texts whose citations could not be verified
