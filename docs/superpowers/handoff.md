# Handoff — after the full project audit (2026-11-22)

Read this first in a new session. The master plan (Claude Docs, "Research Pipeline — Master Implementation Plan") is the source for what comes next; this note holds what the plan is too coarse for.

## State
- **`main` holds Phases 1–5, the review loop, the shelf, summarize-backlog, and the pipeline/app split design and plan.** All feature branches are merged and deleted except `pipeline-app-split` (same as `main` for now). `main` is pushed; CI runs on GitHub (first Phase 5 run 2026-11-22). `uv run pytest -q`: 897 passing.
- **A full audit ran 2026-11-22** (roadmap vs code, loose ends, docs, Phase 7 readiness). Its fixes: this note rewritten, stale guide pages and glossary fixed, the master plan's roadmap given a home for every open item, and factual errors in the Phase 7 plan corrected.
- **Direction (owner, 2026-11-22):** a **pipeline** (gets material, unattended) plus an **app** (read, vote, notes, study, later research and thesis) over **one database**, split by function in code. Design `specs/2026-11-22-backend-frontend-split-design.md`, plan `plans/2026-11-22-pipeline-app-split.md` (not started). `library_api` returns data objects, not terminal text; the app's look is undecided.
- **Owner context:** philosophy BA finishing CS prerequisites, applying for a master's starting fall 2027. "Research" today means articles, repos and project ideas.

## Phase status (checked against the code 2026-11-22)
| Phase | Status |
|---|---|
| 1, 1.5 | Done. Follow-ups moved to Phase 6 (missing setting commands, category check). |
| 2 | Code built (`fulltext`, `llm`, `prompts`, `summarize`, `gold`, `evaluate`). Exit not met: the gold set is empty and no `rpipe eval` has run. |
| 3 | Code complete (votes/saves, Datasette, timer, run log, versioning, onboarding). "A week unattended" not yet observed. |
| 4 | Shelf built. Overlap alerts, archive connections, feasibility tag, weekly synthesis not built; `links` table unused. |
| 5 | Built and merged; Claude Code smoke test passed (canary did not leak; canary line since removed). AI club pilot not started. |
| 6 | Next. Brainstorm first. |
| 7 | Planned; plan corrected after the audit but must be refreshed after Phase 6 (see the note at its top). |

## Next
1. **Phase 6 (pipeline hardening): brainstorm and plan it.** The master plan's Phase 6 list is the scope. Inputs below: progress audit, long papers, open items.
2. Then refresh and execute `plans/2026-11-22-pipeline-app-split.md` (subagent-driven).

## Phase 6 inputs
### Progress feedback (owner requirement: no step may look frozen)
Today only `rpipe summarize` prints `[i/N] id done (Ns)` after each paper, plus `model fit` log lines and `model pull` percent. Silent today:
- Embedder load/download (`cli.py` `_embedder`/`_load_embedder`, `embed.py`) in run, triage, shelf add.
- `run_collect` (`collect.py`) over arXiv pages, 3 s apart, up to `arxiv_max_results`; `rpipe collect` prints only final counts.
- Triage embedding of hundreds of papers; `shelf.sync_shelf`; seed fetches for triage.
- Inside summarize, per paper: `fetch_fulltext`, then each `generate` call (Ollama or Claude Code, up to 600 s each) — no per-stage lines.
- `rpipe eval run` (prints only "this can take a while"), `profile suggest` / `onboarding.propose` and its arXiv lookups, `profile add-seed` and the `init` loop, `model check` probes.
Design sketch: one shared module; spinner/bar with elapsed time on a TTY, one plain line per event when stderr is not a TTY (systemd journal); progress on stderr. **Progress events should be data objects, not strings** (the app will consume them; split design SPEC "progress"), and the module is classified *shared* for Phase 7. Tests: helper unit-tested offline; each stage reports through its callback. Docs: a progress section in `running.md`; add the convention to CLAUDE.md (ask the owner first).

### Long papers (owner, 2026-11-22)
Real Ollama summary of 2611.25005 was fast and "decent" but **truncated**. Order to try: (1) a longer context window (`rpipe model fit` / `rpipe model set-context`; a long-context local model), (2) chunked summarize-and-critique (map-reduce by section, merge overlapping claims, keep section citations; measure with `rpipe eval`). Watch the health line's `truncated N` over several runs before building (2).

### Verify the Ollama path for real
Everything run for real used **claude-code**. Test: `rpipe model set-backend ollama`, `rpipe model check`, `rpipe summarize 2611.25005`; judge prompts (`PROMPT_VERSION` 2026-11-p4), time per paper, quality. The nightly timer uses the configured backend (claude-code spends plan quota).

### Open items the audit gave a phase (see master plan)
- **CLAUDE.md rule gap:** `arxiv_max_results`, `embedding_model`, `rss_feeds` have no `rpipe` command (`models.py`). `rss_feeds` is dead config (no RSS source): add the command with the source, or remove the key.
- **Category check is format-only:** `cs.ML` accepted (`models.py` `CATEGORY_RE`). Validate against the real arXiv list.
- **`require_local` is not wired** (`llm.py`). Wire it before any feature sends shelf (`my_work/`) text to a model.
- `rpipe show <id>...` and `rpipe show --recent` (owner asked: print summary and critique, no voting).
- Owner setup: reinstall with extras (`uv tool install --editable '.[embed,serve]' --force`), `rpipe schedule status`, `loginctl enable-linger $USER`, add seed papers (`rpipe profile suggest "..."` or `rpipe profile add-seed`), start voting, fill the gold set (`rpipe gold add`, `rpipe eval run|grade|report`), run `rpipe summarize --backlog` for the 12 unsummarized 2026-11-21 papers then `rpipe digest --date 2026-11-21`.
- **Repo is private**, but the README's install line is a GitHub URL: make it public (or give club members access) before the AI club pilot.
- Check `SHELF_REASON_FLOOR` against real bge-small scores.
- Deferred minors: no config-to-digest e2e test for plug-in sources; `ollama_fit._request`/`fits` decides deadline vs does-not-fit by clock comparison (theoretical). (Fixed on branch `known-issues`: duplicate `make_arxiv`, duplicate entry-point names, rocm-smi per-card parsing, missing timeout/`ollama_tags`/"Stopped." tests, set-backend wording; the model staying `auto` after a pull is intended and now documented; ruff is installed, configured and in CI.)

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

## Process notes
- Plans are executed subagent-driven (implementer, per-task review, fix rounds). Plan checkboxes were never ticked in executed plans; git history is the record.
- Future plans should mark which tasks can run in parallel and which files each task owns.
- Project rules are in `CLAUDE.md`: docs in the same commit as code, and every user setting needs an `rpipe` command.
- The owner's personal Ollama helper script is not a dependency; never call it from the program, and keep the owner's model names out of the repo.
