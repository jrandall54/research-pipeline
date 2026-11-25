"""Decide what the model reads: drop non-paper text, then appendices, then trim in order.

See docs/guide/summaries.md, The context budget.
"""

import re
from dataclasses import dataclass
from typing import Literal

from research_pipeline.models import Section
from research_pipeline.summary_models import Dropped

# Tokens kept free for the instructions and the model's answer.
RESERVED_TOKENS = 4000
# A conservative estimate: English text averages about 4 characters per token; 3 leaves room.
CHARS_PER_TOKEN = 3
TRIM_MARK = " [...]"
TRIM_FLOOR = 0.25  # the ordered trim never cuts a trimmable section below this share of its text
# A cut paper records the context it would need, rounded up to this step (Ollama sizes go in steps).
NEEDED_STEP = 1024
STATEMENT_CHARS = 1500  # a "data availability" section this short is a statement, not content

SectionClass = Literal["paper", "non_paper", "appendix"]

# Optional numbering in front of a title: "7 ", "3.2 ", "A ", "A.1 ".
_PREFIX = re.compile(r"^(?:(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)*)\.?\s+)")
_APPENDIX_KEY = re.compile(r"^appendix\s+([A-Z0-9]+)\b", re.IGNORECASE)

_NON_PAPER = (
    "references", "bibliography", "acknowledg", "funding", "author contributions",
    "competing interests", "conflict of interest", "conflicts of interest", "ethics statement",
    "reproducibility checklist", "instructions for reporting errors",
)
_STATEMENT = ("data availability", "data and code availability", "code availability")
_APPENDIX = ("appendi", "supplementary", "supplemental", "extended data")
_TRIMMABLE = (
    "related work", "background", "preliminaries", "method", "approach",
    "experimental setup", "implementation",
)


def _words(title: str) -> str:
    return _PREFIX.sub("", title.strip(), count=1).lower()


def classify(title: str, text: str = "") -> SectionClass:
    """`non_paper` (always dropped), `appendix` (dropped first when short) or `paper`."""
    words = _words(title)
    if words.startswith(_NON_PAPER):
        return "non_paper"
    if words.startswith(_STATEMENT) and len(text) <= STATEMENT_CHARS:
        return "non_paper"
    if words.startswith(_APPENDIX):
        return "appendix"
    return "paper"


def _number(title: str) -> str | None:
    """The numbering a section has ("6", "A.1"), or the letter of "Appendix B"."""
    title = title.strip()
    m = re.match(r"^(\d+(?:\.\d+)*|[A-Z](?:\.\d+)*)\.?\s", title)
    if m:
        return m.group(1)
    a = _APPENDIX_KEY.match(title)
    return a.group(1).upper() if a else None


def _classes(sections: list[Section]) -> list[SectionClass]:
    """Class of each section; a child (6.1 under a dropped 6) takes its parent's class."""
    out: list[SectionClass] = []
    parents: list[tuple[str, SectionClass]] = []
    for s in sections:
        cls = classify(s.title, s.text)
        if cls == "paper":
            num = _number(s.title)
            for key, parent_cls in parents:
                if num is not None and num.startswith(key + "."):
                    cls = parent_cls
                    break
        else:
            key = _number(s.title)
            if key is not None:
                parents.append((key, cls))
        out.append(cls)
    return out


@dataclass(frozen=True)
class Fit:
    sections: list[Section]
    dropped: list[Dropped]
    body_cut: bool  # True only when core paper text had to be trimmed to fit
    # Context (tokens) the core text would need to fit uncut; set only when body_cut.
    needed_ctx: int | None = None


def _size(sections: list[Section]) -> int:
    return sum(len(s.title) + len(s.text) for s in sections)


def _needed_ctx(core: list[Section]) -> int:
    """Context that fits `core` whole: its tokens plus the reserve, rounded up to NEEDED_STEP."""
    tokens = -(-_size(core) // CHARS_PER_TOKEN) + RESERVED_TOKENS
    return -(-tokens // NEEDED_STEP) * NEEDED_STEP


def _cut(s: Section, ratio: float) -> Section:
    keep = int(len(s.text) * ratio)
    if keep >= len(s.text):
        return s
    return Section(title=s.title, text=s.text[:keep].rstrip() + TRIM_MARK)


def _uniform(sections: list[Section], budget: int) -> list[Section]:
    title_chars = sum(len(s.title) for s in sections)
    text_chars = sum(len(s.text) for s in sections)
    room = max(0, budget - title_chars - len(TRIM_MARK) * len(sections))
    ratio = room / text_chars if text_chars else 0.0
    return [_cut(s, ratio) for s in sections]


def fit_to_budget(sections: list[Section], num_ctx: int) -> Fit:
    """Fit sections into ``(num_ctx - 4000) * 3`` characters.

    Non-paper text is always dropped. If the rest does not fit, appendices go next. Only then
    is core text trimmed (``body_cut``): first the long method/background/related-work
    sections, then, if that is not enough, every section by the same fraction. A cut fit also
    says how much context the core text needs whole (``needed_ctx``), for a later rerun.
    """
    budget = max(0, (num_ctx - RESERVED_TOKENS) * CHARS_PER_TOKEN)
    classes = _classes(sections)
    dropped = [
        Dropped(title=s.title, chars=len(s.title) + len(s.text), reason=c)  # type: ignore[arg-type]
        for s, c in zip(sections, classes, strict=True) if c != "paper"
    ]
    kept = [s for s, c in zip(sections, classes, strict=True) if c == "paper"]
    with_appendix = [s for s, c in zip(sections, classes, strict=True) if c != "non_paper"]
    if _size(with_appendix) <= budget:
        return Fit(with_appendix, [d for d in dropped if d.reason == "non_paper"], False)
    if _size(kept) <= budget:
        return Fit(kept, dropped, False)

    needed = _needed_ctx(kept)
    trim = [i for i, s in enumerate(kept) if _words(s.title).startswith(_TRIMMABLE)]
    other = _size([s for i, s in enumerate(kept) if i not in trim])
    text_t = sum(len(kept[i].text) for i in trim)
    if text_t:
        room = budget - other - sum(len(kept[i].title) for i in trim) - len(TRIM_MARK) * len(trim)
        ratio = room / text_t
        if ratio >= TRIM_FLOOR:
            return Fit(
                [_cut(s, ratio) if i in trim else s for i, s in enumerate(kept)], dropped, True, needed
            )
    return Fit(_uniform(kept, budget), dropped, True, needed)
