"""Check the hook and key numbers against the paper's text (Phase 9).

The model writes them; code decides whether they are shown. A hook or key number is kept
only if every section it cites exists and every number in it appears in those sections.
See "Hook and key numbers: how they are checked" in docs/guide/summaries.md.
"""

import re

from research_pipeline.citations import section_texts
from research_pipeline.models import Section
from research_pipeline.numbers import number_found, numbers_in
from research_pipeline.summary_models import PaperSummary

_LEADING_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)*)")


def _tag(titles: list[str]) -> str:
    """`§5.1` from the first cited title's leading number, else the title itself."""
    first = titles[0].strip()
    m = _LEADING_NUMBER.match(first)
    return f"§{m.group(1)}" if m else first


def _failure(what: str, text: str, cited: list[str], sections: list[Section], abstract: str) -> str | None:
    """Why ``text`` (a hook or a key number's value) fails against its cited sections, or None."""
    texts = section_texts(cited, sections, abstract)
    if texts is None:
        return f"{what}: cites a section the paper does not have"
    joined = "\n".join(texts)
    numbers = numbers_in(text)
    if what != "hook" and not numbers:
        return f"{what}: not a number"
    for n in numbers:
        if not number_found(n, joined):
            return f"{what}: {n} not found in {_tag(cited)}"
    return None


def check_extras(ps: PaperSummary, sections: list[Section], abstract: str) -> PaperSummary:
    """A copy with a failed hook set to None, failed key numbers removed, at most 3 kept, reasons in `checks`."""
    summary = ps.summary
    checks = list(ps.checks)
    hook = summary.hook
    if hook is not None:
        why = _failure("hook", hook.text, hook.sections, sections, abstract)
        if why:
            checks.append(why)
            hook = None
    kept = []
    for k in summary.key_numbers:
        why = _failure(f"key number {k.value}", k.value, k.sections, sections, abstract)
        if why:
            checks.append(why)
        else:
            kept.append(k)
    return ps.model_copy(
        update={"summary": summary.model_copy(update={"hook": hook, "key_numbers": kept[:3]}),
                "checks": checks}
    )
