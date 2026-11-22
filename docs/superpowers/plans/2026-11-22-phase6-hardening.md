# Phase 6 Pipeline Hardening (Parts A and C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every waiting step in `rpipe` shows visible progress (Part A), and the audit's small gaps are closed (Part C).

**Architecture:** A shared `progress.py` defines progress *events* (data objects) and a `Reporter` interface; pipeline stages emit events and never print. The CLI layer (`progress_render.py`) turns events into a live `rich` display on a terminal or plain lines (plus a 30 s heartbeat) when stderr is not a terminal. Part C adds three commands, a category check against a packaged arXiv list, `rpipe show`, and a boundary test that keeps `my_work/` text out of model code.

**Tech Stack:** Python 3.12, typer (brings `rich`), sqlite3, pytest (offline: fake embedder, fake LLM), ruff.

**Spec:** `docs/superpowers/specs/2026-11-22-phase6-pipeline-hardening-design.md` (approved 2026-11-22). Part B (long papers) is planned separately after the owner's model verdict; Part D is a decision recorded in the master plan, no code.

## Global Constraints

- Tests run offline: `uv run pytest -q` (905 passing at start) and `uv run ruff check .` must both pass after every task.
- CLAUDE.md: every task updates the matching `docs/guide/` page in the SAME commit (What → How → Why → Run/check → Where the code is); new terms go in `docs/guide/glossary.md`; CLI name `rpipe`; no workflow may require hand-editing YAML.
- Progress always goes to **stderr**; stdout output of every command is unchanged.
- Stages take a keyword-only `reporter: Reporter | None = None` (None = silent). Signatures change at once; no compatibility shim.
- The per-paper line format `[2/3] 2611.25005 done (summary + critique, 41s)` stays exactly as today (also `stopped: usage limit reached`, `failed: <detail>`).
- Heartbeat interval 30 s; `set-max-results` range 50–5000; default embedding model `BAAI/bge-small-en-v1.5`.
- No new runtime dependency (`rich` comes with typer).
- Commit message trailer on every commit:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01STEoRinVAQCUHevUpZJie5
  ```

## Review Focus

1. **Output piped or under systemd** (stderr not a TTY): no ANSI codes, no carriage-return redraws, one line per event; a 10-minute model call prints a heartbeat every 30 s and nothing else. Test in Task 2.
2. **A stage raises mid-way** (network error during collect, model timeout during summarize): the live display stops cleanly, the error message is still printed, and the `done` event carries `outcome="failed"`. Test in Tasks 1 and 2.
3. **Ctrl-C / SIGTERM during a live display**: the heartbeat thread stops and the terminal is restored (no hung process, no leftover cursor state). Test in Task 2 (thread stops on `close()` even after an exception).
4. **Zero work** (no new papers, empty shelf, no seeds): no bar with total 0, no division by zero; a single `done` line. Tests in Tasks 4 and 5.
5. **Stored config/profile with now-invalid category codes** (e.g. `cs.ML` saved earlier): `rpipe run` still works and warns; only the set command refuses. Test in Task 9.

---

### Task 1: Progress events core (`progress.py`)

**Files:**
- Create: `src/research_pipeline/progress.py`
- Test: `tests/test_progress.py`

**Interfaces:**
- Produces:
  - `EventKind = Literal["start", "step", "note", "done"]`
  - `@dataclass(frozen=True) class ProgressEvent: stage: str; kind: EventKind; label: str = ""; current: int | None = None; total: int | None = None; elapsed: float = 0.0; outcome: str = ""; detail: str = ""`
  - `class Reporter(Protocol): def emit(self, event: ProgressEvent) -> None`
  - `class ListReporter` (collects `events: list[ProgressEvent]`)
  - `def emit(reporter: Reporter | None, event: ProgressEvent) -> None` (no-op when None)
  - `@contextmanager def stage(reporter: Reporter | None, name: str, total: int | None = None, label: str = "", clock: Callable[[], float] = time.monotonic) -> Iterator[StageHandle]`
  - `class StageHandle: def step(self, label: str, *, current: int | None = None, outcome: str = "", detail: str = "") -> None; def note(self, label: str) -> None; elapsed (property)`
  - `stage()` emits `start` on entry and `done` on exit with `elapsed`; on exception it emits `done` with `outcome="failed"`, `detail=str(exc)` and re-raises. `step` events carry the stage's `total` and their own elapsed-since-stage-start.

- [ ] **Step 1: Write the failing tests**

```python
def test_stage_emits_start_steps_and_done():
    rep = ListReporter(); t = iter([0.0, 1.0, 2.5, 4.0])
    with stage(rep, "collect", total=2, clock=lambda: next(t)) as s:
        s.step("page 1", current=1); s.step("page 2", current=2)
    kinds = [e.kind for e in rep.events]
    assert kinds == ["start", "step", "step", "done"]
    assert rep.events[1].total == 2 and rep.events[1].current == 1
    assert rep.events[-1].elapsed == 4.0 and rep.events[-1].outcome == ""

def test_stage_failure_emits_failed_done_and_reraises():
    rep = ListReporter()
    with pytest.raises(ValueError):
        with stage(rep, "summarize"):
            raise ValueError("boom")
    assert rep.events[-1].kind == "done" and rep.events[-1].outcome == "failed"
    assert rep.events[-1].detail == "boom"

def test_none_reporter_is_silent():
    with stage(None, "x") as s:
        s.step("a"); s.note("b")   # no error, nothing to assert beyond not raising
```

- [ ] **Step 2: Run** `uv run pytest tests/test_progress.py -q` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement `progress.py`** with the interfaces above. Standard library only (this module is *shared* in the Phase 7 split; it must import nothing from the package).
- [ ] **Step 4: Run** `uv run pytest tests/test_progress.py -q && uv run ruff check .` — Expected: PASS.
- [ ] **Step 5: Docs:** add `progress.py` to the module map in `docs/guide/README.md` ("Where the code is").
- [ ] **Step 6: Commit** `feat: progress events core (stages emit data, never print)`.

### Task 2: Renderers, heartbeat and the CLI hook

**Files:**
- Create: `src/research_pipeline/progress_render.py`
- Modify: `src/research_pipeline/cli.py` (`_progress` at ~94 becomes a thin wrapper over the reporter; add `_reporter()` factory)
- Modify: `docs/guide/running.md` (new "Progress" section), `docs/guide/glossary.md` (TTY, stderr, heartbeat), `CLAUDE.md` (one convention line — see Step 7)
- Test: `tests/test_progress_render.py`

**Interfaces:**
- Consumes: Task 1 `ProgressEvent`, `Reporter`.
- Produces:
  - `class LineRenderer(Reporter): __init__(self, out: TextIO, heartbeat: float = 30.0, clock: Callable[[], float] = time.monotonic, start_thread: bool = True); emit(event); close()`
  - `class LiveRenderer(Reporter): __init__(self, out: TextIO); emit(event); close()` (rich `Live`/`Progress` on `out`; spinner + elapsed always; bar only when `total` is known and > 0)
  - `def make_reporter(out: TextIO | None = None) -> LineRenderer | LiveRenderer` — `LiveRenderer` when `out.isatty()`, else `LineRenderer`; default `out=sys.stderr`.
  - `def format_line(event: ProgressEvent) -> str | None` — the plain-text form (None = print nothing). Per-paper summarize steps (`stage == "summarize"`, kind `step`, `outcome` in `done|stopped|failed`) render exactly as today's `_paper_progress` lines.
  - `cli._reporter()` context manager: yields `make_reporter()` and always calls `close()`.
- Heartbeat: `LineRenderer` keeps the label of the latest open stage/step; a daemon thread prints `still waiting: <label> (1m30s)` every `heartbeat` seconds while no other event arrived. `close()` stops the thread and is idempotent. For tests, `start_thread=False` plus a public `tick()` that performs one heartbeat check against `clock`.

- [ ] **Step 1: Write the failing tests**

```python
def test_line_renderer_plain_lines_no_ansi():
    buf = io.StringIO(); r = LineRenderer(buf, start_thread=False)
    r.emit(ProgressEvent("collect", "start", label="arXiv"))
    r.emit(ProgressEvent("collect", "done", elapsed=3.2))
    r.close()
    out = buf.getvalue()
    assert "\x1b[" not in out and "\r" not in out
    assert out.count("\n") == 2

def test_summarize_paper_line_unchanged():
    e = ProgressEvent("summarize", "step", label="2611.25005", current=2, total=3, elapsed=41.2, outcome="done")
    assert format_line(e) == "[2/3] 2611.25005 done (summary + critique, 41s)"
    assert format_line(replace(e, outcome="stopped")) == "[2/3] 2611.25005 stopped: usage limit reached"
    assert format_line(replace(e, outcome="failed", detail="timeout")) == "[2/3] 2611.25005 failed: timeout"

def test_heartbeat_after_30s_of_silence():
    now = [0.0]; buf = io.StringIO()
    r = LineRenderer(buf, heartbeat=30.0, clock=lambda: now[0], start_thread=False)
    r.emit(ProgressEvent("summarize", "note", label="writing the critique for 2611.25005"))
    now[0] = 29.0; r.tick(); assert "still waiting" not in buf.getvalue()
    now[0] = 90.0; r.tick()
    assert "still waiting: writing the critique for 2611.25005 (1m30s)" in buf.getvalue()

def test_close_stops_thread_even_after_error():
    r = LineRenderer(io.StringIO(), heartbeat=0.01)
    try:
        raise KeyboardInterrupt
    except KeyboardInterrupt:
        r.close()
    assert not r._thread.is_alive()

def test_make_reporter_picks_by_tty():
    class Tty(io.StringIO):
        def isatty(self): return True
    assert isinstance(make_reporter(io.StringIO()), LineRenderer)
    assert isinstance(make_reporter(Tty()), LiveRenderer)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_progress_render.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** `progress_render.py` and `cli._reporter()`. Plain-line wording: `start` → `<stage>: <label>…`, `note` → `  <label>`, `step` with total → `  [i/N] <label>` (+ ` <outcome>` / `: <detail>`), `done` → `<stage> done (<elapsed>)` or `<stage> failed: <detail>`. Elapsed shown as `41s` under a minute, else `1m30s`.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check .` — Expected: PASS.
- [ ] **Step 5: Docs:** `running.md` "Progress" section: what you see on a terminal vs in `journalctl --user -u research-pipeline`, why events are data (the future app shows the same events), the heartbeat, and where the code is. Glossary: TTY, stderr, heartbeat.
- [ ] **Step 6: Commit** `feat: progress renderers (live on a terminal, plain lines and heartbeat otherwise)`.
- [ ] **Step 7: CLAUDE.md convention (ask the owner first):** propose adding under Conventions: "Every step that waits reports through `progress.py` (stages emit events; the CLI renders them). See docs/guide/running.md, Progress." Commit separately as `docs: progress convention in CLAUDE.md` only after the owner says yes.

### Task 3: Summarize reports per paper and per step

**Files:**
- Modify: `src/research_pipeline/summarize.py:93-216` (`run_summarize`, `summarize_paper`)
- Modify: `src/research_pipeline/cli.py` (`_paper_progress` ~99 removed; callers ~392 `rpipe run`, ~555 `rpipe summarize` pass the reporter)
- Modify: `docs/guide/summaries.md`, `docs/guide/running.md`
- Test: `tests/test_summarize.py`, `tests/test_cli_summarize*.py` (whichever hold the progress tests today)

**Interfaces:**
- Consumes: Task 1 `stage`, `Reporter`; Task 2 `cli._reporter()`.
- Produces: `run_summarize(conn, llm, fetch, num_ctx, today=None, backlog_limit=None, paper_ids=None, *, reporter: Reporter | None = None, refetch=False)` — `on_progress` removed. Inside one `stage(reporter, "summarize", total=len(queue))`: before each paper a `note` "fetching <id>" (only when it must fetch), "writing the summary for <id>", "writing the critique for <id>", "retrying citations for <id>" (when the citation retry runs); after each paper a `step` with `label=<id>`, `current=i`, `outcome` `done|stopped|failed`, `detail` as today.

- [ ] **Step 1: Update/write failing tests:** convert existing `on_progress` tests to `ListReporter`; add

```python
def test_summarize_reports_steps_in_order(fake_llm, conn_with_one_triaged_paper):
    rep = ListReporter()
    run_summarize(conn, fake_llm, fetch=fake_fetch, num_ctx=32768, reporter=rep)
    labels = [e.label for e in rep.events if e.kind == "note"]
    assert labels[:2] == ["writing the summary for 2611.00001", "writing the critique for 2611.00001"]
    last = [e for e in rep.events if e.kind == "step"][-1]
    assert (last.current, last.total, last.outcome) == (1, 1, "done")
```
and a CLI test asserting `rpipe summarize` stderr still contains `[1/1] 2611.00001 done (summary + critique,`.
- [ ] **Step 2: Run** the two test files — Expected: FAIL.
- [ ] **Step 3: Implement** the reporter parameter and the notes; delete `_paper_progress`.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check .` — Expected: PASS.
- [ ] **Step 5: Docs:** `summaries.md` "How it works" mentions the per-step lines; `running.md` example output updated.
- [ ] **Step 6: Commit** `feat: summarize reports fetch, summary, critique and per-paper results`.

### Task 4: Collect reports per source and per arXiv page

**Files:**
- Modify: `src/research_pipeline/collect.py:9` (`run_collect`), `src/research_pipeline/sources/arxiv.py:62-147` (`ArxivSource`)
- Modify: `src/research_pipeline/cli.py` (`rpipe collect`, `rpipe run` pass the reporter)
- Modify: `docs/guide/sources.md`
- Test: `tests/test_collect.py`, `tests/test_arxiv_source.py` (existing names; add tests there)

**Interfaces:**
- Produces: `run_collect(conn, sources, run_id=None, *, reporter=None)`. One `stage(reporter, "collect")`; per source a `note` "<name>: fetching" and, after it, a `step` with `label=<name>`, `outcome="ok"` and `detail="<n> papers"` or `outcome="failed"` and the error. Sources stay plug-ins: the `Source` protocol is NOT changed. Like the existing `set_known_checker`, `run_collect` calls `source.set_reporter(handle)` only if the source has that method; `ArxivSource.set_reporter(handle)` makes `fetch()` emit a `note` per page: `arXiv page <k> (<collected> papers so far)`.

- [ ] **Step 1: Failing tests:**

```python
def test_collect_reports_each_source(conn):
    rep = ListReporter()
    run_collect(conn, [FakeSource("fake", n=3), BrokenSource("bad")], reporter=rep)
    steps = [(e.label, e.outcome) for e in rep.events if e.kind == "step"]
    assert steps == [("fake", "ok"), ("bad", "failed")]

def test_collect_zero_papers_single_done(conn):
    rep = ListReporter()
    run_collect(conn, [FakeSource("fake", n=0)], reporter=rep)
    assert [e.kind for e in rep.events].count("done") == 1

def test_arxiv_page_notes(respx_mock_two_pages):
    rep = ListReporter(); src = ArxivSource(["cs.CR"], max_results=200, client=...)
    with stage(rep, "collect") as s:
        src.set_reporter(s); src.fetch()
    assert [e.label for e in rep.events if e.kind == "note"][:2] == ["arXiv page 1 (0 papers so far)", "arXiv page 2 (100 papers so far)"]
```
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check .` — Expected: PASS.
- [ ] **Step 5: Docs:** `sources.md` "How to run / check" shows the new lines; note that third-party sources may add `set_reporter` (also one line in `plugins.md`).
- [ ] **Step 6: Commit** `feat: collect reports each source and each arXiv page`.

### Task 5: Embedding model load, triage, shelf and seed lookups

**Files:**
- Modify: `src/research_pipeline/cli.py` (`_load_embedder` ~246, `_seed_texts` ~277, `rpipe run` / `triage` / `shelf add|sync`)
- Modify: `src/research_pipeline/triage.py:25` (`run_triage`), `src/research_pipeline/shelf.py:133` (`sync_shelf`), `src/research_pipeline/seeds.py:58` (`resolve_seed_texts`)
- Modify: `docs/guide/triage.md`, `docs/guide/shelf.md`
- Test: `tests/test_triage.py`, `tests/test_shelf.py`, `tests/test_seeds.py`

**Interfaces:**
- Produces:
  - `run_triage(conn, embedder, profile, seed_texts, my_work=None, *, reporter=None)`: `stage(reporter, "triage", total=<papers to embed>)` with a `note` "embedding <n> papers" before the embed call and `step`s only if embedding is batched; with 0 papers: start/done only.
  - `sync_shelf(conn, my_work, embedder, *, reporter=None)`: `stage(reporter, "shelf", total=<files to (re)embed>)`, one `step` per file.
  - `resolve_seed_texts(conn, cache_path, seed_ids, fetcher, *, reporter=None)`: one `note` per seed actually fetched from arXiv ("looking up seed <id>"); cached seeds are silent.
  - CLI: loading the embedder runs inside `stage(reporter, "embedding model", label=<model name>)`; the first-time download is covered by the heartbeat.

- [ ] **Step 1: Failing tests:** triage with 0 new papers → events `["start", "done"]`; triage with 3 → a note `"embedding 3 papers"`; shelf with 2 changed files → 2 steps with labels = file names; seeds with 1 cached + 1 fetched → exactly one note `"looking up seed 2401.00001"`.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check .` — Expected: PASS.
- [ ] **Step 5: Docs:** `triage.md` and `shelf.md` show the new lines; explain that the first run downloads the embedding model (hundreds of MB) and the heartbeat covers it.
- [ ] **Step 6: Commit** `feat: progress for the embedding model, triage, shelf sync and seed lookups`.

### Task 6: Remaining waiting commands (eval, model fit/check/pull, onboarding)

**Files:**
- Modify: `src/research_pipeline/evaluate.py:144` (`run_eval`), `src/research_pipeline/onboarding.py:82` (`resolve_seeds`), `src/research_pipeline/llm.py:140` (`pull_model`)
- Modify: `src/research_pipeline/cli.py` (`eval run` ~1217, `model fit` ~1427, `model check` ~1447, `model setup` ~1663, `profile suggest` ~868, `init`; every `log=_progress` passed to `ollama_fit` becomes a reporter-backed note)
- Modify: `docs/guide/evaluation.md`, `docs/guide/llm.md`, `docs/guide/setup.md`, `docs/guide/profile.md`
- Test: `tests/test_evaluate.py`, `tests/test_onboarding.py`, `tests/test_llm.py`, CLI tests for `model fit`

**Interfaces:**
- Produces: `run_eval(vault, llm, fetch, num_ctx, today, abstract_for=None, *, reporter=None)` — `stage("eval", total=<gold papers>)`, one `step` per paper (`outcome` `ok|failed`). `resolve_seeds(..., *, reporter=None)` — one `note` per title lookup. `pull_model(url, tag, progress)` keeps its signature; the CLI passes a callable that emits `step` events (`label=tag`, `detail="43%"`) so the live bar moves. `ollama_fit`'s `log: Callable[[str], None]` parameters are unchanged; the CLI passes `handle.note`.

- [ ] **Step 1: Failing tests:** `run_eval` over 2 gold papers (one failing) → steps `[("p1","ok"),("p2","failed")]`; `resolve_seeds` with 2 titles → 2 notes; CLI `rpipe model fit` (fake fit) stderr contains `trying fake-model at 8192 tokens` exactly once and a final `model fit done (`.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check .` — Expected: PASS.
- [ ] **Step 5: Docs:** the four guide pages show the new output where they show command output.
- [ ] **Step 6: Commit** `feat: progress for eval, model fit, check, pull and onboarding lookups`.

### Task 7: Commands for the three missing settings

**Files:**
- Modify: `src/research_pipeline/profile_edit.py` (add setters next to `set_top_n` ~229), `src/research_pipeline/cli.py` (`sources_app` ~2059, `model_app` ~1296), `src/research_pipeline/defaults/config.yaml:22` (remove `rss_feeds`)
- Modify: `docs/guide/vault-and-config.md`, `docs/guide/sources.md`, `docs/guide/triage.md`
- Test: `tests/test_profile_edit.py`, `tests/test_cli_sources.py`, `tests/test_cli_model.py`, `tests/test_vault.py`

**Interfaces:**
- Produces: `profile_edit.set_arxiv_max_results(vault: Path, n: int) -> None` (ValueError outside 50–5000); `profile_edit.set_embedding_model(vault: Path, name: str) -> None` (ValueError on empty/whitespace); CLI `rpipe sources set-max-results N` and `rpipe model set-embedding NAME`. `Config.rss_feeds` stays in `models.py` (old files load) but is gone from the default template and the docs' settings table says "reserved: returns with the RSS source".

- [ ] **Step 1: Failing tests:** setter round-trips (comments in config.yaml preserved, like existing setter tests); `set-max-results 10` exits 1 with "between 50 and 5000"; `set-embedding all-MiniLM-L6-v2` prints that the next run downloads the model and re-embeds everything and how to go back (`rpipe model set-embedding BAAI/bge-small-en-v1.5`); a config file still containing `rss_feeds: []` loads; a fresh `rpipe init` config has no `rss_feeds` line.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check .` — Expected: PASS.
- [ ] **Step 5: Docs:** the settings table in `vault-and-config.md` names the command for every key; `sources.md` and `triage.md` document the new commands.
- [ ] **Step 6: Commit** `feat: rpipe sources set-max-results and rpipe model set-embedding; drop rss_feeds from the template`.

### Task 8: `rpipe show`

**Files:**
- Modify: `src/research_pipeline/cli.py` (new `@app.command("show")` near `review` ~1831; reuses `_resolve_paper` ~1681 and `_show_paper` ~1815)
- Modify: `docs/guide/review.md` (or `digest.md` if it fits better), `README.md` daily-use line
- Test: `tests/test_cli_show.py`

**Interfaces:**
- Produces: `rpipe show [ID_OR_TITLE ...] [--recent N]`. With ids/titles: resolve each like `rpipe vote` (exact id, then title substring, numbered pick when several match; unknown → exit 1 "No paper in your vault matches …"), print via `_show_paper`. `--recent N` (option `int | None`, default `None`, must be ≥ 1; `rpipe show --recent 1` is the common use): papers whose `digest_date` is among the latest N distinct digest dates, newest date first, in digest order. No arguments and no `--recent` → exit 1 with a usage hint. No voting, no writes.

- [ ] **Step 1: Failing tests:** show by id prints title, `id:`, `why:` and the summary text; show a paper without a summary prints its abstract; `--recent 1` with two digest dates shows only the newer date's papers; no args → exit code 1; the command writes nothing (row counts of `votes`, `saves`, `skips` unchanged).
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check .` — Expected: PASS.
- [ ] **Step 5: Docs:** guide page section with an example; README daily-use line.
- [ ] **Step 6: Commit** `feat: rpipe show prints a paper's summary and critique (by id, title or --recent)`.

### Task 9: Category codes checked against arXiv's list

**Files:**
- Create: `src/research_pipeline/defaults/arxiv_categories.txt` (one code per line, `#` comments; header comment names the source URL `https://arxiv.org/category_taxonomy` and the date copied), `src/research_pipeline/categories.py`
- Modify: `src/research_pipeline/profile_edit.py:253` (`set_categories`), `src/research_pipeline/cli.py` (`profile show` ~624, health line in `rpipe run`)
- Modify: `docs/guide/profile.md`
- Test: `tests/test_categories.py`, `tests/test_profile_edit.py`

**Interfaces:**
- Produces: `categories.known_categories() -> frozenset[str]` (read once from the packaged file via `importlib.resources`); `categories.unknown(codes: list[str]) -> list[str]` (`"all"` is always known); `categories.closest(code: str) -> str | None` (`difflib.get_close_matches`, cutoff 0.6). `set_categories` raises ValueError naming each unknown code and its suggestion: `cs.ML is not an arXiv category (did you mean cs.LG?)`. `models.Config` validation is unchanged (format only), so stored invalid codes still load; `rpipe profile show` and the `rpipe run` health line add `unknown categories: cs.ML`.
- The list must include every current arXiv code across archives, including those without a dot (`hep-th`, `astro-ph`, `cond-mat`) and with hyphens (`cond-mat.stat-mech`). Copy it from the taxonomy page when implementing; record the date in the file header.

- [ ] **Step 1: Failing tests:**

```python
def test_known_includes_cs_and_old_style():
    k = known_categories()
    assert {"cs.LG", "cs.CR", "stat.ML", "math.PR", "hep-th", "cond-mat.stat-mech"} <= k

def test_cs_ML_unknown_with_suggestion():
    assert unknown(["cs.ML", "cs.LG", "all"]) == ["cs.ML"]
    assert closest("cs.ML") == "cs.LG"

def test_set_categories_rejects_unknown(tmp_vault):
    with pytest.raises(ValueError, match=r"cs\.ML is not an arXiv category \(did you mean cs\.LG\?\)"):
        set_categories(tmp_vault, ["cs.ML"])

def test_stored_unknown_category_still_runs(tmp_vault_with_cs_ML, cli_runner):
    result = cli_runner.invoke(app, ["run", "--vault", str(tmp_vault_with_cs_ML)])  # with the usual offline pipeline stub
    assert result.exit_code == 0 and "unknown categories: cs.ML" in result.output
```
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check .` — Expected: PASS.
- [ ] **Step 5: Docs:** `profile.md` "Choosing categories": codes are checked against a packaged copy of arXiv's list, how to read the "did you mean", and that the list is refreshed by hand.
- [ ] **Step 6: Commit** `feat: check category codes against arXiv's list, with a did-you-mean`.

### Task 10: Keep `my_work/` text out of model code (boundary test)

**Files:**
- Create: `tests/test_boundaries.py`
- Modify: `docs/guide/shelf.md` (privacy section)

**Interfaces:**
- Consumes: nothing new.
- Produces: `test_model_modules_do_not_import_shelf` — parses (with `ast`) each of `prompts.py`, `llm.py`, `summarize.py`, `onboarding.py`, `evaluate.py` and every file in `backends/`, and fails if any imports `research_pipeline.shelf` (any import form, including `from research_pipeline import shelf`). Ranking (`rank.py`, `triage.py`) is deliberately not checked: it reads shelf *embeddings*, which are numbers, not text sent to a model.

- [ ] **Step 1: Write the test** (and a second test proving the checker catches a planted `from research_pipeline import shelf` in a temporary file, so the test cannot pass vacuously).
- [ ] **Step 2: Run** `uv run pytest tests/test_boundaries.py -q` — Expected: PASS on the current code (the guard documents and locks in today's state; the planted-file test proves it can fail).
- [ ] **Step 3: Docs:** `shelf.md` privacy section: nothing sends shelf text to a model; this test enforces it; the first feature that needs shelf text in a prompt must call `llm.require_local` and update the test.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check .` — Expected: PASS.
- [ ] **Step 5: Commit** `test: boundary test keeps my_work text out of prompt and model code`.

### Task 11: Close-out

**Files:**
- Modify: `docs/superpowers/handoff.md`, `docs/guide/README.md` (reading order if pages were added)

- [ ] **Step 1:** Run the whole suite and lint; run `rpipe run --vault <a copy of a test vault>` piped through `cat` and confirm plain lines with no escape codes (paste the output into the review notes).
- [ ] **Step 2:** Update `handoff.md`: Parts A and C done; Part B waits on the owner's model verdict (comparison file `~/research-vault/model-comparison-2026-11-22.md`); Part D recorded in the master plan; the Phase 7 plan now needs its refresh (progress module classified *shared*; `on_progress` is gone).
- [ ] **Step 3: Commit** `docs: handoff after Phase 6 parts A and C`.
