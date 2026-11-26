# Phase 1 — Abstract Digest Implementation Plan

> **Phase 1** · Status: merged · Design: the master plan (archived, see ../ROADMAP.md) · Roadmap: [ROADMAP.md](../ROADMAP.md)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A nightly-runnable CLI that collects arXiv + Hugging Face Daily papers, ranks them against `profile.yaml` with local embeddings, and writes a Markdown digest of titles, abstracts and match reasons. No PDFs, no LLM summaries.

**Architecture:** Python package `research_pipeline` (src layout, uv-managed). Stages (collect → triage → digest) communicate only through a SQLite `vault.sqlite` with a `status` column, so each stage is resumable. Network sources and the embedding model sit behind small interfaces so tests run offline with fixtures and a fake embedder.

**Tech Stack:** Python 3.12, uv, pydantic v2, PyYAML, typer, httpx, feedparser (arXiv Atom), sentence-transformers (real embedder), numpy, pytest, respx (HTTP mocking), stdlib sqlite3 (FTS5).

**Spec:** Research Pipeline — Master Implementation Plan (https://claude.ai/artifact/Ajw2s52HwWuZfrYZXsZjfa), "Phase 1 — Abstract digest", Stage 1–2 specs, "Repo, vault and data model".

## Global Constraints

- Engine separate from data: repo holds code and defaults; vault (default `~/research-vault/`) holds everything personal. Nothing personal in the repo.
- Paper status values: `new → triaged → fetched → summarized → in_digest`, plus `skipped` and `failed`. Phase 1 uses only `new`, `triaged`, `skipped`, `in_digest`.
- Canonical id: arXiv id, DOI, or URL hash.
- arXiv categories default `cs.LG, cs.CL, cs.CR, cs.OS, cs.SE` (configurable); **at least 3 seconds between arXiv API calls**.
- Skip a paper whose canonical id is already present, unless its version number is higher.
- A new version sets a *kept* paper back to `triaged`.
- Triage score = similarity to profile seeds/topics + similarity to `my_work/` (Phase 4, stubbed as 0) + boosts (HF upvotes, followed authors, keyword hits) − penalties (downvoted topics; Phase 3, stubbed as 0). Keep top N (default 15); mark the rest `skipped` but keep them searchable.
- Each digest item shows *why it ranked where it did*.
- Core tables: `papers, summaries, votes, embeddings, links, runs, papers_fts` (create all in Phase 1 so later phases need no migration for them).
- Each run writes a row to `runs` (start, end, counts per stage, errors). Failed source is logged; other sources keep running.
- Secrets never in the repo.
- **Documented as built (project-wide):** every task updates its page under `docs/guide/` in the same commit as the code (see "Documentation" below). A task is not complete, and a reviewer should reject it, if its doc page is missing, stale, or would not let a newcomer explain the step.

## Documentation

The owner will not write the code but must understand and explain all of it. Docs live in `docs/guide/`, written for a technically literate reader who has not seen the code.

| Page | Owned by task | Covers |
| --- | --- | --- |
| `docs/guide/README.md` | 1 (stub), 9 (final) | What the pipeline does, the nightly flow end to end, reading order for the other pages |
| `docs/guide/data-model.md` | 1, 2 | Models, every table and column, status lifecycle, dedupe/version rules with a worked example |
| `docs/guide/vault-and-config.md` | 3 | Vault layout, every `config.yaml`/`profile.yaml` field, what `init` does |
| `docs/guide/sources.md` | 4, 5, 6 | arXiv and HF APIs, rate limits, field mapping, how collect isolates failures |
| `docs/guide/triage.md` | 7 | Embeddings in plain terms, the scoring formula with each weight and why, how reasons are generated, how to tune |
| `docs/guide/digest.md` | 8 | Digest format, health line, status changes |
| `docs/guide/running.md` | 9 | CLI commands, a full run walkthrough with sample output, troubleshooting |
| `docs/guide/glossary.md` | any (append) | Terms (embedding, cosine similarity, FTS5, canonical id, …) |

Every page follows the same shape: **What it does** → **How it works** (step by step, with a small diagram or worked example) → **Why it's built this way** (decisions and alternatives rejected) → **How to run / check it** → **Where the code is** (file paths). Every task's commit step includes its page(s).

## Review Focus

1. Paper with empty/missing abstract: must be stored and ranked (title-only), not crash.
2. Same paper arriving from both arXiv and HF: one row, HF upvotes merged in.
3. One source raising (HTTP 500 / malformed feed): run completes, error recorded in `runs`, other source's papers kept.
4. `profile.yaml` with zero seeds or `top_n` larger than paper count: digest still written, no division/index errors.
5. Titles/abstracts with FTS5-hostile characters (`"`, `*`, `-`, unicode, LaTeX `$`): insert and index without error.

## File Structure

```
pyproject.toml
src/research_pipeline/
  models.py        # PaperMeta, Status, Profile, Config pydantic models
  db.py            # open_db(), schema, upsert_paper(), status queries, runs
  vault.py         # init_vault(), vault path resolution
  profile.py       # load_profile()
  sources/base.py  # Source protocol
  sources/arxiv.py
  sources/hf_daily.py
  collect.py       # run_collect()
  embed.py         # Embedder protocol, SentenceTransformerEmbedder, FakeEmbedder (tests)
  rank.py          # score_papers(), explain
  triage.py        # run_triage()
  digest.py        # write_digest()
  cli.py           # typer app: init, run, collect, triage, digest
  defaults/profile.yaml, defaults/config.yaml
tests/ (+ tests/fixtures/arxiv_atom.xml, hf_daily.json)
```

---

### Task 1: Repo skeleton and models

**Files:**
- Create: `pyproject.toml`, `src/research_pipeline/__init__.py`, `src/research_pipeline/models.py`, `.gitignore`, `README.md` (stub), `tests/test_models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  - `class Status(StrEnum)`: `new, triaged, fetched, summarized, in_digest, skipped, failed`
  - `class PaperMeta(BaseModel)`: `id: str, title: str, abstract: str = "", authors: list[str] = [], source: str, url: str, version: int = 1, hf_upvotes: int = 0, repos: list[str] = [], published: date | None = None`
  - `class Profile(BaseModel)`: `topics: list[str], seeds: list[str]` (arXiv ids), `followed_authors: list[str] = [], keywords: list[str] = [], top_n: int = 15`
  - `class Config(BaseModel)`: `vault_path: Path, arxiv_categories: list[str], embedding_model: str, rss_feeds: list[str] = []`

- [ ] **Step 1: Write failing tests** — `test_paper_meta_defaults` (abstract `""`, version 1), `test_status_values_match_spec` (asserts exact set of 7 strings), `test_profile_top_n_default_is_15`, `test_profile_rejects_top_n_below_1` (pydantic `ValidationError`).
- [ ] **Step 2: Run** `uv run pytest tests/test_models.py -v` → FAIL (module missing).
- [ ] **Step 3: Create `pyproject.toml`** (uv project, `research-pipeline = "research_pipeline.cli:app"` script, deps above, pytest config `testpaths=["tests"]`), then implement `models.py`.
- [ ] **Step 4: Run** `uv run pytest tests/test_models.py -v` → PASS.
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat: project skeleton and core models"`

### Task 2: SQLite schema and paper upsert

**Files:**
- Create: `src/research_pipeline/db.py`; Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `PaperMeta`, `Status`
- Produces:
  - `open_db(path: Path) -> sqlite3.Connection` (creates all 7 tables, `row_factory=sqlite3.Row`, WAL on)
  - `upsert_paper(conn, p: PaperMeta) -> Literal["inserted","updated","unchanged"]`
  - `papers_with_status(conn, status: Status) -> list[sqlite3.Row]`
  - `set_status(conn, paper_id: str, status: Status, score: float | None = None) -> None`
  - `start_run(conn) -> int`, `finish_run(conn, run_id: int, counts: dict[str,int], errors: list[str]) -> None`

Rules for `upsert_paper`: new id → insert as `new`, `"inserted"`. Same id, same/lower version → merge `hf_upvotes` (max) and `repos` (union) only, `"unchanged"` unless those changed (`"updated"`). Higher version → update title/abstract/version, `"updated"`; status becomes `triaged` if it was `in_digest`/`summarized`/`fetched`/`triaged`, `new` if it was `new`, and **`skipped` stays `skipped`**. Index title+abstract in `papers_fts` on insert/update.

- [ ] **Step 1: Write failing tests** — `test_open_db_creates_all_tables` (names == the 7), `test_upsert_inserts_new_as_status_new`, `test_upsert_same_version_is_unchanged`, `test_upsert_higher_version_resets_in_digest_to_triaged`, `test_upsert_higher_version_keeps_skipped_skipped`, `test_upsert_merges_hf_upvotes_and_repos`, `test_fts_handles_hostile_characters` (title `'Attn "is" all* you-need $x^2$ — ünïcode'`, then `SELECT … papers_fts MATCH 'ünïcode'` finds it), `test_empty_abstract_is_stored`, `test_run_log_roundtrip`.
- [ ] **Step 2: Run** `uv run pytest tests/test_db.py -v` → FAIL.
- [ ] **Step 3: Implement `db.py`.** Use a standalone FTS5 table (not external-content) keyed by `rowid` = papers rowid, with the FTS5 `unicode61` tokenizer; counts/errors in `runs` stored as JSON text.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: sqlite schema, status-aware upsert, run log`

### Task 3: Vault layout, config/profile loading, `cli init`

**Files:**
- Create: `vault.py`, `profile.py`, `defaults/profile.yaml`, `defaults/config.yaml`, `cli.py` (app + `init` command); Test: `tests/test_vault.py`

**Interfaces:**
- Consumes: `Profile`, `Config`, `open_db`
- Produces:
  - `init_vault(path: Path, *, topics: list[str] | None = None) -> None` — creates `raw/ markdown/ summaries/ my_work/ gold/ digests/`, `config.yaml`, `profile.yaml`, `vault.sqlite`; idempotent, **never overwrites** an existing `profile.yaml`/`config.yaml`.
  - `load_profile(path: Path) -> Profile`, `load_config(path: Path) -> Config`
  - CLI: `research-pipeline init [--vault PATH]` (default `~/research-vault`; env `RESEARCH_VAULT` overrides default). Prompts for topics (comma-separated) and categories only when stdin is a TTY.

- [ ] **Step 1: Write failing tests** — `test_init_creates_expected_layout` (tmp_path), `test_init_twice_preserves_edited_profile`, `test_load_profile_zero_seeds_ok`, `test_load_profile_invalid_yaml_raises_clear_error` (message names the file), `test_cli_init_via_typer_runner`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** Default `profile.yaml` ships with commented example topics and 3 placeholder seed ids. **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: vault layout, config and profile loading, cli init`

### Task 4: arXiv source

**Files:**
- Create: `sources/base.py`, `sources/arxiv.py`, `tests/fixtures/arxiv_atom.xml` (trim a real API response to 3 entries, one with empty summary, one with `v2` in its id); Test: `tests/test_arxiv.py`

**Interfaces:**
- Produces:
  - `class Source(Protocol)`: `name: str`; `fetch(self) -> list[PaperMeta]`
  - `class ArxivSource(categories: list[str], max_results: int = 300, client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep)`; queries `http://export.arxiv.org/api/query?search_query=cat:A+OR+cat:B…&sortBy=submittedDate&sortOrder=descending`, one call per page, calling `sleep(3.0)` **between** calls (not before the first).
  - `parse_arxiv_atom(xml: str) -> list[PaperMeta]` — id stripped of URL and version suffix, version parsed from `vN`, whitespace in title/abstract collapsed.

- [ ] **Step 1: Write failing tests** — `test_parse_extracts_id_and_version` (`2501.01234v2` → id `2501.01234`, version 2), `test_parse_collapses_whitespace`, `test_parse_empty_summary_gives_empty_abstract`, `test_fetch_paginates_and_sleeps_3s_between_calls` (respx mocks two pages; fake `sleep` records `[3.0]`), `test_fetch_http_500_raises` (a typed `SourceError`).
- [ ] **Step 2–4: Run FAIL → implement → PASS.** Verify the live API shape once manually (`curl` one query) before finalising the fixture.
- [ ] **Step 5: Commit** `feat: arxiv source`

### Task 5: Hugging Face Daily Papers source

**Files:**
- Create: `sources/hf_daily.py`, `tests/fixtures/hf_daily.json`; Test: `tests/test_hf_daily.py`

**Interfaces:**
- Produces: `class HfDailySource(client: httpx.Client | None = None)` with `name="hf_daily"`; `parse_hf_daily(data: list[dict]) -> list[PaperMeta]` from `https://huggingface.co/api/daily_papers`; maps arXiv id → `id`, `upvotes` → `hf_upvotes`, linked GitHub repo → `repos`, `source="hf_daily"`. Missing optional fields tolerated.

- [ ] **Step 1: Write failing tests** — `test_parse_maps_upvotes_and_repo`, `test_parse_missing_repo_and_summary_ok`, `test_fetch_malformed_json_raises_source_error`.
- [ ] **Step 2–4: FAIL → implement → PASS.** Check the live payload once and adjust the fixture to match.
- [ ] **Step 5: Commit** `feat: hugging face daily papers source`

### Task 6: Collect stage

**Files:**
- Create: `collect.py`; Test: `tests/test_collect.py`

**Interfaces:**
- Consumes: `Source`, `upsert_paper`, `start_run/finish_run`
- Produces: `run_collect(conn, sources: list[Source], run_id: int) -> dict[str,int]` returning `{"inserted":…, "updated":…, "unchanged":…, "source_errors":…}`; catches `SourceError` (and any `Exception`) **per source**, appends `"<source>: <msg>"` to errors, continues.

- [ ] **Step 1: Write failing tests** (stub sources) — `test_collect_dedupes_same_id_across_sources_and_merges_upvotes`, `test_collect_failing_source_does_not_stop_others_and_is_logged`, `test_collect_second_run_inserts_nothing`, `test_collect_version_bump_counts_as_updated`.
- [ ] **Step 2–4: FAIL → implement → PASS.** **Step 5: Commit** `feat: collect stage with per-source isolation`

### Task 7: Embedder, ranker and triage

**Files:**
- Create: `embed.py`, `rank.py`, `triage.py`; Test: `tests/test_rank.py`, `tests/test_triage.py`

**Interfaces:**
- Produces:
  - `class Embedder(Protocol)`: `model_name: str`; `embed(self, texts: list[str]) -> np.ndarray` (L2-normalised, shape `(n, d)`)
  - `SentenceTransformerEmbedder(model_name: str = "BAAI/bge-small-en-v1.5")` — lazy import; `FakeEmbedder` in `tests/conftest.py` (hash-based bag-of-words so similar texts are closer).
  - `@dataclass class ScoredPaper: id: str, score: float, reasons: list[str]`
  - `score_papers(papers: list[sqlite3.Row], paper_vecs: np.ndarray, profile: Profile, profile_vecs: ProfileVectors) -> list[ScoredPaper]` — score = max cosine to seed/topic vectors + 0.05·log1p(hf_upvotes) + 0.1 per followed-author hit + 0.05 per keyword hit (cap 0.2); reasons e.g. `"closest seed: 2401.00001 (0.71)"`, `"topic: adversarial robustness (0.64)"`, `"HF upvotes: 42"`, `"followed author: X"`. `my_work` and vote penalty terms are 0 stubs with a `TODO(phase3/4)` comment.
  - `run_triage(conn, embedder: Embedder, profile: Profile, seed_texts: dict[str,str]) -> dict[str,int]` — takes `new` papers, embeds `title + abstract` (title-only if no abstract), stores vectors in `embeddings` (with `embedding_model`; skips re-embedding when a row for the same model exists), marks top N `triaged` (with score), rest `skipped`. Seed texts come from the papers table when the seed id is present; unresolved seeds are ignored with a logged warning.

- [ ] **Step 1: Write failing tests** — `test_on_topic_paper_outranks_off_topic`, `test_reasons_name_the_driving_seed`, `test_hf_upvotes_boost_breaks_tie`, `test_followed_author_boost`, `test_zero_seeds_uses_topics_only`, `test_zero_seeds_and_zero_topics_scores_by_boosts_only_without_error`, `test_triage_keeps_top_n_and_skips_rest`, `test_triage_n_larger_than_papers_keeps_all`, `test_triage_title_only_paper_is_ranked`, `test_triage_rerun_does_not_reembed` (FakeEmbedder call counter), `test_skipped_papers_still_in_fts`.
- [ ] **Step 2–4: FAIL → implement → PASS.** **Step 5: Commit** `feat: embedding ranker and triage stage`

### Task 8: Digest writer

**Files:**
- Create: `digest.py`; Test: `tests/test_digest.py`

**Interfaces:**
- Consumes: papers with status `triaged`, stored scores/reasons (persist reasons as JSON in `papers.triage_reasons` — add this column in Task 2's schema if absent; **update `db.py` and its table test accordingly**)
- Produces: `write_digest(conn, vault: Path, date: date, run_summary: str) -> Path` writing `digests/YYYY-MM-DD.md`: first line is a one-line health summary (counts collected/kept/skipped, source errors); then per paper, ranked: title (linked to abs URL), authors, "Why it matched" bullets, abstract, HF upvotes/repos if any. Sets those papers to `in_digest`. Re-running the same date overwrites the file and includes only newly `triaged` papers plus any already `in_digest` for that date.

- [ ] **Step 1: Write failing tests** — `test_digest_file_named_by_date_and_ranked_descending`, `test_digest_contains_reasons_and_abstract`, `test_digest_first_line_is_health_summary_with_errors`, `test_digest_sets_status_in_digest`, `test_empty_triaged_writes_digest_saying_nothing_new`.
- [ ] **Step 2–4: FAIL → implement → PASS.** **Step 5: Commit** `feat: markdown digest writer`

### Task 9: `run` command and end-to-end test

**Files:**
- Modify: `cli.py`; Test: `tests/test_e2e.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `research-pipeline run [--vault PATH]` executes start_run → collect → triage → digest → finish_run in order; also `collect`, `triage`, `digest` individually. Exit code 0 even if a source failed (errors recorded), non-zero only if the vault is missing/uninitialised.

- [ ] **Step 1: Write failing tests** — `test_e2e_offline_run_produces_digest` (init vault in tmp_path, inject fixture-backed sources + FakeEmbedder via a `build_pipeline()` factory the CLI uses and tests monkeypatch; assert digest exists, `runs` row has end time, top paper is the on-topic one); `test_e2e_second_run_same_day_adds_no_duplicates`; `test_run_on_uninitialised_vault_exits_nonzero`.
- [ ] **Step 2–4: FAIL → implement → PASS**, then run the full suite: `uv run pytest -q` → all green.
- [ ] **Step 5: Manual smoke test on real data** — `uv run research-pipeline init && uv run research-pipeline run`, open the digest, confirm the Phase 1 exit criterion ("a useful daily digest"). Record the elapsed time (target: under a few minutes).
- [ ] **Step 6: Commit** `feat: run command and end-to-end pipeline`

---

## Self-Review Notes

- **Spec coverage:** skeleton/models/schema (T1–2), vault + `cli init` (T3), arXiv + HF sources with dedupe (T4–6), `profile.yaml` (T3), embedding ranker + top-N (T7), Markdown digest with match reasons (T8), run log (T2/T9). RSS, votes, Datasette, systemd are later phases.
- **Deferred deliberately:** `my_work/` and vote terms (stubbed zero), RSS feeds, LLM "why matched" line.
- **Docs:** each task's page is in the Documentation table; Task 9 finishes `README.md` as the Phase 1 walkthrough and checks every page against the code.
- **Open question carried from the master plan:** which embedding model is best for triage — default is `BAAI/bge-small-en-v1.5`; swapping is a one-line config change, comparison happens in Phase 2 with the gold set.
