# Pipeline and App Split Implementation Plan

> **Refresh before executing:** Phase 6 runs first and rewrites `cli.py` command bodies, the `on_progress` signature, and `summarize`/`prompts`. Re-check Tasks 1, 3, 4, 5, 6 against the code after Phase 6 lands. Schema version numbers below mean "current + 1" (the code is at schema version 3 today, `db.SCHEMA_VERSION`; if Phase 6 bumps it, this plan's table step is that number + 1). Line references are from 2026-11-22 and will drift. This pass (2026-11-22) only fixed factual errors against the code; a full refresh follows Phase 6.

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
- One database file, `vault.sqlite`; no data moves. The only schema change is the `requests` table (schema version: current + 1; 3 → 4 if nothing else bumps it first).
- Lock-held exit code stays `75` (`RUN_LOCKED_EXIT`).
- Request retry cap is `MAX_SUMMARY_TRIES` (3), imported, not re-typed.
- `library_api` functions never return a display string.
- Behaviour of collect, triage scoring, fetch and summarize is unchanged.

## Review Focus

1. **A hand-run pipeline command while another holds the lock**: the second stops at once with exit 75 and a message naming the holder (e.g. `rpipe run`), and changes nothing. Tested in Task 1.
2. **A search string with FTS syntax in it** (`"`, `*`, `AND`, `-`, a lone `(`): `search_papers` returns results or `[]`, never raises `sqlite3.OperationalError`. Tested in Task 4.
3. **A request for a paper id with a version suffix or `arXiv:` prefix** (`2611.25005v2`): normalised by one shared pure function (`ids.normalise_paper_id`, extracted from `profile_edit.normalise_arxiv_id` in Task 3), then looked up; an id that is not in the vault is refused with `UnknownPaperError`; never a queued row that can only fail. Today `rpipe vote` does NOT normalise (`cli._resolve_paper` does an exact id match, then a title LIKE search), so Task 3 also makes `_resolve_paper` try the normalised id first, which fixes `rpipe vote 2611.25005v2`. Tested in Task 3.
4. **The nightly run dies while a request is `running`**: the next drain resets it to `pending` and processes it; a usage limit leaves the rest `pending` without burning a try. Tested in Task 5.
5. **An app write (vote) during a long pipeline write**: waits up to the 5 s busy timeout instead of failing at once. This already works today (WAL and sqlite's default 5 s timeout are set in `open_db`); Task 1 only makes the timeout explicit and tests the real behaviour once.

---

### Task 1: One lock for every pipeline command

**Files:**
- Modify: `src/research_pipeline/run_lock.py`
- Modify: `src/research_pipeline/cli.py` (`run`, `collect`, `triage`, `summarize`, `digest`, `eval_run`, `model_fit`, `gold_add`, plus the commands decided in Step 0 below; new helper `_pipeline_lock`)
- Modify: `src/research_pipeline/db.py` (`open_db`: explicit `timeout=5.0`; WAL and the default 5 s timeout already exist, so this is documentation-grade only)
- Modify: `src/research_pipeline/serve.py` (Datasette canned write queries, see Step 0)
- Test (update existing): `tests/test_run_lock.py` (5 tests call `run_lock(vault)` with one argument and assert the old message "another `rpipe run` is already using"), `tests/test_runs.py` (same old message); add the new parametrized lock tests
- Docs: `docs/guide/running.md` (lines ~169 and ~230 quote the old lock message and must change; lock section: which commands, the new message, the one changed workflow), `docs/guide/scheduling.md` (the nightly run and hand-run commands now share one lock), `docs/guide/glossary.md` (WAL, busy timeout)

**Interfaces:**
- Produces: `run_lock(vault: Path, holder: str) -> ContextManager[None]`; `RunLocked.holder: str` (the holder text read from the lock file, `"unknown"` if empty); `cli._pipeline_lock(ctx: _Context, holder: str) -> ContextManager[None]`, which turns `RunLocked` into `typer.Exit(RUN_LOCKED_EXIT)` after printing `"<holder> is already working on <vault>; nothing was started. Wait for it to finish (see \`rpipe runs\`)."` to stderr.

- [ ] **Step 0: Decide the lock list.** Beyond the eight commands above, decide and record in `running.md` for each: `model check` and `model setup` (load/probe Ollama, pull models, use the GPU; `model check` can fight a nightly run for the GPU exactly as `model fit` does, so lock them), `profile suggest` and `profile add-seed` (LLM or arXiv calls with 3 s spacing, no vault DB writes; lock `profile suggest` because it uses the model, leave `add-seed` unlocked and say why), `shelf sync` and `shelf add` (they load the embedder and write many shelf rows, and `rpipe run` triage also syncs the shelf, so two syncs could run at once: lock them with holder `rpipe shelf ...` and still treat them as app commands through `library_api`, or explicitly accept and document the race), `eval grade|report` (vault files only: unlocked), `schedule *`, `init` (unlocked). Also: `serve.py` (lines ~27-75) defines Datasette canned queries that INSERT into `votes`/`saves` with raw SQL. That is a second write path that bypasses `record_vote` validation and `library_api`. Either route it (drop the write queries) or document it in `architecture.md` as a known stopgap path (the spec says Datasette is left as is, so document it). Add every locked command to the parametrized test below.

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

In `tests/test_runs.py`, parametrize over the CLI invocations `["collect"]`, `["triage"]`, `["summarize"]`, `["digest"]`, `["eval", "run"]`, `["model", "fit"]`, `["gold", "add", "2307.15043"]`, plus the Step 0 additions (`["model", "check"]`, `["model", "setup"]`, `["profile", "suggest"]`, `["shelf", "sync"]`, `["shelf", "add", ...]` as decided): hold `run_lock(vault, "rpipe run")` in the test, invoke, assert `exit_code == 75`, `"rpipe run is already working" in result.stderr`, and that `papers` / `runs` row counts are unchanged.

`test_vote_waits_for_busy_writer` (WAL and the 5 s default timeout already exist, so this passes before any change and is NOT a red test; keep it only as a regression guard for the explicit `timeout=5.0`, or drop it): open a second connection, `BEGIN IMMEDIATE`, start `rpipe vote <id> up` in a thread, commit the blocker after 0.5 s, assert the vote exit code is 0 and the row exists. Add one genuinely new test: `test_lock_holder_cleared_after_exception` (an exception inside the `with` leaves an empty lock file).

- [ ] **Step 2: Run, verify they fail**

Run: `uv run pytest tests/test_run_lock.py tests/test_runs.py -q`
Expected: FAIL (`run_lock()` takes 1 positional argument; commands exit 0 or 1, not 75; the updated old tests assert the new message).

- [ ] **Step 3: Implement**

`run_lock`: after `flock` succeeds, `truncate(0)`, write `holder`, flush; on failure read the file to fill `RunLocked.holder`; on release truncate. Open with `"a+"` so the read works. `_pipeline_lock` wraps it in `cli.py`. Wrap each listed command's body after `_open` in `with _pipeline_lock(ctx, "rpipe <command>")`. `run` passes `"rpipe run"`. `open_db` passes `timeout=5.0` to `sqlite3.connect` (same as Python's default, now explicit and documented). App commands (`vote`, `save`, `unsave`, `feedback`, `review`, `shelf`, `serve`, `runs`) take no lock.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Write the docs** (CLAUDE.md: same commit): update the lock section and both quoted messages in `running.md`, add the lock note to `scheduling.md`, add WAL and busy timeout to `glossary.md`, document the Datasette write path decision.

- [ ] **Step 6: Commit** (`feat: one lock for every pipeline command, naming the holder`), code and docs together.

---

### Task 2: `library.py`, the user-action functions

**Files:**
- Create: `src/research_pipeline/library.py`
- Modify: `src/research_pipeline/db.py` (remove the moved functions; keep `_require_paper`, `UnknownPaperError`, `_now`, `get_paper`)
- Modify importers (all mandatory): `cli.py`; `triage.py`; `rank.py` (line ~12 imports `shelf_chunk_rows, shelf_embedding_id` from `db`); `digest.py` (line ~9 imports `current_votes`, `is_saved` from `db`); `shelf.py` (calls `db.shelf_embedding_id`, `list_shelf_items`, `upsert_shelf_item`, `delete_shelf_item` at lines ~122-257). `review.py` needs no change (raw SQL only).
- Test: move the tests of the moved functions from `tests/test_feedback_db.py` and `tests/test_shelf_db.py` to import from `library`; add `tests/test_library.py` only for the one new test below
- Docs: `docs/guide/data-model.md` (which module writes which tables), `docs/guide/shelf.md` (function locations), `docs/guide/digest.md` (if it names where votes/saves are read from)

**Interfaces:**
- Produces in `research_pipeline.library`, same signatures and behaviour as today in `db.py`: `record_vote`, `record_skip`, `clear_skip`, `current_votes`, `set_saved`, `is_saved`, `list_saved`, `shelf_embedding_id`, `upsert_shelf_item`, `delete_shelf_item`, `list_shelf_items`, `shelf_chunk_rows`, and the private helper `_delete_shelf_embeddings` (db.py ~558, used by `delete_shelf_item`; it must move with it).
- Stays in `db.py` (shared connection, schema and paper plumbing, and the pipeline's own writes): `open_db`, `_migrate`, `_now`, `get_paper`, `upsert_paper`, `set_status`, `_require_paper`, `UnknownPaperError`, `mark_in_digest`, `start_run`, `finish_run`, `save_fulltext`, `save_summary`, `record_failure`, and the other run/summary/provenance functions. Task 6 documents these as pipeline writes in a shared module; the boundary tests do not stop the app calling them, so Task 4's check (below) covers app commands.
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

- [ ] **Step 5: Docs.** Update `data-model.md`, `shelf.md` (and `digest.md` if needed) for the new function locations.

- [ ] **Step 6: Commit** (`refactor: move user-action functions from db.py to library.py`), code and docs together.

---

### Task 3: The `requests` table and `library_api.request`

**Files:**
- Modify: `src/research_pipeline/db.py` (schema: `requests` table; `SCHEMA_VERSION` bumped by one (current is 3, so 4 unless Phase 6 changed it); `_migrate` step `if version < <new>: pass  # requests table`; `insert_request`, `requests_by_status`)
- Create: `src/research_pipeline/ids.py` (new tiny shared module, standard library only, no heavy imports): `normalise_paper_id(s: str) -> str`, extracted from `profile_edit.normalise_arxiv_id` (profile_edit.py:49, which strips URLs, `arxiv:`, `.pdf` and `vN`, and raises `ValueError` for non-ids). `profile_edit.normalise_arxiv_id` becomes a re-export/thin wrapper so `seed_input`, `gold` and the profile code keep working; move the id regexes with it.
- Modify: `src/research_pipeline/cli.py` (`_resolve_paper`, ~line 1681: try `ids.normalise_paper_id` first, catching `ValueError`, then exact match, then the existing title search)
- Create: `src/research_pipeline/library_api.py` (only `request` in this task)
- Test: `tests/test_requests.py`, `tests/test_ids.py`, plus a `_resolve_paper` case in the existing vote tests
- Docs: `docs/guide/data-model.md` (the table and statuses, schema version), `docs/guide/glossary.md` (request list), `docs/guide/review.md` (`rpipe vote` now accepts `arXiv:`/`vN` spellings)

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
- Produces in `library_api.py`: `request(conn, kind: str, payload: dict) -> int`. Raises `ValueError` for a kind not in `REQUEST_KINDS` or a `summarize` payload without a string `paper_id`; normalises `paper_id` with `ids.normalise_paper_id` (strip URL prefix, `arXiv:`, `.pdf` and `vN`; a non-id raises `UnknownPaperError`, not a bare `ValueError`, so callers have one error), looks it up with `get_paper`, `UnknownPaperError` if absent; stores the normalised id. Title fragments are NOT accepted by `request` (the spec says a known paper id only); only `rpipe vote` and friends keep title search via `_resolve_paper`. The pure function lives in `ids.py` so `library_api` does not import `profile_edit` (which imports `backends`, `llm` and `vault` and would break the local-only test in Task 6).

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

def test_old_vault_gains_requests_table(tmp_path): ...  # db at the previous user_version opens, version becomes current, table exists

def test_normalise_paper_id():  # in tests/test_ids.py: "arXiv:2611.25005v2", "2611.25005V2" (decide: casefold the prefix only), URL and .pdf forms -> "2611.25005"; old-style "cs/0112017v1"; "hello" raises ValueError
def test_resolve_paper_accepts_versioned_id(...):  # rpipe vote 2611.25005v2 up works (it fails today)
```

Implement `ids.normalise_paper_id` first (a pure move of the logic out of `profile_edit.normalise_arxiv_id`; existing `profile_edit` tests must still pass unchanged through the re-export), then have `_resolve_paper` and `library_api.request` both call it.

- [ ] **Step 2: Run, verify fail.** `uv run pytest tests/test_requests.py -q`

- [ ] **Step 3: Implement** as specified.

- [ ] **Step 4: Run the full suite**, expected all pass.

- [ ] **Step 5: Docs.** Update `data-model.md`, `glossary.md`, `review.md` as listed in Files.

- [ ] **Step 6: Commit** (`feat: requests table and library_api.request`), code and docs together.

---

### Task 4: `library_api` reads and writes; route app commands through it

**Files:**
- Modify: `src/research_pipeline/library_api.py`
- Modify: `src/research_pipeline/cli.py` (`vote`, `save`, `unsave`, `feedback`, `review`, `shelf add|list|remove|sync` call `library_api`, then format for the terminal)
- Test: `tests/test_library_api.py`; existing CLI tests must pass unchanged
- Docs: new section "The app's door" in `docs/guide/architecture.md` (the file is created here; Task 6 completes it), `docs/guide/review.md`, `docs/guide/shelf.md` (commands now go through `library_api`), `docs/guide/browsing.md` (Datasette stays a stopgap and bypasses `library_api`), `docs/guide/digest.md` (if digest reading is described there)

**Interfaces:**
- Produces in `library_api.py` (all take `conn: sqlite3.Connection` first):

```python
class PaperView(BaseModel):
    id: str; title: str; abstract: str; authors: list[str]; url: str
    published: str | None; status: str; digest_date: str | None
    triage_score: float | None; triage_reasons: list[str]   # `_show_paper` (cli.py ~1784) prints "why: ..." from these
    summary: PaperSummary | None
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
def shelf_add(conn, my_work: Path, embedder: Embedder, source: str) -> ShelfItemView   # the added item (data object, not a bare string)
def shelf_remove(conn, my_work: Path, name: str) -> list[str]
def shelf_sync(conn, my_work: Path, embedder: Embedder) -> SyncResult

# review queues (wrap review.py: review_queue, later_queue, queue_progress)
class QueueProgress(BaseModel): handled: int; total: int
def review_queue(conn) -> list[PaperView]                  # unreviewed digest papers
def later_queue(conn) -> list[PaperView]                   # skipped papers
def queue_progress(conn, digest_date: str) -> QueueProgress
# votes for `rpipe feedback`
class VoteView(BaseModel): paper_id: str; title: str; vote: Literal[-1, 1]; voted_at: str
def list_votes(conn) -> list[VoteView]                     # current votes with titles (replaces the raw title SQL at cli.py ~1758)
def latest_digest_date(conn) -> str | None
```

`shelf_remove` returns `list[str]` (names removed); `list[str]` passes the Task 6 return-type test, a bare `str` would not. The `_refresh_hint` SQL (cli.py ~534) and `_resolve_paper` SQL (cli.py ~1687) are direct DB uses outside the planned boundary: `_resolve_paper` stays in `cli.py` (title search with an interactive picker is terminal behaviour) but calls `ids.normalise_paper_id`; note both in `architecture.md`.

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

- [ ] **Step 3: Implement**, delegating writes to `library` and shelf work to `shelf`. Route the listed CLI commands through `library_api`; terminal output text stays byte-identical (including the `why:` line in `review`, which is why `PaperView` carries `triage_reasons`).

- [ ] **Step 4: Run the full suite**, expected all pass (existing CLI tests prove the output did not change).

- [ ] **Step 5: Verify the exit criterion** ("the app can be built on `library_api` without touching pipeline code"). Add `tests/test_architecture.py::test_app_commands_use_only_library_api`: with `ast`, inspect the bodies of the app commands in `cli.py` (`vote`, `save`, `unsave`, `feedback`, `review`, `shelf` subcommands) and assert none references a `db` write function (`record_*`, `set_saved`, `upsert_*`, `mark_*`, `save_*`, `start_run`, ...) or a pipeline module, only `library_api` and formatting helpers. Fix any command that fails. Add tests for `review_queue`, `later_queue`, `queue_progress`, `list_votes`, and for `shelf_add` returning a `ShelfItemView`.

- [ ] **Step 6: Docs.** Write the "The app's door" section and update `review.md`, `shelf.md`, `browsing.md` (and `digest.md` if needed).

- [ ] **Step 7: Commit** (`feat: library_api, the app's one door; app commands use it`), code and docs together.

---

### Task 5: The pipeline works through requests

**Files:**
- Create: `src/research_pipeline/requests_queue.py`
- Modify: `src/research_pipeline/db.py` (`mark_request(conn, rid: int, status: str, *, error: str | None = None, result: dict | None = None, add_try: bool = False) -> None`, commits; `reset_running_requests(conn) -> int`)
- Modify: `src/research_pipeline/cli.py` (new `requests` Typer group: `run`, `list`; drain step in `_run_locked` after summarize, before digest)
- Modify: `src/research_pipeline/triage.py` and `cli.py`: shelf sync moves to the composition layer (see Step 3); `cli.health_line` (line ~259) prints only fixed count keys, so add `requests_done`/`requests_failed`/`requests_waiting` there (otherwise the counts never reach the digest)
- Test: `tests/test_requests_queue.py`, `tests/test_triage.py`, `tests/test_runs.py`
- Docs: `docs/guide/running.md` (`rpipe requests run|list`, the drain in the nightly run, retries), `docs/guide/triage.md` and `shelf.md` (sync now happens before triage, not inside it), `docs/guide/scheduling.md` (the nightly run now also drains requests), `docs/guide/digest.md` (the health line shows request counts), `docs/guide/summaries.md` (retries via requests)

**Interfaces:**
- Consumes: `requests_by_status`, `mark_request`, `reset_running_requests`, `MAX_SUMMARY_TRIES`, `run_summarize(conn, llm, fetch, num_ctx, paper_ids=[pid], on_progress=...)`.
- Produces: `drain_requests(conn, llm, fetch, num_ctx, on_progress=None) -> tuple[dict[str, int], list[str]]`, counts keys `requests_done`, `requests_failed`, `requests_waiting`. `run_triage(conn, embedder, profile, seed_texts, use_shelf: bool = False)` replaces the `my_work` parameter.

Drain algorithm (the parts the tests do not determine):
1. `reset_running_requests` (any `running` → `pending`).
2. For each `pending` row with `tries < MAX_SUMMARY_TRIES`, oldest first: mark `running` with `add_try=True`; call `run_summarize(paper_ids=[pid])` with an `on_progress` that captures the outcome.
3. Outcome `done` → `done`, `result={"summarized": True}`. Outcome `failed` → `failed` if `tries` now equals `MAX_SUMMARY_TRIES`, else back to `pending`, `error` = the detail. Outcome `stopped` (usage limit) → back to `pending` and **undo the try** (`tries - 1`), then stop draining; every remaining row stays `pending`. A timeout outcome (`failed` followed by run_summarize's stop) also stops the drain.
4. `requests_waiting` = pending rows left afterwards.

**Open issues to settle before building (not decided here):**
- `on_progress` carries no timeout signal (summarize.py ~159-216); a timeout shows up only as an appended error string, so a one-paper `run_summarize` call cannot report it through the callback. Phase 6 changes this signature; add an explicit outcome (e.g. `timeout`) or have the drain inspect `run_summarize`'s returned errors.
- Two retry counters exist for one paper: `papers.retry_count` (bumped by `record_failure` in summarize) and `requests.tries`. Pick ONE as the source of truth (suggest `papers.retry_count` and `MAX_SUMMARY_TRIES`, with `requests.tries` informational) so a request cannot be given up on after fewer real attempts, or retried after the paper was given up on.
- Skip the drain when the summarize stage already stopped on a usage limit: `_run_locked` (cli.py ~350-366) has no signal for it today, so the drain would burn a call right after "usage limit reached". Add one (a flag returned from the summarize stage) and a test.
- `run_summarize` reports `[1/1]` for each one-paper call; the drain should print its own `[i/N]`.

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
def test_drain_skipped_after_usage_limit_stop(...): summarize stage hit the limit -> no drain call made, rows stay pending
def test_health_line_shows_request_counts(...): requests_done / requests_waiting appear in the digest health line
```

Every existing `run_triage(...)` call in the tests changes (today it takes `my_work: Path | None`; the new `use_shelf: bool` replaces it).

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement.** Shelf sync (definite, not conditional): remove `sync_shelf` and the `my_work` parameter from `triage.py`, and change the shelf-vector reads in `rank.py` to import from `library` (via `PIPELINE_READS`); in `cli.run` (inside stage `"triage"`) and `cli.triage`, call `library_api.shelf_sync(ctx.conn, ctx.vault / "my_work", embedder)` then `run_triage(..., use_shelf=True)`. Drain in `_run_locked`: new stage `"requests"` after summarize, only when `llm` is not `None`; its counts merge into `counts` and its errors into `errors`; `health_line` is extended (Files above) so the digest actually shows the request counts.

- [ ] **Step 4: Run the full suite**, expected all pass.

- [ ] **Step 5: Docs.** Update `running.md`, `scheduling.md`, `digest.md`, `summaries.md`, `triage.md`, `shelf.md` as listed in Files; add "request list" and the retry rule to `glossary.md`.

- [ ] **Step 6: Commit** (`feat: pipeline drains app requests; shelf sync moves before triage`), code and docs together.

---

### Task 6: Boundary tests and the architecture page

**Files:**
- Create: `tests/test_architecture.py`
- Create: `src/research_pipeline/workbench/__init__.py` (docstring only: "Future app features. Import only shared modules, `library` and `library_api`.")
- Complete: `docs/guide/architecture.md`
- Modify: `docs/guide/README.md` (link), `docs/guide/glossary.md` (pipeline, app, composition layer, one door), `CLAUDE.md` (one line under Conventions: "Pipeline code and app code are separate groups; see docs/guide/architecture.md." Ask the owner before editing CLAUDE.md, as the handoff asks.)

**Interfaces:**
- Consumes: `library.PIPELINE_READS`; the classification above (mirrored in the spec's "Which code is which" table).

Module classification (every file and package in `src/research_pipeline`, verified against imports on 2026-11-22; re-verify after Phase 6):

```python
SHARED = {"models", "summary_models", "db", "vault", "embed", "llm", "backends", "defaults",
          "seeds", "profile_edit", "ids", "progress"}   # progress = the Phase 6 helper, not yet present
PIPELINE = {"collect", "sources", "triage", "rank", "fulltext", "summarize", "prompts", "digest",
            "evaluate", "gold", "schedule", "ollama_fit", "machine", "run_lock", "requests_queue",
            "registry", "seed_input", "onboarding"}
APP = {"library", "shelf", "review", "serve", "library_api", "workbench"}
COMPOSITION = {"cli"}
```

Notes on the non-obvious ones (all checked against their imports):
- `registry` imports `ollama_fit` and `sources.*`, so it is PIPELINE, not shared (the spec listed it as shared; fix the spec). If an app module ever needs the backend registry, split it into a shared lookup and a pipeline part first.
- `profile_edit` imports only `backends`, `llm`, `models`, `vault`: SHARED. `seeds` imports only `models`: SHARED. `defaults/` is data: SHARED.
- `seed_input` and `onboarding` import `sources.arxiv` (and `onboarding` imports `prompts`): PIPELINE-side setup flows, used by `cli` only. `gold` stays PIPELINE.
- `db` is SHARED but still holds the pipeline's own writes (`mark_in_digest`, `start_run`, `finish_run`, `save_fulltext`, `save_summary`, `record_failure`, `papers_to_summarize`, request functions). Because the boundary tests cannot stop an app module calling them, the app-commands test from Task 4 covers it; a later split of `db.py` is out of scope.
- `ids` must import nothing from the package (pure, standard library only).
- `progress` (Phase 6) is SHARED because both groups report progress; it must emit data events, not preformatted strings, to honour the spec's "progress as data" rule.

Tests (import inspection with `ast`, like `test_shelf_is_local_only`):

```python
def test_every_module_is_classified(): each module/package under src/research_pipeline is in exactly one of SHARED, PIPELINE, APP, COMPOSITION, so a new module must be placed deliberately
def test_shared_imports_only_shared(): every SHARED module imports only SHARED modules (so none imports `sources`, `ollama_fit`, `summarize`, and so on)
def test_pipeline_never_imports_app(): no PIPELINE module imports library_api, review, serve, workbench, shelf
def test_pipeline_reads_only_listed_library_functions(): every `from research_pipeline.library import X` in PIPELINE has X in PIPELINE_READS
def test_app_never_imports_pipeline_stages(): no APP module imports any PIPELINE module
def test_library_api_is_local_only(): library_api imports neither research_pipeline.llm nor research_pipeline.backends, directly or through the transitive import closure of its research_pipeline imports (comment: the first function that sends library content to a model must wire require_local and replace this test)
def test_library_api_returns_data_not_text(): for every public function, typing.get_type_hints(f)["return"] is not str (so `shelf_add` returns `ShelfItemView`)
def test_app_commands_use_only_library_api(): added in Task 4 Step 5
```

Architecture page shape (CLAUDE.md): What it does → How it works (the diagram from the spec, the three groups, the one door, the request list, the lock) → Why (one database vs two, with the rejected two-file design and why; data not text, for a future app) → How to check it (`uv run pytest tests/test_architecture.py`, `rpipe requests list`) → Where the code is.

- [ ] **Step 1: Write the tests.** Run them; fix any real boundary violation they find in the code (not the test).
- [ ] **Step 2: Write the docs.** Complete `architecture.md` (include the module classification table and the two known side doors: `db.py` pipeline writes and the Datasette canned write queries in `serve.py`); update `README.md`, `glossary.md`, and the lock/progress cross-links in `running.md`.
- [ ] **Step 3: Run the full suite**, expected all pass.
- [ ] **Step 4: Commit** (`test: enforce the pipeline/app boundary; docs: architecture page`).

---

## After the plan

- ~~Update the master plan (Claude Docs) with the two halves, an app section and a revised roadmap.~~ Done 2026-11-22.
- Update `docs/superpowers/handoff.md`: state, what changed, the next phase.
- Whole-branch review before the owner merges.
