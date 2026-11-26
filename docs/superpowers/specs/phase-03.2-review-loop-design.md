# `rpipe review`: a terminal loop for reading and voting (design)

> **Phase 3.2** · Status: approved, built · Plan: [phase-03.2-review-loop.md](../plans/phase-03.2-review-loop.md) · Roadmap: [ROADMAP.md](../ROADMAP.md)

Status: approved by the owner on 2026-11-22. Size: small change (one task plus review).

## Intent and why
The owner reads the pipeline's output on a headless server over SSH. Today the only ways to read are a markdown file and a database browser, and voting means copying ids. Votes are the data the implicit profile learns from, so the loop of read, vote, better ranking is not working yet. `rpipe review` walks through unreviewed papers one at a time so reading and voting happen in one place, and gets votes into the system.

## Goals
- One command that shows each unreviewed digest paper (title, why it matched, summary or abstract, link) and takes up / down / skip / quit.
- Oldest unreviewed digest first, so no day is ever left behind, even if the owner skips everything.
- Skips are their own tracked state ("review later"), never a vote or a save, so they do not affect ranking.
- A second mode, `rpipe review --later`, to work through skipped papers when there is time.

## Non-goals
- No web UI (a reader page for `rpipe serve` is a later, separate piece of work).
- No undo key, no tagging or sorting of the later list, no per-paper notes.
- No change to ranking, triage or the digest file.

## Anti-goals
- A skip must not count as a signal about the paper. A skipped paper must not reappear at the head of every session.

## Approaches considered
1. **New `skips` table plus `rpipe review` (chosen by the owner).** Skip is its own record. Clean, and skips never touch ranking.
2. Reuse `saves` with a flag. Rejected: blurs "I like this" with "ask me later" and would skew the ranking.
3. Leave skipped papers unvoted with no record. Rejected: with oldest-first order the same papers would come back first every session.

## Design
- **Queue (`rpipe review`):** every paper with a non-null `digest_date` that has no vote and no skip, ordered by `digest_date` ascending, then in digest order within the day (score descending, then id). "No vote" means no row in `votes` at all (a `clear` vote row counts as a vote that was cleared: treat the paper as unvoted only if its latest vote is a clear? Builder's call, document it).
- **Later queue (`rpipe review --later`):** papers in `skips` whose latest vote state is "no active vote", oldest skip first. Voting removes the row from `skips`; skipping again refreshes `skipped_at`.
- **Per-paper display:** `Digest <date> · paper N of M · V voted, S skipped`, then title, why it matched (the stored reasons), the summary if one exists (else the abstract), and the arXiv abs link. Keys: `u` up, `d` down, `s` skip, `q` quit. Invalid keys re-prompt. Votes use the existing vote function so they feed the implicit profile like `rpipe vote`.
- **End / quit:** print a tally (voted, skipped) and how many remain in the queue.
- **`rpipe feedback`:** also shows how many papers wait in "review later".
- **No terminal:** if stdin is not a TTY, refuse with a message pointing at `rpipe vote ID up|down`.
- **Storage:** new table `skips(paper_id TEXT PRIMARY KEY REFERENCES papers(id), skipped_at TEXT)`, added with the existing `user_version` migration ladder in `db.py` (bump `SCHEMA_VERSION`). Skipping an unknown paper id raises the same `UnknownPaperError` pattern votes use. Timestamps use `db.py`'s format.
- **Docs (same commit as code):** new `docs/guide/review.md` (What it does / How it works / Why / How to run / Where the code is), plus updates to `data-model.md`, `running.md`, `glossary.md` (skip, review queue), `digest.md` where it points at voting, `docs/guide/README.md` index, and `docs/superpowers/archive/handoff-log-to-2026-11-25.md`.

## Constraints
`CLAUDE.md` rules: docs in the same commit, every setting settable through `rpipe` (this feature adds none), offline tests (`uv run pytest -q`), `rpipe` naming, nothing personal in the repo.

## Left to the builder
Module layout (suggest `review.py` for queue logic, kept free of terminal I/O so it is testable, with the prompt loop in `cli.py`); exact wording of prompts; how a `clear` vote is treated (document the choice).
