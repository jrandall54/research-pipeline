# Handoff — after Phase 6 parts A and C (2026-11-22)

Read this first in a new session. The master plan (Claude Docs, "Research Pipeline — Master Implementation Plan") is the source for what comes next; this note holds what the plan is too coarse for.

## State
- **`main` holds Phases 1-5, the review loop, the shelf, summarize-backlog, and the pipeline/app split design and plan.** `main` is pushed; CI runs on GitHub.
- **Phase 6 Parts A (progress feedback) and C (setting commands, category check, `rpipe show`, `my_work` boundary) are built and merged to `main` (2026-11-22).** `uv run pytest -q`: 994 passing; `ruff check .` clean. Part B (long papers) is not started; Part D is decided and recorded in the master plan.
- **Direction (owner, 2026-11-22):** a **pipeline** (gets material, unattended) plus an **app** (read, vote, notes, study, later research and thesis) over **one database**, split by function in code. Design `specs/2026-11-22-backend-frontend-split-design.md`, plan `plans/2026-11-22-pipeline-app-split.md` (not started). `library_api` returns data objects, not terminal text; the app's look is undecided.
- **Owner context:** philosophy BA finishing CS prerequisites, applying for a master's starting fall 2027. "Research" today means articles, repos and project ideas.

## Phase status (checked against the code 2026-11-22)
| Phase | Status |
|---|---|
| 1, 1.5 | Done. Their follow-ups (setting commands, category check) were closed by Phase 6 Part C. |
| 2 | Code built (`fulltext`, `llm`, `prompts`, `summarize`, `gold`, `evaluate`). Exit not met: the gold set is empty and no `rpipe eval` has run. |
| 3 | Code complete (votes/saves, Datasette, timer, run log, versioning, onboarding). "A week unattended" not yet observed. |
| 4 | Shelf built. Overlap alerts, archive connections, feasibility tag, weekly synthesis not built; `links` table unused. |
| 5 | Built and merged; Claude Code smoke test passed. AI club pilot not started. |
| 6 | Parts A and C built and merged. Part B waits on the owner's model verdict; Part D decided (master plan). |
| 7 | Planned; plan must be refreshed (see Next, item 4). |

## Next
1. **Start here (next session): review the owner's Google Neon document** (model choices and pipeline ideas), then brainstorm (superpowers:brainstorming) before any plan. It may change the master plan.
2. **Part B (long papers) is reframed as hardware-adaptive.** The pipeline is for other users too, so the design must not lock onto one local model; model and context choices adapt to each user's hardware. The owner's comparison `~/research-vault/model-comparison-2026-11-22.md` is one data point, not a pick: the same 3 long papers summarised by a 27B model at ~34K context (fits 33,792 tokens; truncated 2 of 3; 54-83 s/paper; 0 unverified citations) and a 14B model at ~143K context (fits 130,048 tokens; whole papers; 28-56 s/paper; 7 unverified citations). Candidate adaptive rules: `auto` prefers a model whose fitted context holds a typical paper; section-priority trimming or chunked summaries when context is short; a tighter citation retry for long contexts. Spec constraint carried forward: any change to `auto` must not silently move every user to a smaller model, must work on an empty vault, and is checked against the gold set or the comparison papers. Write Part B's plan after the brainstorm.
3. **Done 2026-11-22 (owner approved):** Part D recorded in the master plan (overlap alerts, archive connections and weekly synthesis to app Phase 9; feasibility tag to Ongoing; `links` table stays for Phase 9); `CLAUDE.md` gained the progress convention line.
4. **Refresh `plans/2026-11-22-pipeline-app-split.md` (Phase 7), then execute it.** What changed: `progress.py` is classified *shared*; `run_summarize`'s `on_progress` is gone (stages take a keyword-only `reporter`); per-paper summarize lines now go to stderr.

## Phase 6 check (real run, 2026-11-22, copy of the owner vault, ollama backend)
`rpipe run --vault <copy> 2>&1 | cat` (stderr merged): plain lines, 0 escape codes, 0 carriage returns.
```
embedding model: BAAI/bge-small-en-v1.5...
embedding model done (2s)
collect...
  arxiv: fetching
  arXiv page 1 (0 papers so far)
  arxiv ok: 100 papers
  hf_daily: fetching
  hf_daily ok: 50 papers
collect done (0s)
shelf...
shelf done (0s)
triage...
triage done (0s)
summarize...
summarize done (0s)
Digest written to <copy>/digests/2026-11-22.md
  inserted=0, updated=7, unchanged=143, source_errors=0, triaged=0, skipped=0, embedded=0, fetched=0, summarized=0, abstract_only=0, truncated=0, summary_failed=0
```
One-paper summarize (also plain):
```
summarize...
  writing the summary for 2611.25005
  writing the critique for 2611.25005
  retrying citations for 2611.25005
[1/1] 2611.25005 done (summary + critique, 80s)
summarize done (1m20s)
```
The first real run found the Hugging Face "Loading weights" bar and an HF_TOKEN warning; fixed in 9686858.

## Phase 6 inputs
Done on branch `phase-6-hardening`: Part A, the progress-feedback list (every waiting step reports; shared reporter, spinner on a terminal, plain lines otherwise); Part C, the setting commands for `arxiv_max_results` and `embedding_model` (`rss_feeds` got no command: it was dropped from the profile template), the arXiv category check with did-you-mean, `rpipe show`, and the `my_work` boundary (shelf text never reaches a model; tested).

### Long papers (Part B, owner, 2026-11-22)
Real Ollama summary of 2611.25005 was fast and "decent" but **truncated**. Order to try: (1) a longer context window (`rpipe model fit` / `rpipe model set-context`; a long-context local model), (2) chunked summarize-and-critique (map-reduce by section, merge overlapping claims, keep section citations; measure with `rpipe eval`). Watch the health line's `truncated N` before building (2). See Next, item 2.

### Open items
- **Owner setup:** the repo's own dev env needs `uv sync --extra embed` (or `uv run --extra embed`) for real runs from the checkout. Reinstall the tool with extras (`uv tool install --editable '.[embed,serve]' --force`), `rpipe schedule status`, `loginctl enable-linger $USER`, add seed papers (`rpipe profile suggest "..."` or `rpipe profile add-seed`), start voting, fill the gold set (`rpipe gold add`, `rpipe eval run|grade|report`), run `rpipe summarize --backlog` for the 12 unsummarized 2026-11-21 papers then `rpipe digest --date 2026-11-21`.
- **Switching `embedding_model`** drops the feedback boost of earlier votes/saves until switched back (documented, not fixed).
- Check `SHELF_REASON_FLOOR` against real bge-small scores.
- Deferred minors from Phase 6 reviews: fit/check failure printed twice; `show ID --recent` may print a paper twice, no ambiguous-title test; `test_categories` imports fixtures from `test_e2e`; LiveRenderer total fixed at start; `rpipe show --recent` needs a number (the design said default 1); model probe lines in `run`/`summarize`/`eval run` still print outside a progress stage (no heartbeat). Older: no config-to-digest e2e test for plug-in sources; `ollama_fit` deadline-vs-does-not-fit decided by clock comparison.

## Separate projects (not scheduled)
- Optimised CPU pipeline (own brainstorm first). Antigravity (`agy`) backend investigation. Codex adapter (needs a real-install check; cannot switch tools off). Copilot / Cursor / Kiro adapters (`specs/2026-11-21-cli-backends-research.md`). Schedulers other than systemd (macOS launchd, Windows).
- Not planned: Docker, API-key backends.

## Rulings worth remembering
Phase 3:
- The 1024-token margin is kept even at the full trained context: it guards memory fluctuation, not just overflow (llm.md).
- Executed without a separate plan-review pause, on a branch in place (no worktree); merge is the owner's call.
- Digest: id shown per item, the vote hint once in the footer (`digest.md`).
- Implicit profile uses the NEAREST liked/disliked paper (max of weight x cosine), not an average, which dilutes with history (`triage.md`). Weights 0.20 liked, 0.15 disliked; a reason shows only at raw cosine >= 0.70.
- Timestamps parsed tolerantly (ISO `T`/`Z` and `YYYY-MM-DD HH:MM:SS`); SQL inserts use `db.py`'s timestamp format.
- `runs.errors` keeps its shape, a list of `<stage>: <message>` strings (`running.md`).
- Migration ladder keyed on `user_version`, no migrations framework (`data-model.md`).
- `rpipe serve` allows only localhost, loopback and Tailscale hosts (allowlist, not blocklist); `datasette` pinned `>=0.65,<1` because 1.0 moved canned queries to `datasette.yaml` (unverified) (`browsing.md`).
- Votes are append-only; a down vote beats a save of the same paper.

Parked-fixes branch segment:
- Run lock covers only `rpipe run` today; Phase 7 Task 1 widens it to every pipeline command.
- `save_summary` provenance stays optional; the digest footer tolerates missing fields.
- Locked run exits 75; unit file unchanged.
- Probing deadlines: fit/run/check 900 s, `model show` 10 s, fixed constants.
- Pinned num_ctx is cached as given; the 8192 floor applies to auto-chosen values only.

From before:
- A pinned model needs to fit at 8192 only; an auto-chosen one at 16384 or more. `llm_num_ctx` floor is 8192.
- Summary retries: same-day recovery, cap 3 (`MAX_SUMMARY_TRIES`); a failure left to a later day is not retried.
- Hidden-text stripping has documented gaps (`fulltext.md`).

Phase 6:
- Per-paper summarize lines (`[2/3] id done (41s)`) now go to stderr with all other progress; "stdout unchanged" means the non-progress output. Cost if wrong: a script grepping stdout for those lines (none known).
- Notes (model-fit warnings) stay as permanent lines on a terminal as well as in the spinner text, so they never vanish.
- Category check: a small hand-kept table of common mistakes (`cs.ML` means `cs.LG`) runs before difflib, because difflib alone cannot make that suggestion.
- The category list was written from knowledge/arXiv's taxonomy page; it could miss a code.
- Commit trailers on this branch keep the Opus 5.5 line with this session's URL.
- Switching `embedding_model` drops the feedback boost of earlier votes and saves until you switch back (`rank.py` skips papers without current-model vectors). Documented, not fixed.



## Process notes
- Plans are executed subagent-driven (implementer, per-task review, fix rounds). Plan checkboxes were never ticked in executed plans; git history is the record.
- Future plans should mark which tasks can run in parallel and which files each task owns.
- Project rules are in `CLAUDE.md`: docs in the same commit as code, and every user setting needs an `rpipe` command.
- The owner's personal Ollama helper script is not a dependency; never call it from the program, and keep the owner's model names out of the repo.
