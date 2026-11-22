# Phase 3 — Daily Habit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the pipeline a daily habit: vote/save feedback that shapes ranking, an unattended nightly timer, a browsable vault, provenance on summaries, and LLM-guided onboarding.

**Architecture:** New tables (`saves`) and JSON keys avoid risky ALTERs; a small `PRAGMA user_version` migration hook in `open_db` handles anything else. Feedback is a time-decayed implicit profile computed in `run_triage` from stored embeddings and passed into `score_papers`. Scheduling and Datasette are thin command wrappers (`rpipe schedule`, `rpipe serve`) around pure, tested generators; external programs (`systemctl`, `datasette`, `tailscale`) are injected so tests stay offline.

**Tech Stack:** Python, Typer, SQLite (WAL, FTS5), pydantic, Ollama client (`llm.py`), systemd user units, optional `datasette` extra.

**Spec:** Claude Docs "Research Pipeline — Master Implementation Plan" (https://claude.ai/artifact/Ajw2s52HwWuZfrYZXsZjfa), section "Phase 3 — Daily habit", plus Stage 6 (Feedback), Scheduling, Reliability, "Your own research" (implicit layer). Handoff: `docs/superpowers/handoff.md`.

## Global Constraints

- CLI is `rpipe` (alias `research-pipeline`); use `rpipe` in docs, messages, examples.
- Every change updates the relevant `docs/guide/` page **in the same commit** (shape: What it does → How it works → Why it's built this way → How to run / check it → Where the code is). New ML/IR terms go in `docs/guide/glossary.md`. Fix docs describing old behaviour.
- Every new user setting gets an `rpipe` command that validates input; no workflow may require hand-editing `profile.yaml`/`config.yaml`.
- Nothing personal in the repo. Tests run offline (fixtures, `FakeEmbedder`, `FakeLLM`): `uv run pytest -q` (407 passing at start; must stay green).
- Timer: daily 03:00 with `Persistent=true`. Datasette binds to the Tailscale interface only, never 0.0.0.0.
- The user's `o` script is never called from the program. Pinned model needs to fit at 8192; `llm_num_ctx` floor 8192.
- Votes: latest vote per paper wins; +1 up, -1 down, 0 cleared. Saves are a separate toggle.
- Commit per task; end commit messages with the attribution lines from the session.

## Review Focus

- Vote on an id that is not in the vault, or an ambiguous input → clear message, no row written (Task 2).
- Votes on papers with no stored embedding (e.g. embedder changed) → triage must not crash; they are ignored (Task 3).
- Vault created before Phase 3 (no `saves` table, no `user_version`) → opens and upgrades cleanly (Task 1).
- Old summary rows without provenance keys still load and render (Task 4).
- A crash mid-run leaves `finished_at` NULL today → run must record the error and finish (Task 5).
- `systemctl`/`tailscale`/`datasette` missing or failing → clear message, nonzero exit, nothing half-written (Tasks 6, 7).
- Model proposes seed titles that do not exist on arXiv, or Ollama is down during `rpipe init` → unverified titles dropped; flow skips gracefully (Task 8).

---

### Task 1: Migration hook, `saves` table, feedback data layer

**Files:**
- Modify: `src/research_pipeline/db.py` (SCHEMA at 13-88, `open_db` 94-101)
- Test: `tests/test_db.py` (update `test_open_db_creates_all_tables`), new `tests/test_feedback_db.py`
- Docs: `docs/guide/data-model.md` (fix the "no migration" claim at line 116; document `votes`, `saves`, `user_version`)

**Interfaces:**
- Produces (all in `db.py`):
  - `SCHEMA_VERSION = 1`; `open_db` sets/reads `PRAGMA user_version` and runs `_migrate(conn, from_version)` (a no-op-able ladder: v0→v1 only stamps the version; `CREATE TABLE IF NOT EXISTS` already adds `saves`).
  - `record_vote(conn, paper_id: str, vote: int, now: str | None = None) -> None` — vote in {-1, 0, 1} else `ValueError`; inserts a `votes` row.
  - `current_votes(conn) -> list[sqlite3.Row]` — latest vote per paper with nonzero vote; columns `paper_id, vote, created_at`.
  - `set_saved(conn, paper_id: str, saved: bool, now: str | None = None) -> None`; `is_saved(conn, paper_id) -> bool`; `list_saved(conn) -> list[sqlite3.Row]`.
  - `saves` table: `paper_id TEXT PRIMARY KEY REFERENCES papers(id), created_at TEXT NOT NULL`.

- [ ] **Step 1: Write failing tests** in `tests/test_feedback_db.py`: `test_latest_vote_wins` (up then down → `current_votes` has one row, vote -1); `test_cleared_vote_excluded` (up then 0 → empty); `test_invalid_vote_rejected` (`ValueError` for 2); `test_save_toggle` (`set_saved` True/False, `list_saved`); `test_old_vault_upgrades` (create a db with the pre-Phase-3 schema and `user_version` 0, call `open_db`, assert `saves` exists and `PRAGMA user_version` == 1). Update `test_open_db_creates_all_tables` to include `saves`.
- [ ] **Step 2: Run** `uv run pytest tests/test_feedback_db.py tests/test_db.py -q` → FAIL.
- [ ] **Step 3: Implement** the functions above in `db.py`. Use the same timestamp helper `db.py` already uses for `created_at`.
- [ ] **Step 4: Run** the same command → PASS; then `uv run pytest -q` → all green.
- [ ] **Step 5: Update `data-model.md`**, then commit: `feat: saves table, vote helpers and schema version hook`.

### Task 2: `rpipe vote`, `rpipe save`, digest ids

**Files:**
- Modify: `src/research_pipeline/cli.py`, `src/research_pipeline/digest.py` (`_render` 56-77)
- Test: new `tests/test_cli_feedback.py`, extend `tests/test_digest.py`
- Docs: `docs/guide/digest.md`, `docs/guide/running.md` (commands table), `docs/guide/glossary.md`

**Interfaces:**
- Consumes: Task 1 helpers; `cli._open`, `cli._fail`, `cli._choose` patterns.
- Produces CLI: `rpipe vote ID up|down|clear`, `rpipe save ID`, `rpipe unsave ID`, `rpipe feedback` (lists current votes and saved papers with titles). `ID` is an arXiv id (as stored in `papers.id`); a title fragment is also accepted and resolved against the vault's `papers` table (ambiguity → `_choose`; no match → `_fail` and nothing written). Each command takes `--vault`.
- Digest: each item shows its id and a one-line hint ``rpipe vote <id> up|down · rpipe save <id>``, once in the digest footer using the first item's id as an example rather than on every item; each item header shows ``id: <id>`` (and `★ saved` / `👍` / `👎` marker if present).

- [ ] **Step 1: Failing tests** (CliRunner): `test_vote_up_records_row`, `test_vote_unknown_id_fails_and_writes_nothing`, `test_vote_by_title_fragment_unique`, `test_vote_ambiguous_prompts` (scripted input `1`), `test_save_and_unsave`, `test_feedback_lists`; digest test: rendered item contains `id: <id>` and the saved marker for a saved paper.
- [ ] **Step 2: Run** `uv run pytest tests/test_cli_feedback.py tests/test_digest.py -q` → FAIL.
- [ ] **Step 3: Implement** commands in `cli.py` (a `feedback`-themed section) and the digest changes.
- [ ] **Step 4: Run** → PASS; full suite green.
- [ ] **Step 5: Docs, commit:** `feat: rpipe vote/save/feedback and ids in the digest`.

### Task 3: Implicit profile in triage

**Files:**
- Modify: `src/research_pipeline/rank.py` (VOTE_PENALTY stub at line 30, stub use at 107, `ProfileVectors` 22-27), `src/research_pipeline/triage.py` (35-36), `src/research_pipeline/models.py` (`Profile`, line 37), `src/research_pipeline/profile_edit.py`, `src/research_pipeline/cli.py` (profile sub-app)
- Test: `tests/test_rank.py`, `tests/test_triage.py`, `tests/test_cli_profile.py`
- Docs: `docs/guide/triage.md` (formula lines 56-60, weights 72, reasons 78), `docs/guide/profile.md`, `docs/guide/glossary.md` (implicit profile, decay, half-life)

**Interfaces:**
- Consumes: `db.current_votes`, `db.list_saved`, embeddings rows (item_id = paper id).
- Produces:
  - `rank.FeedbackVectors` dataclass: `liked: list[tuple[str, np.ndarray, float]]`, `disliked: list[tuple[str, np.ndarray, float]]` (paper id, vector, weight).
  - `rank.build_feedback_vectors(votes, saves, embeddings_by_id, halflife_days: float, now) -> FeedbackVectors`. Weight = `0.5 ** (age_days / halflife_days)`; an up vote has base 1.0, a save base 1.0 (an up-voted and saved paper counts once with max), a down vote goes to `disliked`. Papers without an embedding are skipped.
  - `score_papers(..., feedback: FeedbackVectors | None = None)` adds `FEEDBACK_BOOST * weighted_mean_sim_to_liked − FEEDBACK_PENALTY * weighted_mean_sim_to_disliked` (replaces the `VOTE_PENALTY` stub; constants sized against the ~0.045 similarity std noted at `rank.py:15-24`). Reason strings: `"liked similar: <id>"` / `"disliked similar: <id>"` (the single most influential paper), appended in the existing reasons order.
  - `Profile.feedback_halflife_days: float = 60.0` (validated 1–3650) with `rpipe profile set-feedback-halflife DAYS` (`profile_edit.set_feedback_halflife`).

- [ ] **Step 1: Failing tests:** `test_liked_neighbour_scores_higher` (two otherwise equal papers, the one near a liked vector wins, reason contains `liked similar`); `test_disliked_neighbour_scores_lower`; `test_old_vote_decays` (same vote at age = 1 half-life has half the effect of a fresh one, with exact ratio asserted within 1e-9); `test_vote_without_embedding_ignored` (no crash); `test_no_feedback_scores_unchanged` (regression: scores identical to before when `feedback=None`); `test_set_feedback_halflife_validates` (0 and "abc" rejected; 30 saved).
- [ ] **Step 2: Run** `uv run pytest tests/test_rank.py tests/test_triage.py tests/test_cli_profile.py -q` → FAIL.
- [ ] **Step 3: Implement** per the interfaces. `run_triage` loads votes/saves/embeddings and passes `feedback`.
- [ ] **Step 4: Run** → PASS; full suite green.
- [ ] **Step 5: Docs, commit:** `feat: votes and saves feed an implicit profile with decay`.

### Task 4: Provenance on summaries

**Files:**
- Modify: `src/research_pipeline/prompts.py`, `src/research_pipeline/summarize.py` (`PaperSummary` 49-54, save at 170), `src/research_pipeline/db.py` (`save_summary` 246-267, `get_summary` 270), `src/research_pipeline/digest.py` (`_render_summary` 35-54)
- Test: `tests/test_summarize.py`, `tests/test_db.py`, `tests/test_digest.py`
- Docs: `docs/guide/summaries.md`, `docs/guide/digest.md`, `docs/guide/data-model.md`

**Interfaces:**
- Produces: `prompts.PROMPT_VERSION = "2026-11-p3"` (bump rule documented: change it whenever `SUMMARY_SYSTEM`/`CRITIQUE_SYSTEM` change). Provenance dict stored as an extra JSON key `provenance` in the summary row: `{"model", "prompt_version", "paper_version", "num_ctx", "created_at"}`. `get_summary` pops `provenance` before building `PaperSummary` and exposes it via a new `db.get_provenance(conn, paper_id) -> dict | None`. Rows without the key return `None`. Digest prints a small italic footer per item: `model · prompt <version> · paper v<N> · <date>`; omitted when provenance is `None`.

- [ ] **Step 1: Failing tests:** `test_provenance_saved_and_read`; `test_old_summary_without_provenance_loads` (insert a row in the old JSON shape; `get_summary` works, `get_provenance` is None); `test_digest_footer_shows_provenance`; `test_digest_without_provenance_has_no_footer`.
- [ ] **Step 2: Run** `uv run pytest tests/test_summarize.py tests/test_db.py tests/test_digest.py -q` → FAIL.
- [ ] **Step 3: Implement.** `summarize` passes `llm.model_name`, `llm.num_ctx`, `paper.version`, `PROMPT_VERSION` into `save_summary`.
- [ ] **Step 4: Run** → PASS; full suite green.
- [ ] **Step 5: Docs, commit:** `feat: record provenance on every summary`.

### Task 5: Run log hardening and `rpipe runs`

**Files:**
- Modify: `src/research_pipeline/cli.py` (`run` 231-251, `health_line` 192-206), `src/research_pipeline/db.py` (`start_run`/`finish_run` 211-224; add `list_runs(conn, limit) -> list[sqlite3.Row]`)
- Test: `tests/test_e2e.py`, new `tests/test_runs.py`
- Docs: `docs/guide/running.md`, `docs/guide/digest.md`

**Interfaces:**
- `cli.run` wraps the stages in `try/finally`: an unexpected exception is recorded in `errors` (`{"stage": ..., "error": str}` in the existing errors shape), `finish_run` always executes, the digest is still written when possible, and the exit code is nonzero after recording.
- `rpipe runs [--limit 10]` prints started, duration, counts summary, error count per run (newest first).
- `health_line` gains the previous run's status only when it failed or never finished: `previous run did not finish` (checked via `finished_at IS NULL` on the run before the current one).

- [ ] **Step 1: Failing tests:** `test_crash_in_collect_still_finishes_run` (monkeypatch a source to raise; `runs.finished_at` set, errors contains the message, exit code 1); `test_runs_command_lists_newest_first`; `test_health_line_flags_unfinished_previous_run`.
- [ ] **Step 2: Run** `uv run pytest tests/test_runs.py tests/test_e2e.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS; full suite green.
- [ ] **Step 5: Docs, commit:** `feat: crash-safe run log and rpipe runs`.

### Task 6: `rpipe schedule` (systemd user timer)

**Files:**
- Create: `src/research_pipeline/schedule.py`
- Modify: `src/research_pipeline/cli.py` (new `schedule` sub-app)
- Test: new `tests/test_schedule.py`
- Docs: new `docs/guide/scheduling.md` (add to `docs/guide/README.md` index), update `running.md` line 178, `glossary.md` (systemd, timer, linger)

**Interfaces:**
- Produces in `schedule.py`:
  - `render_service(vault: Path, rpipe_cmd: str) -> str` — `[Service] Type=oneshot`, `Environment=RESEARCH_VAULT=<vault>`, `ExecStart=<rpipe_cmd> run`.
  - `render_timer(time: str = "03:00") -> str` — `OnCalendar=*-*-* HH:MM:00`, `Persistent=true`, `WantedBy=timers.target`. `time` validated `HH:MM` (24h) else `ValueError`.
  - `install(vault, time, unit_dir: Path, runner: Callable[[list[str]], int], rpipe_cmd: str) -> list[Path]` writes `research-pipeline.service` and `.timer` to `unit_dir` (default `~/.config/systemd/user`), then `runner(["systemctl","--user","daemon-reload"])` and `runner(["systemctl","--user","enable","--now","research-pipeline.timer"])`; nonzero runner result → `ScheduleError` and the written files are left but reported.
  - `remove(unit_dir, runner)`, `status(runner)`.
- CLI: `rpipe schedule install [--time HH:MM] [--vault PATH]`, `rpipe schedule status`, `rpipe schedule remove`, `rpipe schedule show` (prints unit text without writing, for review). `rpipe_cmd` resolved with `shutil.which("rpipe")`; missing → `_fail` with install hint. Docs explain `loginctl enable-linger $USER` so the timer runs while logged out (printed by `install`, never run automatically).

- [ ] **Step 1: Failing tests:** `test_timer_has_persistent_and_0300_default`; `test_bad_time_rejected` ("25:00", "3am"); `test_service_sets_vault_env`; `test_install_writes_files_and_calls_systemctl_in_order` (fake runner records calls); `test_install_failure_raises_schedule_error`; `test_cli_show_prints_without_writing` (tmp unit_dir stays empty).
- [ ] **Step 2: Run** `uv run pytest tests/test_schedule.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS; full suite green.
- [ ] **Step 5: Docs, commit:** `feat: rpipe schedule installs the nightly systemd timer`.

### Task 7: `rpipe serve` (Datasette over Tailscale)

**Files:**
- Create: `src/research_pipeline/serve.py`
- Modify: `pyproject.toml` (extra `serve = ["datasette"]`), `src/research_pipeline/cli.py`
- Test: new `tests/test_serve.py`
- Docs: new `docs/guide/browsing.md` (add to README index), `glossary.md` (Datasette, Tailscale, canned query)

**Interfaces:**
- Produces in `serve.py`:
  - `build_metadata() -> dict` — Datasette metadata with a `vault` database, canned **writable** queries `vote_up`, `vote_down`, `vote_clear` (insert into `votes`, params `paper_id`), `save`, `unsave`, and read queries `todays_digest`, `saved`. `write: true` only on the write queries.
  - `tailscale_ip(runner) -> str` — runs `tailscale ip -4`, returns the first line; failure → `ServeError("Tailscale not running; use --host 127.0.0.1 to serve locally")`.
  - `resolve_host(host: str | None, runner) -> str` — `None` → Tailscale IP; refuses `0.0.0.0` and `::` with `ServeError`.
  - `command(vault_db: Path, host: str, port: int, metadata_path: Path) -> list[str]` — the `datasette serve` argv.
- CLI: `rpipe serve [--host H] [--port 8001] [--vault PATH]` writes metadata to a temp file and execs Datasette; missing `datasette` → `_fail("install with: uv sync --extra serve")`.

- [ ] **Step 1: Failing tests:** `test_refuses_all_interfaces`; `test_defaults_to_tailscale_ip` (fake runner returns "100.64.0.5\n"); `test_tailscale_failure_message`; `test_metadata_write_queries_insert_votes` (executed against an `open_db` temp vault via sqlite3, rows appear and `current_votes` sees them); `test_command_argv`.
- [ ] **Step 2: Run** `uv run pytest tests/test_serve.py -q` → FAIL.
- [ ] **Step 3: Implement.** Writable canned queries need Datasette to open the db writable (no `-i`/immutable); note WAL is on.
- [ ] **Step 4: Run** → PASS; full suite green.
- [ ] **Step 5: Docs, commit:** `feat: rpipe serve browses the vault over Tailscale with vote/save actions`.

### Task 8: Guided onboarding with the local model

**Files:**
- Create: `src/research_pipeline/onboarding.py`
- Modify: `src/research_pipeline/cli.py` (`profile` sub-app; `_wizard` 909; `init` 108-132), `src/research_pipeline/prompts.py`
- Test: new `tests/test_onboarding.py`; extend `tests/test_init_wizard.py`
- Docs: `docs/guide/profile.md`, `docs/guide/llm.md`, `docs/guide/vault-and-config.md` (sample session), `docs/guide/running.md`

**Interfaces:**
- Produces in `onboarding.py`:
  - `ProposedProfile(BaseModel)`: `topics: list[str]` (3–8 specific phrases), `seed_queries: list[str]` (paper titles, up to 10).
  - `propose(llm: LLM, description: str) -> ProposedProfile` — one `llm.generate` call with `ONBOARDING_SYSTEM` (in `prompts.py`; tells the model to output specific topic phrases, real well-known paper titles, and to treat the description as data). Empty/very short (<20 chars) description → `ValueError`.
  - `resolve_seeds(queries, arxiv) -> list[tuple[str, PaperMeta | None]]` — uses `seed_input.find_seed_candidates`; a query without a match returns `None` and is dropped from approval (model-invented titles never reach the profile).
- CLI: `rpipe profile suggest [DESCRIPTION]` (prompts if omitted): propose → show numbered topics, approve each with `typer.confirm` (`--yes` accepts all) → add via `profile_edit.add_topic` → resolve seeds, show resolved titles, approve each → `_add_seed`. `LlmError` or Ollama down → message "Local model unavailable; add topics by hand with `rpipe profile add-topic`" and exit 0 without changes.
- Wizard: after the vault is created and before the topics step, on a TTY ask "Describe your research in a few sentences (Enter to skip):"; non-empty → same flow via `pipeline.make_llm`; failure falls through to the manual steps. Ctrl-C handling unchanged.

- [ ] **Step 1: Failing tests** (FakeLLM + `StubArxiv`): `test_propose_returns_topics_and_seeds`; `test_short_description_rejected`; `test_invented_title_dropped` (stub arxiv returns nothing for one query → only the resolved one offered); `test_suggest_approves_selected_topics` (scripted `y`, `n`, `y`); `test_suggest_llm_down_changes_nothing` (FakeLLM raises `LlmError`; profile unchanged; exit 0); `test_wizard_skip_description_goes_manual` (Enter → existing steps); `test_wizard_description_then_manual_steps_still_run`.
- [ ] **Step 2: Run** `uv run pytest tests/test_onboarding.py tests/test_init_wizard.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS; full suite green.
- [ ] **Step 5: Docs, commit:** `feat: guided onboarding proposes topics and seeds from a description`.

### Task 9: Handoff, README and index

**Files:**
- Modify: `README.md`, `docs/guide/README.md` (stages, index of new pages), `docs/superpowers/handoff.md` (rewrite for state after Phase 3: done, owner actions — run `rpipe schedule install`, `loginctl enable-linger`, `rpipe serve`, start voting; parked items carried over; new parked items from reviews), `docs/guide/glossary.md` (check all new terms present)
- Verify: `uv run pytest -q` green; every new `rpipe` command appears in `docs/guide/running.md` table or its own page.

- [ ] **Step 1:** Grep the guide for stale claims ("Phase 3", "later phase", `VOTE_PENALTY`, "no migration") and fix each.
- [ ] **Step 2:** Update handoff and README; run the full suite.
- [ ] **Step 3: Commit:** `docs: handoff and index after phase 3`.
