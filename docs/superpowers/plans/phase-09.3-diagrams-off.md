# Phase 9.3 — Diagrams off: Implementation Plan

> **Phase 9.3** · Status: merged · Design: none (small phase; owner ruling 2026-11-26) · Roadmap: [ROADMAP.md](../ROADMAP.md)

**Goal:** After the first nightly run with diagrams (2026-11-26), the owner found them pointless: labels in boxes with arrows, drawn from the model's reading of the paper, repeating "What they did". Turn them off. Diagrams worth having would be the paper's own figures, tables and graphics brought into the app; that touches PDFs and images and is a separate future phase, not this one.

**What changes:**

1. `rpipe model set-diagrams off` on the owner's machine (done 2026-11-26): no new diagrams are drawn.
2. The app's paper route hides stored diagrams while the setting is `off` (`_diagrams_off` in `webapp/routes_pages.py`, read from `config.yaml` per request). Nothing is deleted.
3. Tests: `tests/test_webapp_pages.py` (off hides, on/auto show, an unreadable config still shows the page).
4. Docs: `app.md`, `llm.md`, `glossary.md`.

**Not in this phase:** removing the diagram code; the summary display problem found the same day (hook replaces the one-line summary, Skim hides the fields, Limitations never shown). That is the next phase.
