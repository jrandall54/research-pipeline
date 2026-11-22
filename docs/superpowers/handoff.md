# Handoff — pipeline/app split designed and planned (2026-11-22)

Read this first in a new session.

## State
- **`main` now holds Phase 5 and `summarize-backlog`** (fast-forward merge 2026-11-22, 897 tests passing). Before merging, an independent review of `summarize-backlog` found 2 Important issues, both fixed: quota is now checked before structured-output failure in `backends/claude_code.py`, and `rpipe summarize` lists `rpipe digest --date D` for older digests that gained summaries. Minor fixes: empty-backlog message, `--refetch` keeps stored text on a network error. Not pushed.
- **New direction (owner, 2026-11-22):** the project is a **pipeline** (gets material, unattended) plus an **app** (uses material: read, vote, notes, study, later research and thesis), over **one database**, split by function in code. Design: `specs/2026-11-22-backend-frontend-split-design.md` (approved; a two-database version was drafted and dropped at the owner's push-back). Plan: `plans/2026-11-22-pipeline-app-split.md` (6 tasks, not started).
- **Master plan (Claude Docs) updated:** new "Pipeline and app" section with a diagram; Datasette marked a stopgap, Open WebUI dropped; added dimensions sorted into pipeline vs app; roadmap now Phase 6 pipeline hardening (next), Phase 7 the split (this plan), Phases 8–11 the app (read and vote; library and notes; study, using the teacher's layered templates and concept tree; research and thesis), each to be brainstormed on its own first.
- **Owner context:** philosophy BA, finishing CS prerequisites, applying for a master's starting fall 2027. "Research" today means articles, repos, project ideas they find. They want a good-looking, easy app eventually; nothing about its look is decided, and `library_api` must return data objects, not terminal text.

## Next
1. Phase 6 (pipeline hardening): brainstorm and plan it. Items: verify the Ollama path for real (owner's quick test said "decent"; paper was truncated), progress feedback everywhere (sketch below), long papers (bigger context first, chunked summaries second), owner setup, and where Phase 4's remaining slices go.
2. Then execute `plans/2026-11-22-pipeline-app-split.md` (subagent-driven recommended). Task 6 asks the owner before editing CLAUDE.md.
3. Owner action still open: remove the canary line, `sed -i '/PINEAPPLE-CANARY/d' ~/.claude/CLAUDE.md`.

---

# Earlier: Handoff — Phase 5 (Shareable) built, awaiting owner merge (2026-11-21; updated 2026-11-22)

Read this first in a new session.

## State
- **Phase 5 is done on branch `phase-5-shareable` (11 commits ahead of `main`). NOT merged; the owner merges.** `uv run pytest -q` gives 827 passing. Design `specs/2026-11-21-phase5-shareable-design.md`, plan `plans/2026-11-21-phase5-shareable.md`, research `specs/2026-11-21-cli-backends-research.md`.
- What shipped:
  - **Backends**: `llm_backend` is `ollama` (default, unchanged), `claude-code` (subscription CLI, headless, all tools off) or `none` (abstracts only). `rpipe model set-backend`, `rpipe model setup` (hardware-aware walkthrough, also the first step of `rpipe init`; re-running keeps your backend unless you say no). Usage-limit stop (`LlmLimitReached`): remaining papers wait for the next run.
  - **Plug-ins**: entry-point groups for sources and backends (`registry.py`); `rpipe plugins`, `rpipe sources list|enable|disable`. A missing or broken plug-in is reported in the health line and the digest is still written.
  - **Machine detection** (`machine.py`): GPU/RAM/Ollama/Claude detection and a recommendation; `rpipe` never installs Ollama and pulls a model only after a yes/no.
  - **Packaging**: MIT licence, stranger's README, CI (`.github/workflows/tests.yml`), `rpipe schedule` refuses off Linux.
  - Docs: `docs/guide/backends.md` (incl. how to add a backend), `setup.md`, plus llm, running, profile, sources, plugins, summaries, glossary.
- Process: subagent-driven; Tasks 3 and 6 each needed one fix round; the final whole-branch review found 0 Critical, 4 Important (3 fixed in one wave, the 4th is the owner smoke test below).

## Added after Phase 5 (branch `summarize-backlog`, 4 commits on top of `phase-5-shareable`; NOT merged; merge `phase-5-shareable` first)
- `rpipe summarize --backlog [--limit N]` (default 10; past-digest papers with no summary, newest first) and `rpipe summarize <id>...` (any status; replaces an existing summary); `--refetch` re-downloads the full text first.
- `rpipe model set-effort low|medium|high` (default `low`) for Claude Code; `rpipe summarize` prints a progress line per paper.
- Full-text cleanup: LaTeXML math and `\materiaDisplay`-style heading residue no longer leak into text (`fulltext.py`). Stored full texts from before are stale until `--refetch`.
- Prompts now cap claim counts and lengths (`PROMPT_VERSION` 2026-11-p4); `_classify` no longer reports structured-output failures as "usage limit reached".
- Measured 2026-11-22 (Haiku via Claude Code): critique calls went from 70-127 s to about 30 s per paper. `--effort low` is a hint, not a cap: Haiku still thinks for thousands of tokens about 1 call in 4.
- **Phase 5 smoke test passed:** a canary line in `~/.claude/CLAUDE.md` did not leak into any summary (`--setting-sources ""` works). Owner still needs to remove the line: `sed -i '/PINEAPPLE-CANARY/d' ~/.claude/CLAUDE.md`.
- Not yet reviewed by an independent reviewer (only the per-feature tests): run a review before merging `summarize-backlog`.

## NOT verified on the default tier (do before merging)
Everything run for real on 2026-11-22 used the **claude-code** backend. The **Ollama** path (the default tier) has only offline tests with a fake model. Test it for real: `rpipe model set-backend ollama`, `rpipe model check`, `rpipe summarize 2611.25005`, read the result, and judge the new prompts (`PROMPT_VERSION` 2026-11-p4, claim-count limits), the factory (`backends`/`build_backend`) and time per paper on the local model. The nightly timer uses whichever backend is configured (currently claude-code, so it spends plan quota).

## Owner requirement: no step may look frozen (project-wide, not built)
Any step that waits (network, model, processing) must show visible progress: a spinner or progress bar with elapsed time and, where the total is known, a count (`[2/3] ...`). Today only `rpipe summarize` prints a line per paper, and it is silent *during* a paper (~30 s on Claude Code, longer on slow models).
Sketch for a design/plan (brainstorm first, then write a plan):
- Audit every waiting step: collect (arXiv 3 s spacing, HF), seed lookup, full-text fetch, embedder load and embedding, triage, summarize (per paper and per call), `model fit` (minutes), `model pull`, `model check`, eval run/grade, shelf sync, digest, `schedule`, `serve` start.
- One shared helper (a single module) so behaviour is consistent: a spinner/bar with elapsed time on a terminal; plain one-line-per-event output when stderr is not a TTY (systemd timer, pipes, logs) so the nightly log stays readable. Progress goes to stderr so stdout stays parseable.
- Tests: the helper is unit-tested offline; each stage gets a test that it reports progress via the callback.
- Docs: a "progress" section in `docs/guide/running.md`; add the convention to CLAUDE.md so future steps follow it (ask the owner before editing CLAUDE.md).

## Ollama test result and long-paper idea (owner, 2026-11-22, not built)
- Real Ollama summary of 2611.25005: much faster than Claude Code; "decent" at a quick glance (not yet judged in depth). The digest noted the paper was **too long for the model** (truncated).
- Owner's two options, in order of preference to try:
  1. A **longer context window** for the local model (`rpipe model fit` / `set-context`; the pinned 143K-context model `q3f-14b-143k` was the earlier cheap option).
  2. **Chunked summarize-and-critique** (map-reduce): split the paper into parts, run summary and critique on each, then **merge the partial results**: where parts overlap they confirm or deduplicate each other, where they do not overlap they fill in what the other is missing. Needs its own brainstorm and design: how chunks are cut (by section), how overlapping claims are matched, how citations (section titles) survive the merge, cost (more calls, weaker cross-section critique), and how to measure it with `rpipe eval` and the gold set. See also the older "Watching: truncation" section below.
- Look at the `truncated N` count in the health line over a few runs to see how often it matters before building option 2.

## Next up (owner asked for, not built)
1. `rpipe show <id>...` (print one paper's summary and critique, no voting; accepts ids or title fragments) and `rpipe show --recent`.
2. A reader page in `rpipe serve` (it shows raw JSON today). `rpipe review` is a vote loop, not for reading.
3. Run `rpipe summarize --backlog` for the other 12 unsummarized digest papers, then `rpipe digest --date 2026-11-21`. Summaries live in `~/research-vault/digests/2026-11-21.md`.

## Owner actions (not done)
1. **Smoke-test the Claude Code adapter on a real install** (tests are offline and cannot prove this): put a canary line in `~/.claude/CLAUDE.md`, run `rpipe model set-backend claude-code` then `rpipe summarize`, and confirm (a) summaries come back, (b) the canary does not leak into prompts or output, (c) the JSON envelope keys (`structured_output`, `is_error`) parse. If the canary leaks, add `--safe-mode` to the argv in `backends/claude_code.py` if your `claude --help` lists it.
2. Review and merge `phase-5-shareable` into `main`.
3. Reinstall the tool with extras: `uv tool install --editable '.[embed,serve]' --force`, then `rpipe model setup` to record a backend, then `rpipe schedule status`.
4. Confirm the GitHub repo is public before sharing the README (its install line is an https git URL), and push to see CI run (action tags `checkout@v4`, `setup-uv@v5` were not checked online; no uv cache).
5. Carried over from earlier handoffs: start voting; `rpipe profile suggest "<describe your research>"`; `rpipe model fit`.

## Separate projects (not Phase 5)
- **Optimised CPU pipeline** (no GPU, no subscription): needs its own brainstorming session.
- **Antigravity (`agy`) investigation**: make `agy` a safe backend. Gemini Pro is free for students with a school email, so tier 2 would be free for them. Open problems: no-TTY hangs, no tools-off switch, long prompts need `--input-format stream-json`, consumer-plan wording on wrapping. See the research file.
- **Codex adapter**: waits for someone to verify it on a real install; it cannot switch tools off.
- Docker and API-key backends: **not planned**.

## Known deferred minors (from reviews; none block merge)
- `require_local` exists but is not wired anywhere: shelf text goes to no model today. Wire it before any feature sends shelf content to a model.
- `cli.make_arxiv` duplicates `registry.make_arxiv`; duplicate third-party entry-point names overwrite silently; no single config-to-digest e2e test for plug-in sources.
- rocm-smi: one bad card discards all cards; no tests for `system_cmd` timeout or `ollama_tags`; Windows/macOS Ollama install hints not confirmed against ollama.com.
- After an Ollama pull the model stays `auto`; no test for the "Stopped." path; setup.md transcripts are hand-abridged; `model set-backend` docstring still says the model "resets to auto" (only true when the backend changes).
- Claude child env strips `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` (ruling: both move the child off the subscription); `ANTHROPIC_BASE_URL` is left alone.

## Still open from Phase 4
Remaining slices: a local-model judge for overlap callouts plus `links` rows, the feasibility tag, weekly synthesis. Follow-up: check `SHELF_REASON_FLOOR` against real bge-small scores.

## Earlier state (before Phase 5; kept for the rulings below)

Read this first in a new session. The master plan (Claude Docs, "Research Pipeline — Master Implementation Plan") is the source for what comes next.

## Done
- **Phase 4 slice 1, the shelf**, is done on branch `phase-4-shelf`. **NOT merged; the owner merges.** Plan: `plans/2026-11-22-shelf.md`, design `specs/2026-11-22-shelf-design.md`. `rpipe shelf add|list|remove|sync` index your notes in `my_work/` (local embeddings only), and triage boosts papers near them (`shelf.py`, `rank.py`; docs `shelf.md`, `triage.md`). The `TODO(phase4)` `my_work` stub is gone. **To start using it, run `rpipe shelf add "<a note>"` or `rpipe shelf add <file.md>`.**
  - Follow-up: `SHELF_REASON_FLOOR` was set with the fake embedder; check it against real bge-small scores and adjust.
  - Next slice: a local-model judge for overlap callouts plus rows in the `links` table, then a feasibility tag, then weekly synthesis.
  - Plan-time idea: future plans should mark which tasks can run in parallel and which files each task owns.
- **Phase 3, the daily habit**, is complete and on branch `phase-3-daily-habit`. It is **NOT merged to main; the owner merges.** Plan: `plans/2026-11-21-phase3-daily-habit.md`.
  - Votes and saves (`rpipe vote|save|unsave|feedback`), stored in `votes` and `saves`; schema versioning with `user_version` and a migration ladder (`db.py`).
  - Implicit profile in triage: votes and saves nudge scores, with a half-life decay (`rpipe profile set-feedback-halflife`).
  - Run history and crash-safe runs (`rpipe runs`); digest provenance line; summaries record which model and context made them.
  - `rpipe schedule install|status|remove|show` (systemd user timer).
  - `rpipe serve` (Datasette over Tailscale; vote and save forms where you type the paper id).
  - `rpipe profile suggest` and a guided `rpipe init` that propose topics and seeds from a description.
- Earlier, on `main`: Phase 2 (full text, cited summaries, gold set, eval) and auto model and context (`rpipe model fit|show|check|set|set-url|set-context`).
- Parked-list cleanup (the parked-fixes segment of this branch) is done: `uv run pytest -q` gives 623 passing.
- Earlier Phase 3 count: 563 passing. Docs: `docs/guide/` (new `scheduling.md` and `browsing.md`; `triage`, `data-model`, `digest`, `profile`, `running`, `glossary` updated).

- **`rpipe review`** (branch `review-loop`, not merged): terminal read-and-vote loop over unvoted digest papers, plus `rpipe review --later` for skipped ones (`skips` table, schema version 2, `review.py`). Docs: `docs/guide/review.md`. Next UI item: a reader page for `rpipe serve` (read the summary and vote in the browser, the phone equivalent of this loop).

## Owner actions not yet done
- Merge `phase-3-daily-habit` into `main` when happy.
- Reinstall the tool WITH the extras: `cd` into the repo and run `uv tool install --editable '.[embed,serve]' --force` (the plain `rpipe` on PATH was installed without `embed`, so the nightly run would fail). Then `rpipe schedule install` (it refuses if embed is missing), then `loginctl enable-linger $USER` (so the timer runs with nobody logged in). Check with `rpipe schedule status`.
- `rpipe serve` (the metadata shape was verified against a real Datasette 0.65 in a smoke run; see `.superpowers/sdd/2026-11-21-phase3-daily-habit/final-fix-report.md`). In the repo use `uv sync --extra embed --extra serve`; plain `uv sync` removes extras.
- Start voting, so the implicit profile has something to learn from.
- `rpipe profile suggest "<describe your research>"` to get topic and seed proposals. Seed papers are still empty.
- From the previous handoff, if not done: `rpipe model fit` (a few minutes), then `rpipe model show`. "Best model" = most parameters that fits, so `ds2-16b-20k` may beat `q3-14b-22k`; pin with `rpipe model set q3-14b-22k` if wanted. The gold set is still empty (optional: `rpipe gold add`, then `rpipe eval run|grade|report`).
- The user's `o` script (`~/.local/bin/o`) is NOT a dependency. Never call it from the program.

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
- Run lock covers only `rpipe run`; `summarize`, `collect`, `triage`, `eval run`, `model fit` can still overlap a nightly run (parked, see below).
- `save_summary` provenance stays optional; the digest footer tolerates missing fields.
- Locked run exits 75; unit file unchanged.
- Probing deadlines: fit/run/check 900 s, `model show` 10 s, fixed constants.
- Pinned num_ctx is cached as given; the 8192 floor applies to auto-chosen values only.

From before:
- A pinned model needs to fit at 8192 only; an auto-chosen one at 16384 or more. `llm_num_ctx` floor is 8192.
- Summary retries: same-day recovery, cap 3 (`MAX_SUMMARY_TRIES`); a failure left to a later day is not retried.
- Hidden-text stripping has documented gaps (`fulltext.md`).

## Parked (not fixed, no owner decision needed yet)
Fixed, or closed by a ruling (see Rulings), apart from these new ones:
- Only `rpipe run` takes the run lock; other writers (`summarize`, `collect`, `triage`, `eval run`, `model fit`) can overlap a nightly run.
- `ollama_fit._request`/`fits`: after an HTTP error, `session.check()` decides deadline-vs-does-not-fit by clock comparison; timer precision could misclassify (theoretical).

Dropped as resolved in Phase 3: no way to add a gold fact (`rpipe gold add-fact` exists).

## Next: rest of Phase 4
See the master plan in Claude Docs and the slice order under Done. The `links` table (`data-model.md`) is still unused. RSS collection is also marked as a later phase.

## Process notes
- Plans are executed subagent-driven (implementer, per-task review, fix rounds). Phase 3 used 1 to 2 fix rounds on tasks 3, 5, 6, 7, 8. The T6 implementer in Phase 2 wrote code before tests; disclosed.
- Project rules are in `CLAUDE.md`: docs in the same commit as code, and every user setting needs an `rpipe` command.

## Watching: truncation
The health line shows `truncated N` when papers were too long for the context window. If it is often above zero, revisit chunking (map-reduce summaries: split sections into chunks, summarize each, then combine the claims; costs more calls and weakens cross-section critique). Cheaper alternative first: pin the 143K-context model (`q3f-14b-143k`).
