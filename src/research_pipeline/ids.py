"""Paper-id spelling: turn the ways people write an arXiv id into the one the database uses.

Standard library only, so both halves of rpipe (the pipeline and the app) can import it
without pulling in anything else.
"""

import re

# New-style ids (since 2007): four digits, a dot, four or five digits, e.g. 2307.15043.
_NEW_ID_RE = re.compile(r"^\d{4}\.\d{4,5}$")
# Old-style ids: archive name (optionally .XX subject class), a slash, seven digits, e.g. cs/0112017.
_OLD_ID_RE = re.compile(r"^[a-z-]+(\.[A-Z]{2})?/\d{7}$")


def normalise_paper_id(s: str) -> str:
    """Bare arXiv id without a version suffix; ValueError if `s` is not an id.

    Accepts ``2307.15043``, ``2307.15043v2``, ``arXiv:2307.15043`` and arxiv.org abs/pdf URLs.
    """
    t = s.strip()
    t = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", t)
    t = re.sub(r"^arxiv:", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\.pdf$", "", t)
    t = re.sub(r"v\d+$", "", t)
    if _NEW_ID_RE.match(t) or _OLD_ID_RE.match(t):
        return t
    raise ValueError(f"{s!r} is not an arXiv id (expected something like 2307.15043)")
