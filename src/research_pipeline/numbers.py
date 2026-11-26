"""Number matching for checked hooks (Phase 9).

``numbers_in`` lists the numbers a reader would take as claims; ``number_found``
says whether a hook's number appears (exactly or rounded) in a paper section.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# digits with optional thousands groups or decimals, then an optional sign (% or x)
_NUM = re.compile(
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s*(?P<sign>\\?%|[x×]))?"
)


# ASCII hyphen, U+2010 to U+2015 (various dashes) and the minus sign U+2212
_HYPHENS = "-" + "".join(chr(c) for c in range(0x2010, 0x2016)) + "\u2212"


def _extract(text: str, skip_names: bool) -> list[str]:
    found: list[str] = []
    for m in _NUM.finditer(text):
        before = text[m.start("num") - 1] if m.start("num") > 0 else ""
        if skip_names and (before.isalpha() or before and (before in _HYPHENS or before == "§")):
            continue
        sign = (m.group("sign") or "").replace("\\", "").replace("×", "x")
        # a trailing "x" glued to a word (e.g. "5xyz") is not a sign
        if sign == "x" and text[m.end() : m.end() + 1].isalpha():
            sign = ""
        found.append(m.group("num").replace(",", "") + sign)
    return found


def numbers_in(text: str) -> list[str]:
    """Claim-like numbers in ``text``: normalized, in order, duplicates removed."""
    return list(dict.fromkeys(_extract(text, skip_names=True)))


def _split(number: str) -> tuple[Decimal, str] | None:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(%|x)?", number)
    if not m:
        return None
    try:
        return Decimal(m.group(1)), m.group(2) or ""
    except InvalidOperation:
        return None


def number_found(number: str, text: str) -> bool:
    """True if ``text`` has this number, exactly or rounded to its decimal places.

    ``number`` must already be normalized by ``numbers_in`` (e.g. "28%", "1.4x").
    """
    parsed = _split(number)
    if parsed is None:
        return False
    value, sign = parsed
    step = Decimal(1).scaleb(value.as_tuple().exponent)
    for other in _extract(text, skip_names=False):
        o = _split(other)
        if o is None or o[1] != sign:
            continue
        try:
            rounded = o[0].quantize(step, rounding=ROUND_HALF_UP)
        except InvalidOperation:  # absurdly long digit runs (IDs, checksums)
            continue
        if rounded == value:
            return True
    return False
