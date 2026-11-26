# Phase 8.1 — Readable text in the app: Implementation Plan

> **Phase 8.1** · Status: merged · Design: none (small phase; decided in the Phase 9 design conversation, 2026-11-25) · Roadmap: [ROADMAP.md](../ROADMAP.md)

**Goal:** The owner found the app's summary text hard to read: each field ("The problem", "What they did", "What they found") is one dense block with little space around it. Fix the layout only. The wording itself (long, jargon-heavy model output) is Phase 9's job, because changing it means changing prompts and re-checking the gold set.

**Approach (Claude's call, owner agreed to the split):**

- Each field is its own block: the label on its own line, then the text.
- A field of two or more sentences is shown as a short list, one sentence per point, so no block runs past about three lines on a phone. The section tag goes after the last point.
- Sentences are split by a careful rule (`present.sentences`): a break only after `.`, `!` or `?` followed by a space and a capital letter, digit, bracket or quote, and never after common abbreviations (`e.g.`, `i.e.`, `et al.`, `vs.`, `Fig.`, `Eq.`, `Sec.`, …) or a single initial (`J.`). When unsure it keeps the text together: a missed split leaves one block (today's behaviour), a wrong split would cut a sentence in two.
- More breathing room: taller line height for summary text, more space between fields, a thin rule above the Read fields on a card, more space between critique points.

**Rejected:** asking the model for bullet points (a prompt change; Phase 9); a "show more" fold on each field (one more tap for text already on screen; Read mode is the fold).

## Tasks

- [x] `present.sentences(text) -> list[str]` with tests (abbreviations, initials, decimals like `1.37x`, one sentence, empty text).
- [x] Registered as the Jinja filter `sentences` in `deps.py`; a shared macro `_field.html` renders one field; `_card.html` and `paper.html` use it.
- [x] CSS spacing in `app.css`.
- [x] Page tests: a two-sentence field renders as a list with the tag on the last point; a one-sentence field stays a paragraph.
- [x] Docs: `docs/guide/app.md` (Skim and Read, paper screen), glossary not needed (no new terms).
