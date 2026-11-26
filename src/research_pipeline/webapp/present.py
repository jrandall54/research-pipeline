"""Shapes library_api views into what a card shows. Pure functions: no database, no web framework."""

import re
import zlib
from dataclasses import dataclass
from datetime import date
from typing import Literal

from research_pipeline.library_api import PaperView, ProvenanceView, ReasonView, plain_reasons
from research_pipeline.summary_models import Claim

SummarizeState = Literal["none", "pending", "running", "done", "failed"]
EXCERPT_CHARS = 300
_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_SECTION_NUMBER = re.compile(r"^(\d+(\.\d+)*|[IVXLC]+(-[A-Z])?|[A-Z](\.\d+)+)$")


# A sentence ends at . ! or ? (perhaps inside a closing bracket or quote), then space and a
# capital, digit, bracket or quote.
_SENTENCE_BREAK = re.compile(r"(?:(?<=[.!?])|(?<=[.!?][)\"\u201d]))\s+(?=[A-Z0-9(\"\u201c])")
# Words whose full stop is not a sentence end; a break after one of these is undone.
_ABBREVIATIONS = ("e.g.", "i.e.", "et al.", "vs.", "cf.", "etc.", "approx.", "Fig.", "Figs.", "Eq.", "Eqs.",
                  "Sec.", "Tab.", "No.", "Ref.", "Refs.", "Dr.", "Mr.", "Ms.", "St.")
_INITIAL = re.compile(r"(?:^|\s)[A-Z]\.$")


def sentences(text: str) -> list[str]:
    """Split a summary field into sentences for display as points.

    Careful on purpose: when unsure it keeps text together, because a missed split leaves one
    block (harmless) while a wrong split cuts a sentence in two.
    """
    parts: list[str] = []
    for piece in _SENTENCE_BREAK.split(text.strip()):
        if parts and (parts[-1].endswith(_ABBREVIATIONS) or _INITIAL.search(parts[-1])):
            parts[-1] += " " + piece
        elif piece:
            parts.append(piece)
    return parts


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
    safe_url: str | None
    topic: str | None
    topic_color: int
    one_line: str | None
    hook: Field | None
    key_numbers: list[tuple[str, str, str | None]]  # (value, label, section tag)
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


def safe_url(url: str) -> str | None:
    """The paper's address, only when it is a plain web link (never ``javascript:`` and the like)."""
    return url if url.lower().startswith(("http://", "https://")) else None


def card(view: PaperView, state: str | None) -> CardData:
    reasons = plain_reasons(view.triage_reasons)
    topic = topic_of(reasons)
    s = view.summary.summary if view.summary else None
    return CardData(
        id=view.id, title=view.title, url=view.url, safe_url=safe_url(view.url), topic=topic,
        topic_color=topic_color(topic) if topic else 0,
        one_line=s.one_line if s else None, hook=_field(s.hook if s else None),
        key_numbers=[(k.value, k.label, section_tag(k.sections)) for k in s.key_numbers] if s else [],
        abstract=excerpt(view.abstract),
        problem=_field(s.problem if s else None), approach=_field(s.approach if s else None),
        results=_field(s.results if s else None), why=why_sentence(reasons),
        vote=view.vote, saved=view.saved,
        summarize_state="done" if s else (state or "none"),  # type: ignore[arg-type]
    )


_SECTIONS_SUFFIX = re.compile(r"\s*\[sections?:[^\]]*\]\s*$", re.IGNORECASE)


@dataclass
class DetailData:
    card: CardData
    authors_short: str
    abstract: str  # full text, for papers with no summary yet
    strengths: list[Field]
    weaknesses: list[Field]
    questions: list[str]
    about: list[str]


def authors_short(authors: list[str]) -> str:
    """'A', 'A and B', 'A, B and C', or 'A, B, C and N others' for more than three."""
    if len(authors) <= 1:
        return "".join(authors)
    if len(authors) <= 3:
        return f"{', '.join(authors[:-1])} and {authors[-1]}"
    return f"{', '.join(authors[:3])} and {len(authors) - 3} others"


def _tagged(claims: list[Claim]) -> list[Field]:
    return [(c.text, section_tag(c.sections)) for c in claims]


def paper_detail(view: PaperView, state: str | None, provenance: ProvenanceView | None = None) -> DetailData:
    """Everything the paper page prints beyond the card: critique with tags and the "About this summary" lines."""
    ps = view.summary
    about: list[str] = []
    if ps:
        about.append("Read from the full paper" if ps.basis == "full_text" else "Read from the abstract only")
        if ps.parts > 1:
            about.append(f"Read in {ps.parts} parts")
        if ps.body_cut:
            about.append("Some of the paper was too long and was cut")
        if provenance:
            about.append(f"Summarized by {provenance.model} on {provenance.created_at[:10]}")
        about += [f'Could not check: "{t}"' for t in ps.unverified]
        about += [f"Left out: {r}" for r in ps.checks]
    return DetailData(
        card=card(view, state),
        authors_short=authors_short(view.authors), abstract=view.abstract,
        strengths=_tagged(ps.critique.strengths) if ps else [],
        weaknesses=_tagged(ps.critique.weaknesses) if ps else [],
        questions=[_SECTIONS_SUFFIX.sub("", q) for q in ps.critique.questions] if ps else [],
        about=about,
    )
