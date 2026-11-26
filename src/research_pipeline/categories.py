"""arXiv category codes checked against a packaged copy of arXiv's own list."""

import difflib
from functools import cache
from importlib import resources

ALL = "all"  # sentinel: every computer-science category (cs.*)

# Plausible but wrong codes whose intended meaning is clear; plain string similarity would
# suggest cs.PL for cs.ML, which is a worse guess.
COMMON_MISTAKES = {"cs.ML": "cs.LG", "cs.NLP": "cs.CL", "cs.LLM": "cs.CL"}


@cache
def known_categories() -> frozenset[str]:
    """Every real arXiv code, read once from defaults/arxiv_categories.txt."""
    text = resources.files("research_pipeline.defaults").joinpath("arxiv_categories.txt").read_text("utf-8")
    lines = (line.strip() for line in text.splitlines())
    return frozenset(line for line in lines if line and not line.startswith("#"))


def unknown(codes: list[str]) -> list[str]:
    """The codes that are not real arXiv categories (`all` always counts as known)."""
    known = known_categories()
    return [c for c in codes if c != ALL and c not in known]


def closest(code: str) -> str | None:
    """The nearest real code, for a did-you-mean (None if nothing is close)."""
    if code in COMMON_MISTAKES and COMMON_MISTAKES[code] in known_categories():
        return COMMON_MISTAKES[code]
    found = difflib.get_close_matches(code, sorted(known_categories()), n=1, cutoff=0.6)
    return found[0] if found else None


def describe_unknown(code: str) -> str:
    hint = closest(code)
    return f"{code} is not an arXiv category" + (f" (did you mean {hint}?)" if hint else "")


# The setup menu: (code, plain description), in menu order. Numbers are 1-based positions.
COMMON: list[tuple[str, str]] = [
    ("cs.AI", "Artificial intelligence (general)"),
    ("cs.LG", "Machine learning"),
    ("cs.CL", "Language and LLMs"),
    ("cs.CV", "Computer vision (images, video)"),
    ("cs.CR", "Security and cryptography"),
    ("cs.SE", "Software engineering"),
    ("cs.MA", "Multi-agent systems"),
    ("cs.HC", "Human-computer interaction"),
    ("cs.CY", "Computers and society (ethics, policy, education)"),
    ("cs.IR", "Search and recommendation"),
    ("cs.RO", "Robotics"),
    ("cs.DC", "Distributed and parallel computing"),
    ("cs.OS", "Operating systems"),
    ("cs.NI", "Networking"),
    ("cs.PL", "Programming languages"),
    ("cs.DB", "Databases"),
    ("stat.ML", "Statistical machine learning"),
]


def parse_choice(text: str, current: list[str]) -> list[str]:
    """Turn what the user typed at the categories prompt into a category list.

    Blank keeps `current`; `all` means every cs.* category; otherwise comma-separated menu
    numbers and/or arXiv codes, which replace the selection (order kept, duplicates dropped)."""
    text = text.strip()
    if not text:
        return list(current)
    if text.lower() == ALL:
        return [ALL]
    result: list[str] = []
    for item in (part.strip() for part in text.split(",")):
        if not item:
            continue
        if item.isdigit():
            number = int(item)
            if not 1 <= number <= len(COMMON):
                raise ValueError(
                    f"There is no option {number}. Pick numbers from 1 to {len(COMMON)}, or type arXiv codes."
                )
            item = COMMON[number - 1][0]
        elif item.lower() == ALL:
            item = ALL
        if item not in result:
            result.append(item)
    return result or list(current)
