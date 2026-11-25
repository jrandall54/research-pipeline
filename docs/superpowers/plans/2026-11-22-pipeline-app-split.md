# Pipeline and App Split Implementation Plan (Phase 7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Organize the code into a pipeline group and an app group over one database, give the app one door (`library_api`) that returns data objects, let the app ask the pipeline for work through a request list, and put every pipeline command under one lock.

**Architecture:** Existing modules stay where they are. User-action functions move from `db.py` to a new `library.py`; a new `library_api.py` is the app's only door and returns Pydantic objects; a `requests` table plus `requests_queue.py` let the app queue summaries the pipeline runs later. `cli.py` (with `progress_render.py`) is the composition layer allowed to import both groups. Tests that read each module's imports enforce the boundary.

**Tech Stack:** Python 3.12, SQLite (WAL), Pydantic v2, Typer, pytest. `uv run pytest -q`.

**Spec:** `docs/superpowers/specs/2026-11-22-backend-frontend-split-design.md`

**Starts from:** `main` at `8fde6d4` (1404 tests passing, ruff clean). Work on a new branch `pipeline-app-split`.

**Refreshed 2026-11-25** against the code after Phase 6 (A, C, B.1-B.6). What changed since the 11-22 draft:
- Progress: stages take a keyword-only `reporter` (`progress.py`: `stage()`, `StageHandle.step/note`, `ListReporter` for tests); `on_progress` is gone. Terminal rendering lives in `progress_render.py`.
- `run_summarize(conn, llm, fetch, num_ctx, today=None, backlog_limit=None, paper_ids=None, *, reporter=None, refetch=False, mode="whole", fallback_above=None) -> (counts, errors)`. A timeout appends `summarize.TIMEOUT_STOPPED` to errors; a usage limit appends a fixed line (Task 5 turns it into a constant). This settles the 11-22 open issue about signalling: the drain reads the returned counts and errors.
- `rpipe run` now has a `rerun` stage (`spill.run_reruns`, Ollama only) after summarize, and picks the reading mode with `cli._summarize_plan`.
- 13 new modules (classified in Task 6); `llm` imports `bench` only under `TYPE_CHECKING`; `profile_edit` imports `schedule.parse_time` and `categories`.
- New commands: `model bench|tune|scan|recommend|set-mode|catalog *`, `gold suggest|auto|draft|...`, `schedule set-deadline`, `show`. Lock decisions for them are made in Task 1.
- Schema is still version 3; the `requests` table makes it 4.
- Line numbers are no longer quoted; find code by name.

**Parallelism:** none. Every task edits `cli.py`, and Tasks 3-6 build on each other's names. Run them in order.

## Global Constraints

- Docs in `docs/guide/` change in the same commit as the code they describe (CLAUDE.md). Every new term goes in `docs/guide/glossary.md`.
- Use `rpipe` in docs, messages and examples.
- Tests run offline: fixtures, `FakeEmbedder` and `FakeLLM` from `tests/conftest.py`. No network.
- Every step that waits reports through `progress.py` (CLAUDE.md); new stages emit events, the CLI renders them.
- One database file, `vault.sqlite`; no data moves. The only schema change is the `requests` table, `SCHEMA_VERSION` 3 → 4.
- Lock-held exit code stays `75` (`RUN_LOCKED_EXIT`).
- Request retry cap is `MAX_SUMMARY_TRIES` (3), imported, not re-typed.
- `library_api` functions never return a display string.
- Behaviour of collect, triage scoring, fetch, summarize, reading mode and reruns is unchanged.
- Never leave the owner's config changed after a test or check (owner rule).

## Review Focus

1. **A hand-run pipeline command while another holds the lock** (including `rpipe model tune`, which restarts Ollama): the second stops at once with exit 75, a message naming the holder (e.g. `rpipe run`), and changes nothing. Tested in Task 1.
2. **A search string with FTS syntax in it** (`"`, `*`, `AND`, `-`, a lone `(`): `search_papers` returns results or `[]`, never raises `sqlite3.OperationalError`. Tested in Task 4.
3. **A paper id with a version suffix, `arXiv:` prefix or URL** (`2611.25005v2`): normalised by one shared pure function (`ids.normalise_paper_id`), then looked up; an id not in the vault is refused with `UnknownPaperError`, never queued. `rpipe vote 2611.25005v2 up` fails today (`_resolve_paper` does an exact match, then a title search); Task 3 fixes it. Tested in Task 3.
4. **The nightly run dies while a request is `running`, or stops on a usage limit or timeout**: the next drain resets `running` to `pending`; a limit or timeout leaves the rest `pending` without burning a try, and the drain is skipped entirely when summarize already stopped on one. Tested in Task 5.
5. **Pending requests on a night with no new papers**: today `_run_locked` skips the model load when `papers_to_summarize` is empty (`has_work=False`), so requests would never run on quiet nights. `has_work` must also count pending requests. Tested in Task 5.

---

### Task 1: One lock for every pipeline command

**Files:**
- Modify: `src/research_pipeline/run_lock.py`
- Modify: `src/research_pipeline/cli.py` (`run` and the commands listed in Step 0; new helper `_pipeline_lock`)
- Modify: `src/research_pipeline/db.py` (`open_db`: explicit `timeout=5.0`)
- Test (update existing): `tests/test_run_lock.py` and `tests/test_runs.py` (they call `run_lock(vault)` with one argument and assert the old message "another `rpipe run` is already using"); add the new tests
- Docs: `docs/guide/running.md` (lock section; both places that quote the old message), `docs/guide/scheduling.md` (nightly run and hand-run commands share one lock), `docs/guide/hardware.md` (`model tune|bench|recommend` wait for or refuse a running job), `docs/guide/browsing.md` (the Datasette write path, Step 0), `docs/guide/glossary.md` (WAL, busy timeout, lock holder)

**Interfaces:**
- Produces: `run_lock(vault: Path, holder: str) -> ContextManager[None]`; `RunLocked.holder: str` (text read from the lock file, `"unknown"` if empty); `cli._pipeline_lock(ctx: _Context, holder: str) -> ContextManager[None]`, which turns `RunLocked` into `typer.Exit(RUN_LOCKED_EXIT)` after printing to stderr: `"<holder> is already working on <vault>; nothing was started. Wait for it to finish (see `rpipe runs`)."`

- [ ] **Step 0: Record the lock list in `running.md`** (a table: command, locked?, why). Decided here:
  - **Locked** (pipeline work, model/GPU, Ollama service, or fetching into the vault): `run`, `collect`, `triage`, `summarize` (all forms incl. `--backlog`, `--refetch`), `digest`, `eval run`, `model fit`, `model check`, `model bench`, `model tune` (restarts Ollama: would break a nightly run), `model recommend` (can pull a model and refit), `model setup`, `profile suggest` (uses the model), `gold add`, `gold suggest`, `gold auto`, `gold draft`, `shelf sync`, `shelf add` (load the embedder and rewrite shelf rows; `rpipe run` syncs the shelf before triage, so two syncs could collide), and `requests run` (Task 5).
  - **Unlocked**: app commands `vote`, `save`, `unsave`, `feedback`, `review`, `show`, `shelf list|remove`, `serve`, `runs`, `requests list`; settings and read-only commands `profile show|add-seed|set-*`, `model show|set*|scan`, `catalog show|update`, `gold list|remove|*-fact`, `eval grade|report`, `schedule *`, `sources *`, `init`, `plugins`. `profile add-seed` fetches arXiv metadata but writes only `profile.yaml`: unlocked, say so.
  - **Datasette** (`serve.py` canned queries INSERT into `votes`/`saves` with raw SQL): a second write path outside `library_api`. Left as is (the spec makes Datasette a stopgap); document it in `browsing.md` and later in `architecture.md`.

- [ ] **Step 1: Write failing tests**

```python
def test_lock_records_holder(tmp_path):
    with run_lock(tmp_path, "rpipe run"):
        with pytest.raises(RunLocked) as exc:
            with run_lock(tmp_path, "rpipe summarize"):
                pass
    assert exc.value.holder == "rpipe run"
    assert "rpipe run" in str(exc.value)

def test_lock_released_holder_cleared(tmp_path):
    with run_lock(tmp_path, "rpipe run"):
        pass
    with run_lock(tmp_path, "rpipe summarize"):  # no raise
        pass

def test_lock_holder_cleared_after_exception(tmp_path): ...  # raise inside the with -> lock file empty, next lock works
```

In `tests/test_runs.py`, `test_locked_commands_stop` parametrized over every locked command from Step 0 (with the minimal arguments each needs, e.g. `["gold", "add", "2307.15043"]`, `["shelf", "add", str(note)]`, `["model", "tune", "--undo"]`): hold `run_lock(vault, "rpipe run")`, invoke, assert `exit_code == 75`, `"rpipe run is already working" in result.stderr`, and `papers` / `runs` row counts unchanged. Each command must hit the lock before any network, model or embedder call (patch those to raise, so a missed lock fails loudly).

- [ ] **Step 2: Run, verify they fail**

Run: `uv run pytest tests/test_run_lock.py tests/test_runs.py -q`
Expected: FAIL (`run_lock()` missing `holder`; commands exit 0 or 1, not 75).

- [ ] **Step 3: Implement.** `run_lock`: open the lock file with `"a+"`; after `flock` succeeds, `truncate(0)`, write `holder`, flush; on failure, seek 0 and read it into `RunLocked.holder`; on release, truncate. Wrap each locked command's body (after `_open`) in `with _pipeline_lock(ctx, "rpipe <command words>")`; `run` uses it too, replacing its inline `try/except RunLocked`. Commands that do not call `_open` (e.g. `model tune`, `model recommend`) resolve the vault the same way `_open` does. `open_db` passes `timeout=5.0` (Python's default, now explicit).

- [ ] **Step 4: Run the full suite.** `uv run pytest -q` and `uv run ruff check .`: all pass.

- [ ] **Step 5: Docs** as listed in Files.

- [ ] **Step 6: Commit** (`feat: one lock for every pipeline command, naming the holder`), code and docs together.

---

### Task 2: `library.py`, the user-action functions

**Files:**
- Create: `src/research_pipeline/library.py`
- Modify: `src/research_pipeline/db.py` (remove the moved functions)
- Modify importers: `cli.py`, `triage.py` (`current_votes`, `list_saved`), `rank.py` (`shelf_chunk_rows`, `shelf_embedding_id`), `digest.py` (`current_votes`, `is_saved`), `shelf.py` (`shelf_embedding_id`, `list_shelf_items`, `upsert_shelf_item`, `delete_shelf_item`). `review.py` and `serve.py` need no change.
- Test: update imports in `tests/test_feedback_db.py`, `test_shelf_db.py`, `test_db.py`, `test_shelf.py`, `test_triage.py`, `test_rank.py`, `test_digest.py`, `test_cli_feedback.py`, `test_cli_review.py`, `test_cli_shelf.py`, `test_review.py`, `test_serve.py` as needed; new `tests/test_library.py`
- Docs: `docs/guide/data-model.md` (which module writes which tables), `docs/guide/shelf.md`, `docs/guide/digest.md` (if it names where votes/saves are read)

**Interfaces:**
- Produces in `research_pipeline.library`, same signatures and behaviour as today in `db.py`: `record_vote`, `record_skip`, `clear_skip`, `current_votes`, `set_saved`, `is_saved`, `list_saved`, `shelf_embedding_id`, `upsert_shelf_item`, `delete_shelf_item`, `list_shelf_items`, `shelf_chunk_rows`, and the private `_delete_shelf_embeddings` (used by `delete_shelf_item`).
- Produces: `library.PIPELINE_READS: frozenset[str] = frozenset({"current_votes", "list_saved", "is_saved", "shelf_chunk_rows", "shelf_embedding_id"})` — exactly what `triage`, `rank` and `digest` use today; the only names pipeline modules may import from `library` (Task 6).
- Stays in `db.py`: connection, schema, `_now`, `_require_paper`, `UnknownPaperError`, paper plumbing, and the pipeline's own writes (`start_run`, `finish_run`, `save_fulltext`, `save_summary`, `record_failure`, `mark_in_digest`, ...).

- [ ] **Step 1: Write the failing test**

```python
def test_db_no_longer_has_user_action_functions():
    import research_pipeline.db as db
    for name in ("record_vote", "set_saved", "current_votes", "upsert_shelf_item", "shelf_chunk_rows"):
        assert not hasattr(db, name), name

def test_pipeline_reads_exist():
    import research_pipeline.library as library
    assert all(callable(getattr(library, n)) for n in library.PIPELINE_READS)
```

- [ ] **Step 2: Run, verify it fails** (`uv run pytest tests/test_library.py -q`).
- [ ] **Step 3: Move the functions** (cut and paste, no behaviour change); update every import. No re-exports from `db.py`.
- [ ] **Step 4: Run the full suite**, expected all pass.
- [ ] **Step 5: Docs** as listed.
- [ ] **Step 6: Commit** (`refactor: move user-action functions from db.py to library.py`).

---

### Task 3: The `requests` table, `ids.py` and `library_api.request`

**Files:**
- Modify: `src/research_pipeline/db.py` (`requests` table in the schema; `SCHEMA_VERSION = 4`; `_migrate` step `if from_version < 4: pass  # requests table, created by the schema script`; `REQUEST_KINDS`, `insert_request`, `requests_by_status`)
- Create: `src/research_pipeline/ids.py` (standard library only): `normalise_paper_id(s: str) -> str`, the logic of `profile_edit.normalise_arxiv_id` moved here with its regexes; `profile_edit.normalise_arxiv_id` becomes a thin wrapper so `seed_input`, `gold` and profile code are unchanged
- Modify: `cli._resolve_paper` (try `ids.normalise_paper_id` first, catching `ValueError`; then the exact match; then the title search)
- Create: `src/research_pipeline/library_api.py` (only `request` in this task)
- Test: `tests/test_requests.py`, `tests/test_ids.py`, one versioned-id case in the existing vote CLI tests
- Docs: `docs/guide/data-model.md` (table, statuses, schema 4), `docs/guide/glossary.md` (request list), `docs/guide/review.md` (`rpipe vote` accepts `arXiv:`/`vN`/URL spellings)

```sql
CREATE TABLE IF NOT EXISTS requests (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL,          -- canonical JSON: json.dumps(payload, sort_keys=True)
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | failed
    tries      INTEGER NOT NULL DEFAULT 0,
    error      TEXT,
    result     TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

**Interfaces:**
- Produces in `db.py`: `REQUEST_KINDS: tuple[str, ...] = ("summarize",)`; `insert_request(conn, kind: str, payload: dict) -> int` (returns the id of an existing `pending`/`running` row with the same kind and canonical payload instead of inserting; commits); `requests_by_status(conn, *statuses: str) -> list[sqlite3.Row]` (oldest first).
- Produces in `library_api.py`: `request(conn, kind: str, payload: dict) -> int`. `ValueError` for a kind not in `REQUEST_KINDS` or a `summarize` payload without a string `paper_id`; normalises `paper_id` with `ids.normalise_paper_id` (a non-id raises `UnknownPaperError`, so callers get one error type), looks it up with `db.get_paper`, `UnknownPaperError` if absent; stores the normalised id. No title search (spec: a known paper id).

- [ ] **Step 1: Write failing tests**

```python
def test_request_inserts_pending(conn_with_paper):           # paper "2611.25005"
    rid = library_api.request(conn, "summarize", {"paper_id": "2611.25005"})
    row = requests_by_status(conn, "pending")[0]
    assert row["id"] == rid and row["kind"] == "summarize" and row["tries"] == 0

def test_request_dedupes_pending(conn_with_paper):
    a = library_api.request(conn, "summarize", {"paper_id": "2611.25005"})
    b = library_api.request(conn, "summarize", {"paper_id": "arXiv:2611.25005v2"})
    assert a == b and len(requests_by_status(conn, "pending")) == 1

def test_request_unknown_paper_refused(conn_with_paper):
    with pytest.raises(UnknownPaperError):
        library_api.request(conn, "summarize", {"paper_id": "9999.99999"})
    with pytest.raises(UnknownPaperError):
        library_api.request(conn, "summarize", {"paper_id": "hello"})
    assert requests_by_status(conn, "pending", "running", "done", "failed") == []

def test_request_unknown_kind_refused(conn_with_paper):
    with pytest.raises(ValueError):
        library_api.request(conn, "fetch_url", {"url": "https://x"})

def test_done_request_can_be_asked_again(conn_with_paper): ...  # mark row done, request again -> new id
def test_old_vault_gains_requests_table(tmp_path): ...  # db at user_version 3 opens, version becomes 4, table exists

# tests/test_ids.py
@pytest.mark.parametrize("raw", ["2611.25005", "arXiv:2611.25005v2", "https://arxiv.org/abs/2611.25005v1",
                                 "https://arxiv.org/pdf/2611.25005.pdf"])
def test_normalise_paper_id(raw): assert normalise_paper_id(raw) == "2611.25005"
def test_normalise_old_style(): assert normalise_paper_id("cs/0112017v1") == "cs/0112017"
def test_normalise_rejects_text(): pytest.raises(ValueError, normalise_paper_id, "hello")
def test_resolve_paper_accepts_versioned_id(...): ...  # `rpipe vote 2611.25005v2 up` exits 0, vote stored on 2611.25005
```

Existing `profile_edit` tests must pass unchanged through the wrapper.

- [ ] **Step 2: Run, verify fail.** `uv run pytest tests/test_requests.py tests/test_ids.py -q`
- [ ] **Step 3: Implement** as specified (`ids.py` first).
- [ ] **Step 4: Run the full suite**, expected all pass.
- [ ] **Step 5: Docs** as listed.
- [ ] **Step 6: Commit** (`feat: requests table, shared paper-id normaliser, library_api.request`).

---

### Task 4: `library_api` reads and writes; route app commands through it

**Files:**
- Modify: `src/research_pipeline/library_api.py`
- Modify: `src/research_pipeline/cli.py` (`vote`, `save`, `unsave`, `feedback`, `review`, `show`, `shelf add|list|remove|sync` call `library_api`, then format for the terminal)
- Test: `tests/test_library_api.py`; `tests/test_architecture.py` (first test, Step 5); existing CLI tests pass unchanged
- Docs: create `docs/guide/architecture.md` with the section "The app's door" (Task 6 completes the page); update `review.md`, `shelf.md`, `browsing.md` (Datasette bypasses `library_api`)

**Interfaces:**
- Produces in `library_api.py` (all take `conn: sqlite3.Connection` first):

```python
class PaperView(BaseModel):
    id: str; title: str; abstract: str; authors: list[str]; url: str
    published: str | None; status: str; digest_date: str | None
    triage_score: float | None; triage_reasons: list[str]   # `rpipe review`/`show` print "why: ..." from these
    summary: PaperSummary | None                            # carries read_mode and parts for `rpipe show`
    vote: Literal[-1, 0, 1]; saved: bool

class ShelfItemView(BaseModel):
    name: str; added_at: str; chunks: int

class QueueProgress(BaseModel): handled: int; total: int
class VoteView(BaseModel): paper_id: str; title: str; vote: Literal[-1, 1]; voted_at: str

def get_paper(conn, paper_id: str) -> PaperView            # UnknownPaperError if absent
def digest_papers(conn, digest_date: str | None = None) -> list[PaperView]  # None = latest digest; score order
def recent_papers(conn, n: int) -> list[PaperView]         # wraps review.recent_digest_papers (rpipe show --recent)
def saved_papers(conn) -> list[PaperView]                  # newest save first
def search_papers(conn, query: str, limit: int = 20) -> list[PaperView]
def vote(conn, paper_id: str, direction: Literal["up", "down", "clear"]) -> None
def save(conn, paper_id: str, saved: bool) -> None
def skip(conn, paper_id: str) -> None
def unskip(conn, paper_id: str) -> None
def review_queue(conn) -> list[PaperView]                  # wraps review.review_queue
def later_queue(conn) -> list[PaperView]                   # wraps review.later_queue
def queue_progress(conn, digest_date: str) -> QueueProgress
def list_votes(conn) -> list[VoteView]                     # replaces the raw title SQL in `feedback`
def latest_digest_date(conn) -> str | None
def shelf_items(conn) -> list[ShelfItemView]
def shelf_add(conn, my_work: Path, embedder: Embedder, source: str) -> ShelfItemView  # wraps shelf.add_to_shelf
def shelf_remove(conn, my_work: Path, name: str) -> list[str]   # names removed
def shelf_sync(conn, my_work: Path, embedder: Embedder, *, reporter: Reporter | None = None) -> SyncResult
```

`search_papers` builds the FTS query from word tokens only: `" ".join(f'"{t}"' for t in re.findall(r"\w+", query))`; no tokens → `[]`; ordered by FTS `rank`. `_resolve_paper` (title search with an interactive picker) and `_refresh_hint` stay in `cli.py` as terminal behaviour with their own SQL; note both in `architecture.md`. `_show_paper` takes a `PaperView` and keeps using `render_summary` for the text (formatting is the composition layer's job).

- [ ] **Step 1: Write failing tests**

```python
def test_get_paper_returns_view_with_summary_vote_saved(...):
    v = library_api.get_paper(conn, "2611.25005")
    assert isinstance(v, PaperView) and v.vote == 1 and v.saved and v.summary is not None

def test_get_paper_unknown(...): pytest.raises(UnknownPaperError)
def test_digest_papers_latest_in_score_order(...): ...
def test_recent_papers_matches_review_module(...): ...
def test_saved_papers_newest_first(...): ...
@pytest.mark.parametrize("q", ['"', "*", "AND", "-", "(", "fuzz* OR (", ""])
def test_search_never_raises(q, ...): assert isinstance(library_api.search_papers(conn, q), list)
def test_search_finds_title_word(...): ...
def test_vote_clear_sets_zero(...): ...
def test_review_queue_later_queue_progress(...): ...
def test_list_votes_has_titles(...): ...
def test_shelf_add_returns_item_view(...): ...
def test_views_serialize_to_json(...): json.loads(library_api.get_paper(conn, pid).model_dump_json())
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement**, delegating writes to `library` and shelf work to `shelf`. Route the listed commands; terminal output stays byte-identical (existing CLI tests are the proof), including `why:` and the `read in parts:` line of `rpipe show`.
- [ ] **Step 4: Run the full suite**, expected all pass.
- [ ] **Step 5: Exit criterion test.** `tests/test_architecture.py::test_app_commands_use_only_library_api`: with `ast`, inspect the bodies of `vote`, `_save_command` (behind `save`/`unsave`), `feedback`, `review`, `show`, `_show_paper` and the `shelf_*` commands in `cli.py`; assert none calls a `db`/`library` write (`record_*`, `set_saved`, `upsert_*`, `delete_*`, `mark_*`, `save_*`, `start_run`, `finish_run`) or a pipeline module; allowed: `library_api`, `_open`, `_resolve_paper`, `_fail`, formatting helpers, `render_summary`. Fix any command that fails.
- [ ] **Step 6: Docs** as listed.
- [ ] **Step 7: Commit** (`feat: library_api, the app's one door; app commands use it`).

---

### Task 5: The pipeline works through requests; shelf sync moves before triage

**Files:**
- Create: `src/research_pipeline/requests_queue.py`
- Modify: `src/research_pipeline/db.py` (`mark_request`, `reset_running_requests`)
- Modify: `src/research_pipeline/summarize.py` (constant `LIMIT_STOPPED` for the usage-limit error line, used where that line is built today)
- Modify: `src/research_pipeline/cli.py` (new `requests` Typer group `run`, `list`; `requests` stage in `_run_locked`; `_run_locked`'s own copy of the limit line uses `LIMIT_STOPPED`; `has_work`; `health_line`; shelf sync before triage in `run` and `triage`)
- Modify: `src/research_pipeline/triage.py` (drop `sync_shelf` and the `my_work` parameter)
- Test: `tests/test_requests_queue.py`, `tests/test_triage.py` (every `run_triage(...)` call changes), `tests/test_runs.py`, `tests/test_cli_requests.py`
- Docs: `running.md` (`rpipe requests run|list`, the drain, retries), `triage.md` and `shelf.md` (sync now happens before triage), `scheduling.md` (the nightly run also drains requests), `digest.md` (health line request counts), `summaries.md` (requested summaries), `glossary.md` (retry rule)

**Interfaces:**
- Consumes: `requests_by_status`, `REQUEST_KINDS` (Task 3); `run_summarize` (signature in the header); `TIMEOUT_STOPPED`, `LIMIT_STOPPED`, `MAX_SUMMARY_TRIES`; `progress.stage`; `library_api.shelf_sync` (Task 4).
- Produces in `db.py`: `mark_request(conn, rid: int, status: str, *, error: str | None = None, result: dict | None = None, add_try: bool = False, undo_try: bool = False) -> None` (sets `updated_at`, commits); `reset_running_requests(conn) -> int`.
- Produces in `requests_queue.py`: `drain_requests(conn, llm, fetch, num_ctx, *, reporter: Reporter | None = None, mode: Literal["whole", "split"] = "whole", fallback_above: int | None = None) -> tuple[dict[str, int], list[str]]`; counts keys `requests_done`, `requests_failed`, `requests_waiting`.
- Produces in `triage.py`: `run_triage(conn, embedder, profile, seed_texts, *, use_shelf: bool = False, reporter=None)` replaces the `my_work` parameter.

**Decisions (the 11-22 open issues, settled):**
- **Outcome of one request** comes from `run_summarize(..., paper_ids=[pid], reporter=<notes adapter>, mode=..., fallback_above=...)`'s return value: `counts["summarized"] == 1` → done; `LIMIT_STOPPED in errors` → stopped; otherwise failed (`TIMEOUT_STOPPED in errors` → failed and stop draining).
- **Retry counter:** `requests.tries` is the source of truth. `paper_ids` already ignores the per-paper cap (an explicit ask), so `papers.retry_count` keeps being bumped by `record_failure` as a side effect, exactly as `rpipe summarize ID` does today, and is not consulted.
- **Order in `rpipe run`:** summarize → requests → rerun → digest. Requests use the same `mode`/`fallback_above` as the summarize stage, and a requested paper that got cut is picked up by the rerun stage like any other.
- **Skip the drain** when `sum_errors` contains `LIMIT_STOPPED` or `TIMEOUT_STOPPED` (the model just stopped; don't burn a call). A timed-out drain also passes `model_timed_out=True` to `run_reruns`.
- **`has_work`** in `_run_locked` = papers to summarize **or** pending/running requests (Review Focus 5).
- **Progress:** the drain opens its own stage `"requests"` with `total=N` and emits `step(pid, current=i, outcome=..., detail=...)`; `run_summarize` gets an adapter reporter that forwards only its `note` events into the `requests` stage (dropping its `[1/1]` start/step/done), so the owner sees "fetching…", "writing the summary for…" under `[i/N]`.

Drain algorithm:
1. `reset_running_requests`.
2. For each `pending` `summarize` row with `tries < MAX_SUMMARY_TRIES`, oldest first: `mark_request(running, add_try=True)`, run as above.
3. done → `done`, `result={"summarized": True}`, clear `error`. failed → `failed` if `tries == MAX_SUMMARY_TRIES` else `pending`, `error` = the paper's error line. stopped → `pending` with `undo_try=True`, stop. Timeout → handled as failed, then stop.
4. `requests_waiting` = pending rows left.

`rpipe requests run`: takes the lock (`"rpipe requests run"`), `_require_model`, `_ready_llm`, `_summarize_plan(..., has_work=bool(pending))`, drains with `_reporter()`, prints counts via `_report`. `rpipe requests list`: no lock; newest first, one line per row: `id  status  kind  paper_id  tries  created_at  [error]`; `"No requests."` when empty. `health_line` adds ` · requests done N`, ` · requests failed N`, ` · requests waiting N` when non-zero.

Shelf sync: in `cli.run` (stage `triage`) and `cli.triage`, call `library_api.shelf_sync(conn, vault / "my_work", embedder, reporter=reporter)` then `run_triage(..., use_shelf=True)`.

- [ ] **Step 1: Write failing tests**

```python
def test_drain_summarizes_and_marks_done(...): status == "done", summary saved, requests_done == 1
def test_drain_resets_stale_running(...): row left "running" -> "done"
def test_drain_failure_retries_then_fails(...): 3 drains with a failing FakeLLM -> "pending","pending","failed"; tries == 3
def test_drain_limit_keeps_pending_without_burning_try(...): LlmLimitReached -> both rows "pending", tries == 0, second row never tried
def test_drain_timeout_stops(...): LlmTimeout on row 1 -> row 2 untouched
def test_drain_progress_counts_requests(...): ListReporter shows stage "requests", steps current 1..N, total N, and the inner notes
def test_nightly_run_drains_requests(...): rpipe run with one pending request -> "done"; runs.counts requests_done == 1
def test_nightly_run_drains_on_quiet_night(...): no new papers, one pending request -> model loaded, request "done"
def test_drain_skipped_after_usage_limit_stop(...): summarize stage hit the limit -> no drain, rows stay pending, tries 0
def test_health_line_shows_request_counts(...): "requests done 1" and "requests waiting 2" appear
def test_requests_list_output(...): "No requests." when empty; one line per row newest first
def test_requests_run_takes_lock(...): exit 75 while "rpipe run" holds it
def test_triage_does_not_sync_shelf(...): run_triage(use_shelf=True) with a new my_work/ file does not index it; `rpipe triage` does
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** as decided above.
- [ ] **Step 4: Run the full suite**, expected all pass.
- [ ] **Step 5: Docs** as listed.
- [ ] **Step 6: Commit** (`feat: pipeline drains app requests; shelf sync moves before triage`).

---

### Task 6: Boundary tests and the architecture page

**Files:**
- Modify: `tests/test_architecture.py` (add the tests below)
- Create: `src/research_pipeline/workbench/__init__.py` (docstring only: "Future app features. Import only shared modules, `library` and `library_api`.")
- Complete: `docs/guide/architecture.md`
- Modify: `docs/guide/README.md` (link), `docs/guide/glossary.md` (pipeline, app, composition layer, one door), the spec's "Which code is which" table (match the classification below; drop the obsolete "branches unmerged" constraint), `CLAUDE.md` (one line under Conventions: "Pipeline code and app code are separate groups; see docs/guide/architecture.md." — ask the owner first)

**Interfaces:**
- Consumes: `library.PIPELINE_READS`.

Module classification (every module and package in `src/research_pipeline`, checked against imports on 2026-11-25):

```python
SHARED = {"models", "summary_models", "db", "vault", "embed", "llm", "backends", "defaults",
          "seeds", "profile_edit", "ids", "progress", "categories", "schedule"}
PIPELINE = {"collect", "sources", "triage", "rank", "fulltext", "summarize", "prompts", "digest",
            "evaluate", "gold", "judge", "pruning", "parts", "citations", "spill",
            "ollama_fit", "ollama_tune", "bench", "scan", "catalog", "recommender", "machine",
            "registry", "run_lock", "requests_queue", "seed_input", "onboarding"}
APP = {"library", "shelf", "review", "serve", "library_api", "workbench"}
COMPOSITION = {"cli", "progress_render"}
```

Non-obvious placements:
- `schedule` is SHARED (changed from the spec): it imports nothing from the package, and `profile_edit` uses its `parse_time`; its settings (`set-deadline`) are ones an app settings page would show. `categories` (no imports, used by `profile_edit`) is SHARED.
- `llm` imports `bench` only inside `if TYPE_CHECKING:`; the import scanner must skip `TYPE_CHECKING` blocks (they never run), or `llm` would wrongly fail `test_shared_imports_only_shared`.
- `registry` imports `ollama_fit` and `sources.*`: PIPELINE.
- `progress_render` turns progress events into terminal text: COMPOSITION, with `cli`.
- `db` is SHARED but still holds the pipeline's own writes; `test_app_commands_use_only_library_api` (Task 4) covers app commands, and a later split of `db.py` is out of scope.
- `ids` imports nothing from the package.

Tests (`ast` import inspection, like `test_shelf_is_local_only`; one helper `_internal_imports(module) -> set[str]` that resolves relative imports, maps `research_pipeline.x.y` to `x`, and skips `if TYPE_CHECKING:` bodies):

```python
def test_every_module_is_classified(): each module/package under src/research_pipeline (except __init__) is in exactly one group
def test_shared_imports_only_shared(): ...
def test_pipeline_never_imports_app(): no PIPELINE module imports any APP module
def test_pipeline_reads_only_listed_library_functions(): every `from research_pipeline.library import X` in PIPELINE has X in PIPELINE_READS
def test_app_never_imports_pipeline(): no APP module imports any PIPELINE module
def test_library_api_is_local_only(): llm and backends are not in the transitive closure of library_api's imports
    # comment: the first function that sends library content to a model must wire require_local and replace this test
def test_library_api_returns_data_not_text(): for every public function, typing.get_type_hints(f)["return"] is not str
def test_type_checking_imports_ignored(): the helper reports no `bench` for `llm`
```

Architecture page shape (CLAUDE.md): What it does → How it works (the spec's diagram, the groups with the table above in plain words, the one door, the request list, the lock) → Why (one database vs two, with the rejected two-file design; data not text, for a future app) → How to check it (`uv run pytest tests/test_architecture.py`, `rpipe requests list`) → Where the code is. Include the known side doors: `db.py` pipeline writes, Datasette's canned write queries, `_resolve_paper`/`_refresh_hint` SQL in `cli.py`.

- [ ] **Step 1: Write the tests.** Run them; fix any real boundary violation in the code, not the test (after Task 5 none is expected).
- [ ] **Step 2: Write the docs** as listed.
- [ ] **Step 3: Run the full suite** and `uv run ruff check .`, expected all pass.
- [ ] **Step 4: Commit** (`test: enforce the pipeline/app boundary; docs: architecture page`).

---

## After the plan

- Real check on the owner's machine, in a throwaway copy of the vault (never the real config): `rpipe requests list` empty; queue one request through `library_api.request` from `uv run python -c ...`; `rpipe run --vault COPY` drains it and the digest health line shows `requests done 1`; a second `rpipe summarize --vault COPY` started during that run exits 75 naming `rpipe run`.
- Update `docs/superpowers/handoff.md` and the master plan (Claude Docs): Phase 7 done, next the app designs.
- Whole-branch review before the owner merges.
