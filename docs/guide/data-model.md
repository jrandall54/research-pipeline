# Data model

## What it does

Defines the small set of typed objects every stage passes around, and the lifecycle a paper moves through. Defined with pydantic, which validates values on creation (bad input raises an error instead of flowing on).

## How it works

### `PaperMeta` — one paper as a source reports it

| Field | Meaning |
| --- | --- |
| `id` | Canonical id: arXiv id, else DOI, else a hash of the URL. The dedupe key. |
| `title` | Paper title. |
| `abstract` | Abstract text; `""` if the source has none. |
| `authors` | Author names, in order. |
| `source` | Which source produced it, e.g. `arxiv`, `hf_daily`. |
| `url` | Link to the paper page. |
| `version` | Version number (arXiv v1, v2 ...); starts at 1. A higher version than stored is treated as an update. |
| `hf_upvotes` | Upvotes on Hugging Face Daily Papers; 0 if unknown. Used as a ranking boost. |
| `repos` | Code repository URLs linked to the paper. |
| `published` | Publication date, or `None` if unknown. |

### `Profile` — what you care about

| Field | Meaning |
| --- | --- |
| `topics` | Free-text topics you follow (required). |
| `seeds` | arXiv ids of papers you like; ranking measures similarity to them (required, may be empty). If empty, ranking falls back to `topics` plus the boosts (`followed_authors`, `keywords`, HF upvotes). |
| `followed_authors` | Authors whose papers get a boost. |
| `keywords` | Words that give a boost when they appear. |
| `top_n` | How many papers to keep per run. Default 15, must be at least 1. |

### `Config` — pipeline settings

| Field | Meaning |
| --- | --- |
| `vault_path` | Informational (required): the vault used is set by `--vault` / `$RESEARCH_VAULT` / the default, not by this field. |
| `arxiv_max_results` | Cap on arXiv papers fetched per run. Default 1000, at least 1. |
| `arxiv_categories` | arXiv categories to collect. Default `cs.LG, cs.CL, cs.CR, cs.OS, cs.SE`, or exactly `["all"]` for every computer-science category (`cs.*`). |
| `embedding_model` | Model used to turn text into vectors. Default `BAAI/bge-small-en-v1.5`. |
| `rss_feeds` | Extra RSS feed URLs; default none. |
| `llm_model`, `llm_url`, `llm_num_ctx` | The local model for summaries (`llm.md`). `llm_num_ctx` defaults to 32768 and must be at least 8192. |

`Profile` and `Config` reject unknown keys (`extra="forbid"`).

### `Status` — the paper lifecycle

```
                 +--> triaged --> fetched --> summarized --> in_digest
                 |       ^                                       |
  new -----------+       |    (a higher version arrives)         |
                 |       +---------------------------------------+
                 |
                 +--> skipped        (stays skipped, even on a new version)

  any stage --> failed
```

In words: a paper starts `new`. Triage moves it to `triaged` (kept, top N) or `skipped` (below the cut). A kept paper is listed in a digest and becomes `in_digest`. The summarize stage then moves each `triaged` paper to `fetched` (its full text, or just its abstract if there is no HTML version, is stored) and then `summarized` (the model's summary and critique are stored). The digest lists `triaged`, `fetched` and `summarized` papers and marks them `in_digest`. A paper whose fetch or summary fails keeps its last status (`triaged` or `fetched`), gets `error` and `retry_count` updated, and is tried again next run; until then the digest lists it with its abstract only. If a higher version of a kept paper arrives, it goes back to `triaged` so it can be reconsidered. Any stage can end in `failed`.

| Status | Meaning | Set by |
| --- | --- | --- |
| `new` | Collected, not yet scored. | collect |
| `triaged` | Scored and kept in the top N. | triage |
| `fetched` | Full text (or the abstract, when no HTML exists) stored; waiting for the model. | summarize |
| `summarized` | Summary and critique stored. | summarize |
| `in_digest` | Listed in a digest. | digest |
| `skipped` | Scored but below the cut; kept and searchable. | triage |
| `failed` | Processing errored. | nothing yet (failed papers keep their last status instead) |

A new version of a kept paper (`triaged`, `fetched`, `summarized`, `in_digest`) sets it back to `triaged`. A new version of a `new` paper leaves it `new`; of a `skipped` paper leaves it `skipped`; of a `failed` paper leaves it `failed`.

## Why it's built this way

- Typed models catch malformed source data at the boundary.
- `skipped` papers are retained (not deleted) so search still finds them.
- Status values are a `StrEnum`, so they store as plain strings in the database.

## How to run / check it

`uv run pytest tests/test_models.py -v`

## Where the code is

`src/research_pipeline/models.py`, `tests/test_models.py`

## Database tables

All state lives in one SQLite file (`vault.sqlite` in the vault), opened by `open_db()`. Opening creates every table if missing and switches the file to WAL mode. Timestamps are ISO-8601 UTC text. Lists (authors, repos, reasons) are stored as JSON text.

### `papers` — one row per paper

| Column | Meaning |
| --- | --- |
| `rowid` | Integer primary key. Used as the row number in the search index (see below). Never changes for a paper. |
| `id` | Canonical id. Unique; the dedupe key. |
| `title`, `abstract` | Text from the source (`abstract` may be `''`). |
| `authors` | JSON list of names. |
| `source` | Source that first reported it (`arxiv`, `hf_daily`, ...). |
| `url` | Paper page. |
| `version` | Highest version seen. |
| `hf_upvotes` | Highest HF upvote count seen. |
| `repos` | JSON list of code repository URLs (union over all sightings). |
| `published` | ISO date or NULL. |
| `status` | One of the `Status` values; starts `new`. |
| `triage_score` | Score from the last triage, or NULL. |
| `triage_reasons` | JSON list of strings explaining the score, or NULL. |
| `digest_date` | ISO date of the digest that listed it, or NULL. |
| `retry_count` | Times a stage failed for this paper (`record_failure` in `db.py` adds one each time the summarize stage fails it); starts 0. |
| `error` | Last error message from a failed stage, or NULL; cleared when the summary succeeds. |
| `created_at`, `updated_at` | When the row was first stored / last changed. |

### Other tables

Created now so later phases need no migration. Phase 1 uses `runs` and `embeddings`; Phase 2 uses `fulltexts` and `summaries`.

| Table | Columns | Purpose |
| --- | --- | --- |
| `embeddings` | `item_id`, `embedding_model`, `vector` (BLOB), `dim`; primary key (`item_id`, `embedding_model`) | Cached vectors, one per item per model, so changing the model never mixes vectors. |
| `summaries` | `paper_id`, `kind`, `text`, `model`, `created_at`; primary key (`paper_id`, `kind`) | A paper's model-written summary and critique ([summaries.md](summaries.md)): one row with `kind = summary` and one with `kind = critique`, `text` holding JSON and `model` the model name. The summary row's JSON also carries `basis` (`full_text` or `abstract`), `truncated` and `unverified`. `save_summary()` replaces both rows (no commit; the caller commits); `get_summary()` returns a `PaperSummary`, or `None` unless both rows exist. |
| `fulltexts` | `paper_id` (primary key, references `papers`), `sections` (JSON list of `{title, text}`), `fetched_at` | A paper's arXiv HTML full text, hidden text already removed, split into titled sections (`fulltext.md`). `save_fulltext()` replaces any earlier copy; `get_fulltext()` returns `Section` objects or `None`. |
| `votes` | `id`, `paper_id`, `vote` (+1/-1), `created_at` | Your thumbs up/down (later phase). |
| `links` | `src_id`, `dst_id`, `kind`, `created_at`; primary key (all three) | Relations between items, e.g. a paper and a note (later phase). |
| `runs` | `id`, `started_at`, `finished_at`, `counts` (JSON object), `errors` (JSON list) | One row per pipeline run: `start_run()` opens it, `finish_run()` fills in counts per stage and error messages. |
| `papers_fts` | `title`, `abstract` | Full-text search index (below). |

### Upsert, dedupe and version rules

`upsert_paper(conn, paper)` is called for every paper a source returns, and returns `"inserted"`, `"updated"` or `"unchanged"`.

1. **Unknown id**: insert with status `new`, index title and abstract. Returns `inserted`.
2. **Known id, same or lower version**: nothing about the paper's text or status changes. Only two things merge: `hf_upvotes` keeps the maximum, and `repos` becomes the union. If either grew, `updated`, otherwise `unchanged`.
3. **Known id, higher version**: title, abstract, authors, url, version and published are replaced, upvotes and repos merge, and the search index entry is rewritten. Status: `in_digest`, `summarized`, `fetched` and `triaged` become `triaged`; `new` stays `new`; `skipped` stays `skipped`; `failed` is left as is. Returns `updated`.

Worked example (paper `2401.00001`):

| Step | Incoming | Stored result | Returns |
| --- | --- | --- | --- |
| 1 | v1, 3 upvotes | inserted, status `new`, 3 upvotes | `inserted` |
| 2 | v1 again, 3 upvotes (next night) | nothing changes | `unchanged` |
| 3 | v1, 10 upvotes | upvotes 10, status `new` | `updated` |
| 4 | (triage keeps it, digest lists it) | status `in_digest` | n/a |
| 5 | v2 arrives | new title/abstract, version 2, status `triaged` | `updated` |

Now a different paper `2401.00002`, scored below the cut: status `skipped`. When v2 arrives its text is replaced and re-indexed, but the status stays `skipped`. Reason: the pipeline already decided you do not want this paper; a new version of it is not a reason to re-open that decision, and it stays searchable.

### Full-text search (FTS5)

`papers_fts` is a standalone FTS5 table with the `unicode61` tokenizer and columns `title`, `abstract`. Each row's FTS rowid equals `papers.rowid`, so a hit joins back to the paper. Unicode61 splits on punctuation and folds case and accents, so `ünïcode` matches `unicode`. Titles with quotes, `*`, `$x^2$` or dashes index fine because values are always passed as SQL parameters, never pasted into SQL.

```sql
SELECT p.id, p.title
FROM papers_fts JOIN papers p ON p.rowid = papers_fts.rowid
WHERE papers_fts MATCH 'transformer attention'   -- both words anywhere
ORDER BY papers_fts.rank;                        -- best match first
```

Note that the query string itself is FTS5 syntax (quotes, `*`, `AND`/`OR`), so user-typed queries must be quoted before use. Later phases can add summary and full-text columns by rebuilding the index.

## Why the database is built this way

- **One SQLite file**: no server, easy to back up, ships with Python, includes FTS5.
- **Own integer `rowid`** instead of using the text id: SQLite may renumber implicit rowids on `VACUUM`, which would silently break the link to the search index. An explicit integer primary key never changes.
- **Standalone FTS table** (not "external content"): simpler and safer; the cost is storing the text twice, which is trivial at this scale. The upsert deletes and re-inserts the entry on change.
- **WAL mode**: readers do not block the writer, and a crash mid-write does not corrupt the file.
- **JSON text for lists**: avoids extra join tables for data we only ever read whole.
- **`skipped` stays skipped**: see the worked example.
- **All tables now**: later phases need no migration.

## How to run / check the database

`uv run pytest tests/test_db.py -v`

## Where the database code is

`src/research_pipeline/db.py`, `tests/test_db.py`
