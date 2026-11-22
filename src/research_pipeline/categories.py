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
