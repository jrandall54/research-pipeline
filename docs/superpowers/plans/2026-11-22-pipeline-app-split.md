# Pipeline and App Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Organize the code into a pipeline group and an app group over one database, give the app one door (`library_api`) that returns data objects, let the app ask the pipeline for work through a request list, and put every pipeline command under one lock.

**Architecture:** Existing modules stay where they are. User-action functions move from `db.py` to a new `library.py`; a new `library_api.py` is the app's only door and returns Pydantic objects; a `requests` table plus `requests_queue.py` let the app queue summaries the pipeline runs later. `cli.py` is the composition layer allowed to import both groups. Tests that read each module's imports enforce the boundary.

**Tech Stack:** Python 3.12, SQLite (WAL), Pydantic v2, Typer, pytest. `uv run pytest -q`.

**Spec:** `docs/superpowers/specs/2026-11-22-backend-frontend-split-design.md`

**Starts from:** `main` after `phase-5-shareable` and `summarize-backlog` are merged. Work on a new branch `pipeline-app-split`.

## Global Constraints

- Docs in `docs/guide/` change in the same commit as the code they describe (CLAUDE.md). Every new term goes in `docs/guide/glossary.md`.
- Use `rpipe` in docs, messages and examples.
- Tests run offline: fixtures, `FakeEmbedder` and `FakeLLM` from `tests/conftest.py`. No network.
- One database file, `vault.sqlite`; no data moves. The only schema change is the `requests` table (schema version 3 → 4).
- Lock-held exit code stays `75` (`RUN_LOCKED_EXIT`).
- Request retry cap is `MAX_SUMMARY_TRIES` (3), imported, not re-typed.
- `library_api` functions never return a display string.
- Behaviour of collect, triage scoring, fetch and summarize is unchanged.

## Review Focus

1. **A hand-run pipeline command while another holds the lock**: the second stops at once with exit 75 and a message naming the holder (e.g. `rpipe run`), and changes nothing. Tested in Task 1.
2. **A search string with FTS syntax in it** (`"`, `*`, `AND`, `-`, a lone `(`): `search_papers` returns results or `[]`, never raises `sqlite3.OperationalError`. Tested in Task 4.
3. **A request for a paper id with a version suffix or `arXiv:` prefix** (`2611.25005v2`): resolved the same way `rpipe vote` resolves ids, or refused with `UnknownPaperError`; never a queued row that can only fail. Tested in Task 3.
4. **The nightly run dies while a request is `running`**: the next drain resets it to `pending` and processes it; a usage limit leaves the rest `pending` without burning a try. Tested in Task 5.
5. **An app write (vote) during a long pipeline write**: waits up to the 5 s busy timeout instead of failing at once. Tested in Task 1.

---

### Task 1: One lock for every pipeline command

**Files:**
- Modify: `src/research_pipeline/run_lock.py`
- Modify: `src/research_pipeline/cli.py` (`run`, `collect`, `triage`, `summarize`, `digest`, `eval_run`, `model_fit`, `gold_add`; new helper `_pipeline_lock`)
- Modify: `src/research_pipeline/db.py` (`open_db`: explicit `timeout=5.0`)
- Test: `tests/test_run_lock.py`, `tests/test_runs.py`
- Docs: `docs/guide/running.md` (lock section: which commands, the message, the one changed workflow), `docs/guide/glossary.md` (WAL, busy timeout)

**Interfaces:**
- Produces: `run_lock(vault: Path, holder: str) -> ContextManager[None]`; `RunLocked.holder: str` (the holder text read from the lock file, `"unknown"` if empty); `cli._pipeline_lock(ctx: _Context, holder: str) -> ContextManager[None]`, which turns `RunLocked` into `typer.Exit(RUN_LOCKED_EXIT)` after printing `"<holder> is already working on <vault>; nothing was started. Wait for it to finish (see \`rpipe runs\`)."` to stderr.

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
```

In `tests/test_runs.py`, parametrize over the CLI invocations `["collect"]`, `["triage"]`, `["summarize"]`, `["digest"]`, `["eval", "run"]`, `["model", "fit"]`, `["gold", "add", "2307.15043"]`: hold `run_lock(vault, "rpipe run")` in the test, invoke, assert `exit_code == 75`, `"rpipe run is already working" in result.stderr`, and that `papers` / `runs` row counts are unchanged.

`test_vote_waits_for_busy_writer`: open a second connection, `BEGIN IMMEDIATE`, start `rpipe vote <id> up` in a thread, commit the blocker after 0.5 s, assert the vote exit code is 0 and the row exists.

- [ ] **Step 2: Run, verify they fail**

Run: `uv run pytest tests/test_run_lock.py tests/test_runs.py -q`
Expected: FAIL (`run_lock()` takes 1 positional argument; commands exit 0 or 1, not 75).

- [ ] **Step 3: Implement**

`run_lock`: after `flock` succeeds, `truncate(0)`, write `holder`, flush; on failure read the file to fill `RunLocked.holder`; on release truncate. Open with `"a+"` so the read works. `_pipeline_lock` wraps it in `cli.py`. Wrap each listed command's body after `_open` in `with _pipeline_lock(ctx, "rpipe <command>")`. `run` passes `"rpipe run"`. `open_db` passes `timeout=5.0` to `sqlite3.connect` (same as Python's default, now explicit and documented). App commands (`vote`, `save`, `unsave`, `feedback`, `review`, `shelf`, `serve`, `runs`) take no lock.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit** (`feat: one lock for every pipeline command, naming the holder`), code and docs together.

---

### Task 2: `library.py`, the user-action functions

**Files:**
- Create: `src/research_pipeline/library.py`
- Modify: `src/research_pipeline/db.py` (remove the moved functions; keep `_require_paper`, `UnknownPaperError`, `_now`, `get_paper`)
- Modify importers: `cli.py`, `triage.py`, `rank.py`, `digest.py`, `shelf.py`, `review.py` if needed
- Test: move the tests of the moved functions from `tests/test_feedback_db.py` and `tests/test_shelf_db.py` to import from `library`; add `tests/test_library.py` only for the one new test below
- Docs: `docs/guide/data-model.md` (which module writes which tables), `docs/guide/shelf.md` (function locations)

**Interfaces:**
- Produces in `research_pipeline.library`, same signatures and behaviour as today in `db.py`: `record_vote`, `record_skip`, `clear_skip`, `current_votes`, `set_saved`, `is_saved`, `list_saved`, `shelf_embedding_id`, `upsert_shelf_item`, `delete_shelf_item`, `list_shelf_items`, `shelf_chunk_rows`.
- Produces: `library.PIPELINE_READS: frozenset[str] = {"current_votes", "list_saved", "is_saved", "shelf_chunk_rows", "shelf_embedding_id"}`, the only names pipeline modules may import from `library` (used by Task 6's boundary test).

- [ ] **Step 1: Write the failing test**

```python
def test_db_no_longer_has_user_action_functions():
    import research_pipeline.db as db
    for name in ("record_vote", "set_saved", "current_votes", "upsert_shelf_item", "shelf_chunk_rows"):
        assert not hasattr(db, name), name
```

- [ ] **Step 2: Run, verify it fails** (`uv run pytest tests/test_library.py -q`, expected FAIL).

- [ ] **Step 3: Move the functions** with `git mv`-style cut and paste (no behaviour change), and update every import. No re-exports from `db.py`.

- [ ] **Step 4: Run the full suite**, expected all pass.

- [ ] **Step 5: Commit** (`refactor: move user-action functions from db.py to library.py`).

---

### Task 3: The `requests` table and `library_api.request`

**Files:**
- Modify: `src/research_pipeline/db.py` (schema: `requests` table; `SCHEMA_VERSION = 4`; `_migrate` step `if from_version < 4: pass  # requests table`; `insert_request`, `requests_by_status`)
- Create: `src/research_pipeline/library_api.py` (only `request` in this task)
- Test: `tests/test_requests.py`
- Docs: `docs/guide/data-model.md` (the table and statuses), `docs/guide/glossary.md` (request list)

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
- Produces in `library_api.py`: `request(conn, kind: str, payload: dict) -> int`. Raises `ValueError` for a kind not in `REQUEST_KINDS` or a `summarize` payload without a string `paper_id`; resolves `paper_id` with the same normalization `rpipe vote` uses (`_resolve_paper`'s id rules: strip `arXiv:`, drop `vN`, case-insensitive), then `UnknownPaperError` if absent; stores the resolved id.

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
    assert requests_by_status(conn, "pending", "running", "done", "failed") == []

def test_request_unknown_kind_refused(conn_with_paper):
    with pytest.raises(ValueError):
        library_api.request(conn, "fetch_url", {"url": "https://x"})

def test_done_request_can_be_asked_again(conn_with_paper): ...  # mark row done, request again -> new id

def test_old_vault_gains_requests_table(tmp_path): ...  # db at user_version 3 opens, version becomes 4, table exists
```

If `_resolve_paper` lives in `cli.py`, move its id normalization into a shared function `db.normalize_paper_id(text: str) -> str` first and have both callers use it.

- [ ] **Step 2: Run, verify fail.** `uv run pytest tests/test_requests.py -q`

- [ ] **Step 3: Implement** as specified.

- [ ] **Step 4: Run the full suite**, expected all pass.

- [ ] **Step 5: Commit** (`feat: requests table and library_api.request`).

---

### Task 4: `library_api` reads and writes; route app commands through it

**Files:**
- Modify: `src/research_pipeline/library_api.py`
- Modify: `src/research_pipeline/cli.py` (`vote`, `save`, `unsave`, `feedback`, `review`, `shelf add|list|remove|sync` call `library_api`, then format for the terminal)
- Test: `tests/test_library_api.py`; existing CLI tests must pass unchanged
- Docs: new section "The app's door" in `docs/guide/architecture.md` (the file is created here; Task 6 completes it), `docs/guide/review.md`, `docs/guide/shelf.md` (commands now go through `library_api`)

**Interfaces:**
- Produces in `library_api.py` (all take `conn: sqlite3.Connection` first):

```python
class PaperView(BaseModel):
    id: str; title: str; abstract: str; authors: list[str]; url: str
    published: str | None; status: str; digest_date: str | None
    triage_score: float | None; summary: PaperSummary | None
    vote: Literal[-1, 0, 1]; saved: bool

class ShelfItemView(BaseModel):
    name: str; added_at: str; chunks: int

def get_paper(conn, paper_id: str) -> PaperView            # UnknownPaperError if absent
def digest_papers(conn, digest_date: str | None = None) -> list[PaperView]  # None = latest digest; score order
def saved_papers(conn) -> list[PaperView]                  # newest save first
def search_papers(conn, query: str, limit: int = 20) -> list[PaperView]
def vote(conn, paper_id: str, direction: Literal["up", "down", "clear"]) -> None
def save(conn, paper_id: str, saved: bool) -> None
def skip(conn, paper_id: str) -> None
def unskip(conn, paper_id: str) -> None
def shelf_items(conn) -> list[ShelfItemView]
def shelf_add(conn, my_work: Path, embedder: Embedder, source: str) -> str      # item name
def shelf_remove(conn, my_work: Path, name: str) -> list[str]
def shelf_sync(conn, my_work: Path, embedder: Embedder) -> SyncResult
```

`search_papers` builds the FTS query from word tokens only: `" ".join(f'"{t}"' for t in re.findall(r"\w+", query))`; an empty token list returns `[]`; results ordered by FTS `rank`.

- [ ] **Step 1: Write failing tests**

```python
def test_get_paper_returns_view_with_summary_vote_saved(...):
    v = library_api.get_paper(conn, "2611.25005")
    assert isinstance(v, PaperView) and v.vote == 1 and v.saved and v.summary is not None

def test_get_paper_unknown(...):  pytest.raises(UnknownPaperError)
def test_digest_papers_latest_in_score_order(...): ...
def test_saved_papers_newest_first(...): ...
@pytest.mark.parametrize("q", ['"', "*", "AND", "-", "(", "fuzz* OR (", ""])
def test_search_never_raises(q, ...): assert isinstance(library_api.search_papers(conn, q), list)
def test_search_finds_title_word(...): assert [p.id for p in library_api.search_papers(conn, "kernel")] == ["..."]
def test_vote_clear_sets_zero(...): ...
def test_views_serialize_to_json(...): json.loads(library_api.get_paper(conn, pid).model_dump_json())
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**, delegating writes to `library` and shelf work to `shelf`. Route the listed CLI commands through `library_api`; terminal output text stays byte-identical.

- [ ] **Step 4: Run the full suite**, expected all pass (existing CLI tests prove the output did not change).

- [ ] **Step 5: Commit** (`feat: library_api, the app's one door; app commands use it`).

---

### Task 5: The pipeline works through requests

**Files:**
- Create: `src/research_pipeline/requests_queue.py`
- Modify: `src/research_pipeline/db.py` (`mark_request(conn, rid: int, status: str, *, error: str | None = None, result: dict | None = None, add_try: bool = False) -> None`, commits; `reset_running_requests(conn) -> int`)
- Modify: `src/research_pipeline/cli.py` (new `requests` Typer group: `run`, `list`; drain step in `_run_locked` after summarize, before digest)
- Modify: `src/research_pipeline/triage.py` and `cli.py`: shelf sync moves to the composition layer (see Step 3)
- Test: `tests/test_requests_queue.py`, `tests/test_triage.py`, `tests/test_runs.py`
- Docs: `docs/guide/running.md` (`rpipe requests run|list`, the drain in the nightly run, retries), `docs/guide/triage.md` and `shelf.md` (sync now happens before triage, not inside it)

**Interfaces:**
- Consumes: `requests_by_status`, `mark_request`, `reset_running_requests`, `MAX_SUMMARY_TRIES`, `run_summarize(conn, llm, fetch, num_ctx, paper_ids=[pid], on_progress=...)`.
- Produces: `drain_requests(conn, llm, fetch, num_ctx, on_progress=None) -> tuple[dict[str, int], list[str]]`, counts keys `requests_done`, `requests_failed`, `requests_waiting`. `run_triage(conn, embedder, profile, seed_texts, use_shelf: bool = False)` replaces the `my_work` parameter.

Drain algorithm (the parts the tests do not determine):
1. `reset_running_requests` (any `running` → `pending`).
2. For each `pending` row with `tries < MAX_SUMMARY_TRIES`, oldest first: mark `running` with `add_try=True`; call `run_summarize(paper_ids=[pid])` with an `on_progress` that captures the outcome.
3. Outcome `done` → `done`, `result={"summarized": True}`. Outcome `failed` → `failed` if `tries` now equals `MAX_SUMMARY_TRIES`, else back to `pending`, `error` = the detail. Outcome `stopped` (usage limit) → back to `pending` and **undo the try** (`tries - 1`), then stop draining; every remaining row stays `pending`. A timeout outcome (`failed` followed by run_summarize's stop) also stops the drain.
4. `requests_waiting` = pending rows left afterwards.

`rpipe requests run`: takes the pipeline lock (`"rpipe requests run"`), needs a model (`_require_model`, `_ready_llm`), prints a line per request via `_paper_progress`, then the counts. `rpipe requests list`: no lock; one line per request newest first: `id  status  kind  paper_id  tries  created_at  [error]`; `"No requests."` when empty.

- [ ] **Step 1: Write failing tests**

```python
def test_drain_summarizes_and_marks_done(fake_llm, ...): status == "done", summary saved
def test_drain_resets_stale_running(...): row left "running" -> processed -> "done"
def test_drain_failure_retries_then_fails(...): 3 drains with a failing fake -> "pending","pending","failed"; tries == 3
def test_drain_limit_keeps_pending_without_burning_try(...): fake raises LlmLimitReached -> both rows "pending", tries == 0, drain stopped after first
def test_nightly_run_drains_requests(...): rpipe run with one pending request -> row "done"; runs.counts has requests_done == 1
def test_requests_list_output(...): "No requests." when empty; one line per row otherwise
def test_requests_run_takes_lock(...): exit 75 while "rpipe run" holds it
def test_triage_does_not_sync_shelf(...): run_triage never imports or calls sync_shelf; shelf items added to my_work/ appear only after cli triage/run
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement.** Shelf sync: remove `sync_shelf` from `triage.py`; in `cli.run` (inside stage `"triage"`) and `cli.triage`, call `library_api.shelf_sync(ctx.conn, ctx.vault / "my_work", embedder)` then `run_triage(..., use_shelf=True)`. Drain in `_run_locked`: new stage `"requests"` after summarize, only when `llm` is not `None`; its counts merge into `counts` and its errors into `errors`, so the digest health line shows them.

- [ ] **Step 4: Run the full suite**, expected all pass.

- [ ] **Step 5: Commit** (`feat: pipeline drains app requests; shelf sync moves before triage`).

---

### Task 6: Boundary tests and the architecture page

**Files:**
- Create: `tests/test_architecture.py`
- Create: `src/research_pipeline/workbench/__init__.py` (docstring only: "Future app features. Import only shared modules, `library` and `library_api`.")
- Complete: `docs/guide/architecture.md`
- Modify: `docs/guide/README.md` (link), `docs/guide/glossary.md` (pipeline, app, composition layer, one door), `CLAUDE.md` (one line under Conventions: "Pipeline code and app code are separate groups; see docs/guide/architecture.md." Ask the owner before editing CLAUDE.md, as the handoff asks.)

**Interfaces:**
- Consumes: `library.PIPELINE_READS`; the groups exactly as the spec's "Which code is which" table lists them.

Tests (import inspection with `ast`, like `test_shelf_is_local_only`):

```python
PIPELINE = ["collect", "triage", "rank", "fulltext", "summarize", "prompts", "digest", "evaluate",
            "gold", "schedule", "ollama_fit", "machine", "run_lock", "requests_queue"]  # plus sources/*
APP = ["library", "shelf", "review", "serve", "library_api"]  # plus workbench/*

def test_pipeline_never_imports_app(): no PIPELINE module imports library_api, review, serve, workbench, shelf
def test_pipeline_reads_only_listed_library_functions(): every `from research_pipeline.library import X` in PIPELINE has X in PIPELINE_READS
def test_app_never_imports_pipeline_stages(): no APP module imports any PIPELINE module
def test_library_api_is_local_only(): library_api imports neither research_pipeline.llm nor research_pipeline.backends (comment: the first function that sends library content to a model must wire require_local and replace this test)
def test_library_api_returns_data_not_text(): for every public function, typing.get_type_hints(f)["return"] is not str
def test_every_module_is_classified(): each module under src/research_pipeline is in SHARED, PIPELINE, APP or is cli.py, so a new module must be placed deliberately
```

Architecture page shape (CLAUDE.md): What it does → How it works (the diagram from the spec, the three groups, the one door, the request list, the lock) → Why (one database vs two, with the rejected two-file design and why; data not text, for a future app) → How to check it (`uv run pytest tests/test_architecture.py`, `rpipe requests list`) → Where the code is.

- [ ] **Step 1: Write the tests.** Run them; fix any real boundary violation they find in the code (not the test).
- [ ] **Step 2: Write the docs.**
- [ ] **Step 3: Run the full suite**, expected all pass.
- [ ] **Step 4: Commit** (`test: enforce the pipeline/app boundary; docs: architecture page`).

---

## After the plan

- Update `docs/superpowers/handoff.md`: state, what changed, the next phase.
- Whole-branch review before the owner merges.
