# Phase 9.2 — Diagrams on the paper screen only: Implementation Plan

> **Phase 9.2** · Status: merged · Design: none (small phase; owner's choice 2026-11-25 after Phase 9.1's measurements) · Roadmap: [ROADMAP.md](../ROADMAP.md)

**Goal:** Even after Phase 9.1, the model draws a diagram for nearly every paper; literature reviews get their review process and theory papers a chain of derivation steps. The owner chose to keep diagrams but show them only on the paper screen ("How it works"), never on feed cards, so a dull diagram never takes space in the feed.

**Options put to the owner:** paper screen only (chosen, Claude's recommendation); keep them in Read mode on cards; turn them off by default.

## Tasks

- [x] `templates/_card.html`: no diagram on cards. The paper screen is unchanged.
- [x] Test: `test_card_has_no_diagram_paper_page_does` (replaces `test_card_diagram_inside_read_more`).
- [x] Docs: `docs/guide/app.md` (Skim and Read; Hook, key numbers and the diagram: what, why, rejected, how to check), `docs/guide/glossary.md` (Skim / Read), roadmap row, handoff.
