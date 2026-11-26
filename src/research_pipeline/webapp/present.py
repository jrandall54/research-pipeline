"""Shapes library_api views into what a card shows. Pure functions: no database, no web framework."""

import re
import zlib
from dataclasses import dataclass
from datetime import date
from typing import Literal

from research_pipeline.library_api import PaperView, ReasonView, plain_reasons
from research_pipeline.summary_models import Claim

SummarizeState = Literal["none", "pending", "running", "done", "failed"]
EXCERPT_CHARS = 300
_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_SECTION_NUMBER = re.compile(r"^(\d+(\.\d+)*|[IVXLC]+(-[A-Z])?|[A-Z](\.\d+)+)$")


def _phrase(r: ReasonView) -> str:
    return {
        "topic": f"you follow {r.label}",
        "liked": "it resembles a paper you liked",
        "upvotes": f"{r.label} people upvoted it on Hugging Face",
        "author": f"it's by {r.label}, whom you follow",
        "keyword": f"it mentions {r.label}",
        "shelf": f'it\'s close to "{r.label}" on your shelf',
    }[r.kind]


def why_sentence(reasons: list[ReasonView]) -> str | None:
    parts = [_phrase(r) for r in reasons]
    if not parts:
        return None
    if len(parts) == 1:
        return f"{parts[0]}."
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}."
    return f"{', '.join(parts[:-1])}, and {parts[-1]}."


def topic_of(reasons: list[ReasonView]) -> str | None:
    for r in reasons:
        if r.kind == "topic" and r.label:
            return r.label[0].upper() + r.label[1:]
    return None


def topic_color(topic: str) -> int:
    """Stable index 0-7 into the CSS topic palette: the same topic keeps its colour everywhere."""
    return zlib.crc32(topic.lower().encode()) % 8


def section_tag(sections: list[str]) -> str | None:
    numbers = []
    for title in sections:
        words = title.split()
        if words and _SECTION_NUMBER.match(words[0]):
            numbers.append(words[0])
    if not numbers:
        return None
    if numbers[0] == numbers[-1]:
        return f"§{numbers[0]}"
    return f"§{numbers[0]}–{numbers[-1]}"


def day_heading(iso: str) -> str:
    """'Wednesday, Nov 25', from fixed English names so it does not depend on the machine's locale."""
    d = date.fromisoformat(iso)
    return f"{_DAYS[d.weekday()]}, {_MONTHS[d.month - 1]} {d.day}"


def excerpt(text: str, limit: int = EXCERPT_CHARS) -> str:
    """The first ``limit`` characters, cut at a word, with an ellipsis when something was left out."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if not text[limit].isspace() and " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


Field = tuple[str, str | None]  # (text, section tag)


@dataclass
class CardData:
    id: str
    title: str
    url: str
    topic: str | None
    topic_color: int
    one_line: str | None
    abstract: str
    problem: Field | None
    approach: Field | None
    results: Field | None
    why: str | None
    vote: int
    saved: bool
    summarize_state: SummarizeState


def _field(claim: Claim | None) -> Field | None:
    return (claim.text, section_tag(claim.sections)) if claim else None


def card(view: PaperView, state: str | None) -> CardData:
    reasons = plain_reasons(view.triage_reasons)
    topic = topic_of(reasons)
    s = view.summary.summary if view.summary else None
    return CardData(
        id=view.id, title=view.title, url=view.url, topic=topic,
        topic_color=topic_color(topic) if topic else 0,
        one_line=s.one_line if s else None, abstract=excerpt(view.abstract),
        problem=_field(s.problem if s else None), approach=_field(s.approach if s else None),
        results=_field(s.results if s else None), why=why_sentence(reasons),
        vote=view.vote, saved=view.saved,
        summarize_state="done" if s else (state or "none"),  # type: ignore[arg-type]
    )
