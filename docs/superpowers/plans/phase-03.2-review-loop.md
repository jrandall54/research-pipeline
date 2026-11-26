# `rpipe review` Implementation Plan

> **Phase 3.2** · Status: merged · Design: [phase-03.2-review-loop-design.md](../specs/phase-03.2-review-loop-design.md) · Roadmap: [ROADMAP.md](../ROADMAP.md)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A terminal loop (`rpipe review`, `rpipe review --later`) that shows each unreviewed digest paper and records up / down / skip, with skips tracked as their own "review later" list.

**Architecture:** A new `skips` table (schema version 2, via the existing `user_version` ladder in `db.py`). Pure queue logic in a new `review.py` (no terminal I/O), and a thin prompt loop in `cli.py`. Votes reuse `db.record_vote`, so they feed the implicit profile like `rpipe vote`.

**Tech Stack:** Python, sqlite3, typer (CliRunner for CLI tests). Tests offline: `uv run pytest -q`.

**Spec:** `docs/superpowers/specs/phase-03.2-review-loop-design.md`

## Global Constraints

- Branch `review-loop`; one commit per task; do not merge or push.
- Docs in `docs/guide/` change in the same commit as the behaviour (page shape: What it does / How it works / Why it's built this way / How to run / Where the code is). New terms go in `docs/guide/glossary.md`.
- Use `rpipe` in docs and messages. Nothing personal in the repo. This feature adds no user setting.
- TDD, genuinely red-first. `uv run pytest -q` stays green (baseline 623).
- Timestamps use `db._now()` format. Unknown paper ids raise `UnknownPaperError` via `db._require_paper`.
- "Unvoted" means the paper has no active vote: no `votes` row, or its latest row has `vote == 0` (cleared). Document this in `review.md`.

## Review Focus

- A paper whose vote was cleared returns to the queue (it has no active vote).
- A skipped paper never appears in the main queue, and leaves the later queue once it has an active vote.
- Skipping a paper again in `--later` mode moves it to the back of the later queue (`skipped_at` refreshed).
- Digest with zero unreviewed papers: `rpipe review` prints "Nothing to review" and exits 0.
- Piped/non-terminal stdin: refuse with exit 1 and a message naming `rpipe vote`.
- A paper with no summary shows its abstract; one with an empty abstract shows "No abstract available."
- An old database (version 1) opens, gains the `skips` table and reports version 2; a version-3 database is still refused by the existing guard.

---

### Task 1: Skips storage and queue logic

**Files:**
- Modify: `src/research_pipeline/db.py` (schema, `SCHEMA_VERSION = 2`, `_migrate`, skip helpers)
- Create: `src/research_pipeline/review.py`
- Test: `tests/test_db.py`, create `tests/test_review.py`
- Docs: `docs/guide/data-model.md`, `docs/guide/glossary.md`

**Interfaces:**
- Produces in `db.py`:
  - table `skips(paper_id TEXT PRIMARY KEY REFERENCES papers(id), skipped_at TEXT NOT NULL)` added to `SCHEMA`
  - `record_skip(conn, paper_id: str, now: str | None = None) -> None`: upsert (refresh `skipped_at`), commits, raises `UnknownPaperError` for a bad id
  - `clear_skip(conn, paper_id: str) -> None`: delete the row if present, commits
- Produces in `review.py`:
  - `review_queue(conn) -> list[sqlite3.Row]`: papers with `digest_date IS NOT NULL`, no active vote, not in `skips`; ordered by `digest_date ASC, triage_score DESC, id ASC`; rows are full `papers` rows
  - `later_queue(conn) -> list[sqlite3.Row]`: papers in `skips` with no active vote, ordered by `skipped_at ASC, id ASC`; full `papers` rows
  - `queue_progress(conn, digest_date: str) -> tuple[int, int]`: `(handled, total)` for that digest date, where total is papers with that `digest_date` and handled is those with an active vote or a skip (used for the "paper N of M" line)

- [ ] **Step 1: Write failing tests** in `tests/test_db.py`: `test_open_db_upgrades_v1_to_v2` (build a v1 file by creating the old tables and `PRAGMA user_version = 1`, open with `open_db`, assert `skips` exists and `user_version == 2`); `test_record_skip_unknown_paper_raises`; `test_record_skip_twice_refreshes_timestamp` (pass explicit `now` values); `test_clear_skip_is_idempotent`. In `tests/test_review.py`: `test_queue_oldest_digest_first` (papers across three `digest_date`s, assert order, and within a day by score desc then id), `test_queue_excludes_voted_and_skipped`, `test_cleared_vote_returns_to_queue`, `test_queue_ignores_papers_never_in_a_digest`, `test_later_queue_orders_by_skip_time`, `test_later_queue_drops_paper_once_voted`, `test_queue_progress_counts`. Use the existing helpers/fixtures in `tests/` for inserting papers; set `digest_date` through `db.mark_in_digest` or direct SQL.
- [ ] **Step 2: Run them; confirm they fail** (`uv run pytest tests/test_db.py tests/test_review.py -q`).
- [ ] **Step 3: Implement** the table, `SCHEMA_VERSION = 2` with a `from_version < 2` step in `_migrate` (a comment only is fine because `CREATE TABLE IF NOT EXISTS` makes the table), the two helpers, and `review.py`. One SQL query per function; the active-vote test is "latest `votes` row for the paper has `vote != 0`", matching `db.current_votes`.
- [ ] **Step 4: Run the full suite**; green.
- [ ] **Step 5: Docs.** `data-model.md`: add `skips` to the table list, describe it, note schema version 2 and the migration step, and the "active vote" rule. `glossary.md`: **Skip**, **Review queue**, **Review later**.
- [ ] **Step 6: Remove nothing from handoff; commit** `feat: skips table and review queues` with the two trailer lines the session requires.

### Task 2: `rpipe review` command, feedback count, docs

**Files:**
- Modify: `src/research_pipeline/cli.py` (new `review` command; `feedback` prints a review-later count), `src/research_pipeline/digest.py` (rename `_render_summary` to public `render_summary`, update its callers; no behaviour change)
- Create: `docs/guide/review.md`, `tests/test_cli_review.py`
- Modify docs: `docs/guide/README.md` (index), `docs/guide/running.md` (command list and a short example), `docs/guide/digest.md` (point the voting section at `rpipe review`), `docs/superpowers/archive/handoff-log-to-2026-11-25.md` (Done section; mention the reader-page idea for `rpipe serve` as the next UI item)

**Interfaces:**
- Consumes: `review.review_queue`, `review.later_queue`, `review.queue_progress`, `db.record_vote(conn, paper_id, vote)`, `db.record_skip`, `db.clear_skip`, `db.get_summary`, `digest.render_summary(ps) -> list[str]`, `cli._is_tty()`, `cli._open(vault)`, `cli._fail(msg)`.
- Produces: `rpipe review [--later] [--vault PATH]`.

**Behaviour (exact copy and rules):**
- Not a terminal (`_is_tty()` false): `_fail("rpipe review needs a terminal. Use `rpipe vote ID up|down` instead.")`, exit 1.
- Empty queue: print `Nothing to review.` (for `--later`: `Nothing waiting in review later.`) and exit 0.
- Header per paper: `Digest <digest_date> · paper <handled+1> of <total> · <V> voted, <S> skipped` (V, S counted for this session), a blank line, title, `id: <id>`, `why: ` and the stored reasons joined with `; ` (omit the line if none), blank line, the summary (via `render_summary`) if `get_summary` returns one, otherwise the abstract (`No abstract available.` if empty), blank line, the paper's `url`. In `--later` mode use the header `Review later · <i> of <n> · <V> voted, <S> skipped` instead.
- Prompt: `typer.prompt("[u]p  [d]own  [s]kip  [q]uit")`; accept the first letter, case-insensitive (`u`, `d`, `s`, `q`); anything else prints `Please answer u, d, s or q.` and asks again. Line input (Enter required) so it is testable.
- `u`/`d`: `record_vote(conn, id, 1 or -1)` then `clear_skip(conn, id)`. `s`: `record_skip(conn, id)`. `q`: stop.
- End (queue exhausted or `q`): print `Voted <V>, skipped <S> this session. <N> left in the queue.` where N is the length of the same queue recomputed (for the main queue) or of `later_queue` (for `--later`). Closing the connection in `finally`.
- `rpipe feedback`: add a final section `Review later` listing the count: `  <n> papers waiting. Run `rpipe review --later`.` or `  Nothing waiting.`

- [ ] **Step 1: Write failing CLI tests** in `tests/test_cli_review.py` using `CliRunner` and the same vault/DB fixture style as `tests/test_cli_feedback.py`, monkeypatching `cli._is_tty` to True: `test_review_votes_and_skips_in_order` (three papers over two digests; input `u\nd\ns\n`; assert votes recorded, skip recorded, oldest digest first, tally line with the exact copy); `test_review_quit_leaves_rest` (`q\n`); `test_review_invalid_key_reprompts` (`x\nu\n`, message appears once); `test_review_requires_terminal` (default non-tty; exit 1 and the exact message); `test_review_empty_queue` (exit 0, `Nothing to review.`); `test_review_shows_summary_else_abstract` (one paper with a stored summary via `db.save_summary`, one without); `test_review_later_votes_remove_skip`; `test_review_later_skip_again_moves_to_back`; `test_feedback_shows_review_later_count`.
- [ ] **Step 2: Run them; confirm they fail.**
- [ ] **Step 3: Implement** `review` in `cli.py` as a thin loop over `review.py`, rename `_render_summary` to `render_summary` in `digest.py` (grep all callers, including tests), extend `feedback`.
- [ ] **Step 4: Run the full suite**; green.
- [ ] **Step 5: Docs.** Write `docs/guide/review.md` in the required shape: worked example with a short transcript, the queue rules (oldest digest first, active-vote rule, why skips are their own table and never affect ranking), and the non-terminal refusal. Update the other pages listed above and the handoff.
- [ ] **Step 6: Commit** `feat: rpipe review loop with a review-later list` with the two trailer lines the session requires.

### Task 2 addendum (carried from Task 1 review, Minor)

- `docs/guide/data-model.md` "Foreign keys and paper ids": add `skips` to the list of FK tables and note `record_skip` raises `UnknownPaperError`.
- `tests/test_review.py`: pin that a paper with NULL `triage_score` in a digest sorts last within its day (add a test).
- Document in `docs/guide/review.md` that `digest_date` stays set when a paper later falls back to `triaged` on a new version, so it stays eligible for the queue (intended).
