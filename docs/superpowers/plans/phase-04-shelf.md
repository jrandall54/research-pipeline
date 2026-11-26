# Shelf Implementation Plan

> **Phase 4** · Status: merged (shelf slice; the rest moved to later phases) · Design: [phase-04-shelf-design.md](../specs/phase-04-shelf-design.md) · Roadmap: [ROADMAP.md](../ROADMAP.md)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A shelf of user materials (`my_work/`) that can be added and removed, whose nearest chunk to each new paper boosts its triage score.

**Architecture:** Files in `my_work/` are the source of truth. `shelf.py` chunks and embeds them into SQLite (`shelf_items`, `shelf_chunks`, plus rows in `embeddings`). `rank.py` gets a nearest-chunk term like the feedback term. `rpipe shelf add|remove|list|sync` manage it; triage syncs first.

**Tech Stack:** Python, sqlite3, numpy, typer, pytest with `FakeEmbedder` (tests/conftest.py).

**Spec:** `docs/superpowers/specs/phase-04-shelf-design.md`

## Global Constraints

- Local only: `shelf.py` must not import `research_pipeline.llm`, `requests`, `httpx`, `urllib` or any HTTP library; a test enforces it.
- Schema change via the `user_version` ladder in `db.py`: `SCHEMA_VERSION` 2 -> 3.
- Tests offline (`FakeEmbedder`); run with `uv run pytest -q`. Baseline must stay green.
- Docs in `docs/guide/` updated in the same commit as code (CLAUDE.md); new terms into `glossary.md`.
- No new user settings (weights are constants). Use `rpipe` in all messages and docs.
- `remove` never deletes a user file: it moves it to `my_work/.removed/`.
- Commit messages end with:
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01STEoRkRxwUDgaaxtZVuUvd`

## Review Focus

- Empty shelf: scores identical to before (no regression in existing triage tests).
- Removed item: no chunk rows and no `shelf:<name>#%` embedding rows remain.
- File edited between runs: old chunks and vectors replaced, not duplicated; unchanged file is not re-embedded (`embedder.calls` unchanged).
- File deleted by hand from `my_work/`: sync drops its item.
- Empty or whitespace-only file: indexed as zero chunks without crashing; non-`.md/.txt` files and dotfiles ignored.
- Two notes with the same first words get distinct names; `add` of an existing filename does not overwrite.

---

### Task 1: Schema v3 and shelf DB helpers

**Files:**
- Modify: `src/research_pipeline/db.py` (SCHEMA, `SCHEMA_VERSION = 3`, `_migrate` step `from_version < 3`)
- Test: `tests/test_shelf_db.py`
- Docs: `docs/guide/data-model.md`

**Interfaces:**
- Produces (all in `db.py`, each taking `conn: sqlite3.Connection` first):
  - `upsert_shelf_item(conn, name: str, content_hash: str, chunks: list[str]) -> None` replaces any existing chunks for `name` (and deletes its `shelf:<name>#%` embeddings), inserts the item and chunks.
  - `delete_shelf_item(conn, name: str) -> None` removes item, chunks (cascade) and its embeddings.
  - `list_shelf_items(conn) -> list[sqlite3.Row]` columns `name, content_hash, added_at, n_chunks`, ordered by name.
  - `shelf_chunk_rows(conn) -> list[sqlite3.Row]` columns `item_name, chunk_idx, text`.
  - `shelf_embedding_id(name: str, idx: int) -> str` returns `f"shelf:{name}#{idx}"`.

- [ ] **Step 1: Write failing tests** in `tests/test_shelf_db.py`: tables exist on a fresh db and `PRAGMA user_version == 3`; a db created at version 2 (set `user_version=2` and drop the shelf tables) migrates to 3 on `open_db`; `upsert_shelf_item` twice with different chunks leaves only the second set; `delete_shelf_item` removes chunks and embeddings rows with that prefix but leaves a paper's embedding row and another item's `shelf:other#0` row (including a name that is a prefix of another, e.g. `a` vs `ab`).
- [ ] **Step 2: Run** `uv run pytest tests/test_shelf_db.py -q`; expect FAIL.
- [ ] **Step 3: Implement** the tables from the spec ("Data (schema v3)") and the helpers. Match embedding rows with `item_id LIKE 'shelf:<name>#%'` after escaping `%`, `_` and `\` in the name (use `ESCAPE`), or by exact ids derived from `chunk_idx`; the prefix-name case above must pass.
- [ ] **Step 4: Update `data-model.md`** (new tables, the `shelf:<name>#<idx>` id convention in `embeddings`, version 3; `links` still unused). Run the full suite; expect green.
- [ ] **Step 5: Commit** `feat: shelf tables (schema v3)`.

### Task 2: shelf.py: chunking, sync, add, remove

**Files:**
- Create: `src/research_pipeline/shelf.py`
- Test: `tests/test_shelf.py`
- Docs: create `docs/guide/shelf.md` (page shape from CLAUDE.md; worked example; the why-decisions and rejected alternatives from the spec), add `chunk`, `shelf`, `nearest neighbour` to `glossary.md`, mention in `vault-and-config.md` (`my_work/` is now read; `.removed/`)

**Interfaces:**
- Consumes: Task 1 helpers; `Embedder` from `embed.py`.
- Produces in `shelf.py`:
  - constants `MIN_CHUNK_CHARS = 200`, `MAX_CHUNK_CHARS = 1500`
  - `chunk_text(name: str, text: str) -> list[str]` returns the chunk texts to embed, each prefixed `"{name}"` plus the nearest markdown heading when there is one (`"{name} / {heading}\n{body}"`); empty/whitespace text returns `[]`.
  - `SyncResult` dataclass: `added: list[str]`, `updated: list[str]`, `removed: list[str]`, `ignored: list[str]`.
  - `sync_shelf(conn, my_work: Path, embedder: Embedder) -> SyncResult`: item name = filename stem; indexes `.md`/`.txt` files directly in `my_work/` (not dotfiles, not `.removed/`); content hash (sha256 of bytes) unchanged and embeddings present for the embedder's model -> skip; otherwise (re)chunk, `upsert_shelf_item`, embed chunks and store in `embeddings` (`embedding_model = embedder.model_name`); items whose file is gone are deleted; other extensions go in `ignored`. Commits.
  - `add_to_shelf(conn, my_work: Path, embedder: Embedder, source: str) -> str`: if `source` is an existing `.md`/`.txt` file, copy it into `my_work/` under its name made unique (`name-2`, ...) and never overwriting; else write `source` as note text to `my_work/<slug>.md` (slug: lowercase first 6 words, alphanumerics and hyphens, unique-ified; fall back to `note` if empty). Runs `sync_shelf`, returns the item name. Empty text or a file with a bad extension raises `ValueError` with a message saying what to do.
  - `remove_from_shelf(conn, my_work: Path, name: str) -> None`: moves `my_work/<name>.md|.txt` to `my_work/.removed/` (unique-ified if already there), deletes the item. Unknown name raises `KeyError` carrying close matches (`difflib.get_close_matches`).

- [ ] **Step 1: Write failing tests** covering every Review Focus line above, plus: chunking (one-line note -> one chunk; headings split sections; two 50-char paragraphs merge; a 4000-char paragraph splits at sentence boundaries into chunks `<= MAX_CHUNK_CHARS`); `add` of text then `list_shelf_items` shows it; `remove` leaves the file in `.removed/` and none in `my_work/`; and `test_shelf_is_local_only` which parses `shelf.py` with `ast` and asserts none of the import names above appear.
- [ ] **Step 2: Run** `uv run pytest tests/test_shelf.py -q`; expect FAIL.
- [ ] **Step 3: Implement** as specified. Chunking approach: split on lines starting with `#`, then on blank lines, greedily merge short neighbours within the same section, split long ones on `. ` boundaries.
- [ ] **Step 4: Write `shelf.md` and the glossary/vault-and-config updates.** Run the full suite; expect green.
- [ ] **Step 5: Commit** `feat: shelf indexing (chunk, sync, add, remove)`.

### Task 3: Ranking term and triage wiring

**Files:**
- Modify: `src/research_pipeline/rank.py` (replace `MY_WORK_WEIGHT` stub), `src/research_pipeline/triage.py`
- Test: `tests/test_rank.py`, `tests/test_triage.py`
- Docs: `docs/guide/triage.md` (stub row and the `+ 0.0 (my_work similarity, Phase 4)` line become real; explain the size against the 0.045 spread)

**Interfaces:**
- Consumes: `shelf_chunk_rows`, `shelf_embedding_id` (Task 1), `sync_shelf` (Task 2).
- Produces in `rank.py`:
  - `SHELF_BOOST = 0.20`, `SHELF_REASON_FLOOR = 0.70` (replacing `MY_WORK_WEIGHT`)
  - `ShelfVectors` dataclass: `names: list[str]` (item name per chunk), `vecs: np.ndarray` (n, d), empty by default
  - `build_shelf_vectors(conn, embedder) -> ShelfVectors` (reads chunk rows, loads stored vectors for the embedder's model; chunks lacking a vector are skipped)
  - `score_papers(..., shelf: ShelfVectors | None = None)`: adds `SHELF_BOOST * max(0, max cosine to any chunk)`; if that raw cosine >= `SHELF_REASON_FLOOR` appends reason `near your shelf: <item name>`.
- `run_triage(conn, embedder, profile, seed_texts, my_work: Path | None = None)`: when `my_work` is given, calls `sync_shelf` first, then builds shelf vectors and passes them to `score_papers`. `cli.py` passes `ctx` vault's `my_work` path in both `run` (line ~295) and `triage` (line ~398); locate the path through the existing vault context object.

- [ ] **Step 1: Write failing tests:** with `shelf=None` or empty, scores equal the existing behaviour (an existing test stays unchanged); a paper sharing words with a chunk outranks an otherwise equal paper and carries reason `near your shelf: <name>`; nearest-chunk only (adding unrelated chunks does not change a paper's boost); negative cosine adds nothing; `run_triage` with a note dropped in `my_work/` syncs and applies the boost end to end, and a second run does not re-embed the note.
- [ ] **Step 2: Run** the two test files; expect the new tests FAIL.
- [ ] **Step 3: Implement** per Interfaces, with a comment justifying `SHELF_BOOST` in the style of the surrounding constants.
- [ ] **Step 4: Update `triage.md`.** Run the full suite; expect green.
- [ ] **Step 5: Commit** `feat: shelf similarity boost in triage`.

### Task 4: `rpipe shelf` commands

**Files:**
- Modify: `src/research_pipeline/cli.py` (new `shelf_app` Typer group registered as `shelf`, same pattern as `gold_app`)
- Test: `tests/test_cli_shelf.py` (follow `tests/test_cli_review.py` for how the CLI and the embedder are faked)
- Docs: `docs/guide/shelf.md` (command section with real example output), `docs/guide/README.md` (pipeline overview and page index), `docs/superpowers/archive/handoff-log-to-2026-11-25.md` (new state; judge is next slice; `TODO(phase4)` stub gone)

**Interfaces:**
- Consumes: `add_to_shelf`, `remove_from_shelf`, `sync_shelf`, `list_shelf_items`.
- Produces: `rpipe shelf add <text-or-file>`, `remove <name>`, `list`, `sync`. Each prints plain-language results (name used, counts); errors from `ValueError`/`KeyError` become a message and exit code 1, with the close matches listed for an unknown name. `list` on an empty shelf says how to add something.

- [ ] **Step 1: Write failing tests:** `add` with text prints the new item name and creates `my_work/<slug>.md`; `add` with a file path copies it; `list` shows name, chunk count, date; `remove` then `list` no longer shows it and the file is under `.removed/`; unknown name exits 1 and suggests a close name; `sync` reports added/updated/removed counts after a file is dropped in by hand.
- [ ] **Step 2: Run** `uv run pytest tests/test_cli_shelf.py -q`; expect FAIL.
- [ ] **Step 3: Implement** the four commands.
- [ ] **Step 4: Finish docs** (command walk-through, README, handoff). Run the full suite; expect green.
- [ ] **Step 5: Commit** `feat: rpipe shelf commands`.

---

## Self-review

- Spec coverage: commands (T4), data (T1), chunking (T2), ranking (T3), wiring (T3), docs (each task), local-only test (T2), `.removed/` (T2/T4), no new settings (Constraints). Judge and `links` are non-goals.
- Names consistent across tasks: `shelf_embedding_id`, `sync_shelf`, `ShelfVectors`, `build_shelf_vectors`.
