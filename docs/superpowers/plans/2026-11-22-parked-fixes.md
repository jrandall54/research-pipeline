# Parked-list fixes (before Phase 4)

**Goal:** clear every item on the "Parked" list in `docs/superpowers/handoff.md` (Phase 3 and Phase 2/auto-model sections) before Phase 4 starts.

**Spec:** `docs/superpowers/handoff.md` (Parked section) and `CLAUDE.md`. No separate design spec.

## Global Constraints

- Branch `phase-3-daily-habit`, work in place, one commit per task. Do not merge or push.
- Tests run offline: `uv run pytest -q` (fixtures + fake embedder). The suite must stay green after every task (baseline 563 passing).
- Write the failing test first, then the fix (TDD).
- Docs are part of every change: update the relevant page in `docs/guide/` in the same commit, in the page shape What it does / How it works / Why it's built this way / How to run / Where the code is. Fix any doc describing old behaviour. Add new terms to `docs/guide/glossary.md`.
- Any new user-facing setting needs an `rpipe` command that sets it (no hand-editing of `profile.yaml`/`config.yaml`).
- Use `rpipe` (not `research-pipeline`) in docs and messages. Nothing personal goes in the repo. Never call the user's `o` script from the program.
- Remove the fixed item from the Parked list in `docs/superpowers/handoff.md` in the same commit (the controller does the final handoff rewrite; just delete the line you fixed).
- Tasks run sequentially because several touch the same files.

### Task 1: Database hardening (`db.py`)

Parked items:
- No foreign-key enforcement, so callers must validate paper ids. Decide the minimal safe fix: validate that a paper id exists in `vote`/`save`-style writers (raise a clear error) and/or enable `PRAGMA foreign_keys=ON` if it does not break existing tests or the migration ladder. Record the choice in `docs/guide/data-model.md` (Why section).
- No guard when the database's `user_version` is greater than `SCHEMA_VERSION` (a newer vault opened by older code). Refuse with a clear message telling the user to upgrade `rpipe`; never downgrade or modify the file.
- Layering inversion: `db.py` imports `summarize`. Remove it by moving whatever `db.py` needs (constants/types) to a lower-level module that both import, without behaviour change.
- Test gap: add a test for the disliked-side old-vote floor in the implicit-profile triage nudge (see `triage.py` and `docs/guide/triage.md`).

Docs: `data-model.md`, `triage.md` if wording changes.

### Task 2: Run robustness (`cli.py` run command, `db.py` finish_run, new run lock)

Parked items:
- `finish_run` itself could raise and mask the original error. Make the run command's error path preserve the original exception (log the secondary failure, re-raise the original).
- No run lock: `Persistent=true` catch-up can overlap a manual `rpipe run`. Add a non-blocking lock (e.g. `fcntl.flock` on a lock file in the vault state dir). A second concurrent run exits promptly with a clear message and a nonzero code that the timer treats sensibly; the lock must release on crash/SIGTERM (flock does, by process death). Test it offline with two lock acquisitions in one test.
- No stop after the first LLM timeout in a run: after the first LLM timeout, stop calling the LLM for the rest of that run (record it in `runs.errors` as `<stage>: <message>`, keep collecting/triaging/digest working with abstracts only). Document in `running.md`.

Docs: `running.md`, `scheduling.md` (overlap behaviour), `llm.md` if the timeout behaviour is described there.

### Task 3: Digest and schedule edge cases (`digest.py`, `schedule.py`, `cli.py` picker)

Parked items:
- `digest.py`: `_provenance_line` hard-indexes provenance keys (KeyError on a partial dict). Use tolerant `.get` with sensible omissions; `save_summary` provenance is optional, so a summary with none must render without a provenance line, not crash.
- `schedule.py`: `status` hint is misleading if the user manager is unreachable. Distinguish "unit not installed" from "cannot reach systemd user manager" and print an accurate hint for each.
- `cli.py`: `_resolve_paper` picker duplicates `_choose` (about 8 lines). Factor the shared picker so there is one implementation, no behaviour change.

Docs: `digest.md`, `scheduling.md`.

### Task 4: Input cleaning (`profile_edit.py`, `onboarding.py`)

Parked items:
- C1 and bidi control characters are not stripped from topics (`profile_edit`/onboarding). Strip C1 controls (U+0080-U+009F) and bidi controls (U+202A-U+202E, U+2066-U+2069, U+200E/U+200F, U+061C) in the shared topic-cleaning path; keep ordinary text and non-Latin scripts intact.
- `seed_queries` are not cleaned (never shown raw). Clean them with the same helper anyway, so stored data is consistent.

Docs: `profile.md`.

### Task 5: Model probing and fitting (`ollama_fit.py`, `cli.py` model commands)

Parked items:
- Cached `num_ctx` is not re-checked against bounds: re-validate on load (floor 8192, ceiling the model's trained context) and re-fit or fall back with a message if out of bounds.
- A failed unload after a probe is swallowed silently: surface a warning (once per probe session) instead of `pass`.
- No overall time limit on probing (a hung Ollama could stall about 30 minutes; `rpipe model show` can take about 30 s if Ollama hangs): add an overall deadline for a probing session (default 5 minutes for fit, 10 seconds for `model show`), with a clear message when hit. If a user-visible setting is needed, add an `rpipe model` command for it; otherwise keep constants and document them.
- The 1024-token margin is also applied when a model fits at its full trained context: skip the margin in that case (the margin protects against overflow beyond trained context, which cannot be used anyway). Update the unit tests and `llm.md`.

Docs: `llm.md`.

### Task 6: Gold set editing (`gold.py`, `cli.py`)

Parked item: no command to edit or remove a single gold fact (`rpipe gold add-fact` exists). Add `rpipe gold edit-fact` and `rpipe gold remove-fact` (guided prompt when arguments are omitted, validated input, confirmation before removal unless `--yes`). Test offline.

Docs: `evaluation.md`, plus `glossary.md` if needed.

### Task 7: Test hygiene (tests only)

Parked items:
- Weak assertions on some messages and exit codes: tighten assertions that only check `exit_code == 0` or a vague substring where the behaviour is specified in docs.
- Weak negative digest test: make it assert the absence of the specific thing it claims to exclude.
- Duplicate `AlwaysSummary` and test-to-test imports: move shared fakes into `tests/conftest.py` (or a `tests/fakes.py`) and import from there.

No production code changes except where a tightened test exposes a real bug (fix it and say so in the report). No doc changes unless behaviour changes.

### Task 3 addendum (carried from Task 1 review, Minor)

- `vault.py:67` (`rpipe init` path) calls `open_db` without catching `SchemaTooNewError`; convert it to the same clean error `cli._open` gives.
- Add CLI-level tests: `rpipe vote`/`rpipe save` on an unknown paper id exits 1 with the message; `_open` exits cleanly on a too-new database.

### Task 7 addendum (carried from Task 3 review, Minor)

- `tests/test_cli_feedback.py::test_init_exits_cleanly_on_too_new_database`: replace the weak final assertion (`res.exception is None or SystemExit`) with a precise one.
- `schedule.py` `status`: handle a missing `systemctl` binary the way `install` does (clean message, no traceback); add a test.
- `docs/guide/scheduling.md` usage section (~line 68): mention that `rpipe schedule status` exits 1 with a clear message when the systemd user manager is unreachable (a change from the old exit 0 "not installed").

### Task 7 addendum 2 (carried from Task 4 review, Minor)

- `onboarding.py` ~line 28: two blank lines before `class ProposedProfile` (PEP8/E302).
- `tests/test_onboarding.py`: one-line comment on the seed-query test explaining ESC becomes a space while `[31m` stays.
- `docs/guide/profile.md` normalisation table: state that ZWJ/ZWNJ (U+200D/U+200C) are deliberately kept (needed for Persian, Indic and emoji text), and describe the bidi example in words rather than a literal invisible character. Add a test that ZWJ/ZWNJ survive.
- Add tests that `add_author` and `add_keyword` strip these characters (the docs claim they do).
