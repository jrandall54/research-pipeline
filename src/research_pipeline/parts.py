"""Read a paper in parts: notes on each part, condense the notes if needed, then combine.

A paper too long for one call is read in parts. Each part gets one notes call (claims with the
sections they cite, checked like every other citation); if all the notes together are still too
long, they are condensed in rounds; then the notes are combined into the usual Summary and
Critique. See "Reading a paper in parts" in docs/guide/summaries.md.

Imports citations.py, never summarize.py (summarize.py calls into this module).
"""

import json
from collections.abc import Callable
from typing import Literal

from research_pipeline.citations import generate_checked
from research_pipeline.llm import LLM
from research_pipeline.models import Section
from research_pipeline.prompts import (
    Focus,
    build_combine_prompts,
    build_condense_prompt,
    build_notes_prompt,
    notes_block,
)
from research_pipeline.pruning import (
    CHARS_PER_TOKEN,
    RESERVED_TOKENS,
    TRIM_MARK,
    _number,
    _words,
    partition,
)
from research_pipeline.summary_models import Claim, Critique, Dropped, Notes, PaperSummary, Summary

# Headings (numbering stripped) that start with one of these are front matter (Decision 3).
FRONT_WORDS: tuple[str, ...] = (
    "abstract", "introduction", "conclusion", "discussion", "limitation", "future work", "summary",
)
# Rounds of condensing before the notes are trimmed and the result marked body_cut (Decision 5).
MAX_CONDENSE_ROUNDS = 3
# Smallest context a paper is read in parts at: the config's floor for `llm_num_ctx`. Below it a
# chunk would be a few characters and the combine prompt could not fit its own instructions.
MIN_PARTS_CTX = 8192

Group = tuple[Focus, list[Section]]


def _budget(num_ctx: int) -> int:
    """Characters of paper text (or notes) one call may hold, as in pruning.fit_to_budget."""
    return max(0, (num_ctx - RESERVED_TOKENS) * CHARS_PER_TOKEN)


def _size(sections: list[Section]) -> int:
    return sum(len(s.title) + len(s.text) for s in sections)


# --- Grouping and chunking -----------------------------------------------------------------


def split_groups(sections: list[Section], num_ctx: int) -> tuple[list[Group], list[Dropped]]:
    """Front matter and body (Decision 3), and what was left out.

    Non-paper text is always dropped. Appendices join the body (or the single general group)
    only if body plus appendices fit one call's budget; otherwise they are dropped with reason
    ``appendix``. A subsection (``1.1``) goes with its parent (``1 Introduction``). If the
    headings give no front matter or no body, the whole paper is one ``general`` group, which
    is then read in size-based chunks. Only top-level sections (``4 Discussion``) and unnumbered
    ones are sorted by their heading words: a numbered subsection always goes with its parent,
    so ``4.3 Discussion`` under ``4 Experiments`` stays in the body.
    """
    paper, appendix, _ = partition(sections)
    budget = _budget(num_ctx)
    front: list[Section] = []
    body: list[Section] = []
    front_keys: list[str] = []
    for s in paper:
        num = _number(s.title)
        if num is not None and "." in num:  # a subsection: goes where its parent went
            (front if any(num.startswith(k + ".") for k in front_keys) else body).append(s)
        elif _words(s.title).startswith(FRONT_WORDS):
            front.append(s)
            if num is not None:
                front_keys.append(num)
        else:
            body.append(s)

    rest = body if front and body else paper
    keep_appendix = _size(rest + appendix) <= budget
    if keep_appendix:
        rest = rest + appendix
    groups: list[Group] = [("front", front), ("body", rest)] if front and body else [("general", rest)]

    # Everything not read, in paper order: non-paper text always, appendices when they did not fit.
    read = {id(s) for _, g in groups for s in g}
    is_appendix = {id(s) for s in appendix}
    dropped = [
        Dropped(title=s.title, chars=len(s.title) + len(s.text),
                reason="appendix" if id(s) in is_appendix else "non_paper")
        for s in sections if id(s) not in read
    ]
    return groups, dropped


def _pieces(s: Section, budget: int) -> list[Section]:
    """Cut a section over the budget into pieces that keep its title: at paragraph breaks
    (``\\n\\n``), else hard cuts. Pieces joined back give the original text."""
    room = max(1, budget - len(s.title))
    if len(s.text) <= room:
        return [s]
    paragraphs = s.text.split("\n\n")
    # Each paragraph keeps its separator, so nothing is lost; one too long is hard cut.
    units: list[str] = []
    for i, p in enumerate(paragraphs):
        p = p + ("\n\n" if i < len(paragraphs) - 1 else "")
        units.extend(p[j:j + room] for j in range(0, len(p), room))
    texts: list[str] = []
    cur = ""
    for u in units:
        if cur and len(cur) + len(u) > room:
            texts.append(cur)
            cur = ""
        cur += u
    if cur:
        texts.append(cur)
    return [Section(title=s.title, text=t) for t in texts]


def chunk_to_budget(sections: list[Section], num_ctx: int) -> list[list[Section]]:
    """Consecutive chunks of sections, each within one call's budget, filled greedily."""
    budget = _budget(num_ctx)
    chunks: list[list[Section]] = []
    cur: list[Section] = []
    for s in sections:
        for piece in _pieces(s, budget):
            if cur and _size(cur) + _size([piece]) > budget:
                chunks.append(cur)
                cur = []
            cur.append(piece)
    if cur:
        chunks.append(cur)
    return chunks


# --- Notes, condense, combine --------------------------------------------------------------

_EMPTY_BLOCK = len(notes_block([]))


def _notes_size(notes: list[Claim]) -> int:
    """Characters `notes_block(notes)` takes, without building the block for every subset."""
    lines = sum(len(notes_block([c])) - _EMPTY_BLOCK + 1 for c in notes)  # each line plus its "\n"
    return _EMPTY_BLOCK - 1 + lines if notes else _EMPTY_BLOCK


def _batches(notes: list[Claim], limit: int) -> list[list[Claim]]:
    """Consecutive batches of notes, each block within ``limit`` (a note over it goes alone)."""
    out: list[list[Claim]] = []
    cur: list[Claim] = []
    for c in notes:
        if cur and _notes_size(cur + [c]) > limit:
            out.append(cur)
            cur = []
        cur.append(c)
    if cur:
        out.append(cur)
    return out


def _trim(notes: list[Claim], limit: int) -> list[Claim]:
    """Shorten every note's text by the same share so the block fits; drop notes from the end
    if even that is not enough (their section lists alone overflow). Last resort only."""
    texts = [" ".join(c.text.split()) for c in notes]
    notes = [Claim(text=t, sections=c.sections) for t, c in zip(texts, notes, strict=True)]
    text_chars = sum(len(t) for t in texts)
    overhead = _notes_size(notes) - text_chars + len(TRIM_MARK) * len(notes)
    ratio = max(0.0, (limit - overhead) / text_chars) if text_chars else 0.0
    cut = [
        c if int(len(c.text) * ratio) >= len(c.text)
        else Claim(text=c.text[: int(len(c.text) * ratio)].rstrip() + TRIM_MARK, sections=c.sections)
        for c in notes
    ]
    while len(cut) > 1 and _notes_size(cut) > limit:
        cut.pop()
    return cut


def read_in_parts(
    llm: LLM,
    title: str,
    groups: list[Group],
    num_ctx: int,
    *,
    read_mode: Literal["split", "map_reduce"],
    dropped: list[Dropped],
    on_phase: Callable[[str], None] | None = None,
) -> PaperSummary:
    """Summary and critique of a paper read in parts.

    Each group is chunked to the budget and gets one notes call per chunk (``on_phase`` is told
    ``notes 2/3``); a notes call sees only its chunk's section titles. The notes, in paper
    order, are condensed in rounds while they are too long to combine (``condense``, at most
    `MAX_CONDENSE_ROUNDS`, then trimmed and marked ``body_cut``), then combined
    (``summary``, ``critique``). ``retry`` is told before a citation retry.
    ``dropped`` (what the caller left out) is recorded as given.
    Raises LlmError if the model fails, ValueError if there is nothing to read or ``num_ctx``
    is below `MIN_PARTS_CTX`.
    """
    if num_ctx < MIN_PARTS_CTX:
        raise ValueError(f"reading in parts needs a context of at least {MIN_PARTS_CTX} tokens, got {num_ctx}")
    chunks = [(focus, c) for focus, secs in groups for c in chunk_to_budget(secs, num_ctx)]
    if not chunks:
        raise ValueError("nothing to read in parts")
    titles = list(dict.fromkeys(s.title for _, secs in groups for s in secs))  # every section read
    of = len(chunks)

    def phase(name: str) -> None:
        if on_phase is not None:
            on_phase(name)

    retry = (lambda: on_phase("retry")) if on_phase is not None else None
    unverified: list[str] = []

    notes: list[Claim] = []
    for part, (focus, chunk) in enumerate(chunks, start=1):
        phase(f"notes {part}/{of}")
        chunk_titles = list(dict.fromkeys(s.title for s in chunk))
        got, bad = generate_checked(
            llm, *build_notes_prompt(title, chunk, focus, part, of), Notes, chunk_titles, retry
        )
        notes.extend(got.claims)
        unverified.extend(bad)

    # The combine prompt also lists the valid titles; the notes get the rest of the budget.
    limit = max(0, _budget(num_ctx) - len(json.dumps(titles, ensure_ascii=False)))
    body_cut = False
    rounds = 0
    while _notes_size(notes) > limit:
        if rounds == MAX_CONDENSE_ROUNDS:
            notes, body_cut = _trim(notes, limit), True
            break
        rounds += 1
        phase("condense")
        batches = _batches(notes, limit)
        condensed: list[Claim] = []
        for i, batch in enumerate(batches, start=1):
            got, bad = generate_checked(
                llm, *build_condense_prompt(title, batch, i, len(batches)), Notes, titles, retry
            )
            condensed.extend(got.claims)
            unverified.extend(bad)
        notes = condensed

    (s_sys, s_user), (c_sys, c_user) = build_combine_prompts(title, notes, titles)
    phase("summary")
    summary, bad_s = generate_checked(llm, s_sys, s_user, Summary, titles, retry)
    phase("critique")
    critique, bad_c = generate_checked(llm, c_sys, c_user, Critique, titles, retry)
    return PaperSummary(
        summary=summary,
        critique=critique,
        basis="full_text",
        body_cut=body_cut,
        needed_ctx=None,
        dropped=list(dropped),
        unverified=unverified + bad_s + bad_c,
        read_mode=read_mode,
        parts=of,
    )
