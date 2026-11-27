# Phase 9.4 — Lead with what the paper is about: Implementation Plan

> **Phase 9.4** · Status: merged · Design: none (small phase; owner request 2026-11-27) · Roadmap: [ROADMAP.md](../ROADMAP.md)

**Goal:** The feed is for a quick "is this worth opening?". Until now, a paper with a hook showed the hook *instead of* the one-line summary, with the key numbers as chips right under it. Owner's example (2026-11-27, "Reading, Not Adapting"): "ReMoT achieves 91.2% average accuracy with only 3,074 trainable parameters.", then chips repeating 91.2% and 3,074. After reading only the title, those numbers mean nothing. Papers with no hook showed the one-line summary, which works.

**What changes:**

1. `templates/_field.html`: `headline` always prints the one-line summary. New macro `key_numbers` prints the chips.
2. `templates/paper.html`: the chips come after "What they found" and before "How solid is it?". Cards show no chips.
3. The hook is no longer shown in the app (it repeats "What they found"). It is still generated, checked and stored, and the digest shows it after `**Summary:**` (`digest.render_summary`).
4. `static/app.css`: the unused `.hook` rule is removed.
5. Tests: `test_webapp_feed.py`, `test_webapp_pages.py`, `test_digest.py`.
6. Docs: `app.md`, `digest.md`, `glossary.md`, `summaries.md`.

**Not in this phase:** changing the summary prompt or stopping hook generation; topic labels that don't fit the paper (seen the same day: an SfM paper labelled "Model weight compression").
