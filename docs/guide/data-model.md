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
| `feedback_halflife_days` | Days after which a vote or save counts half as much. Default 60, between 1 and 3650. |

### `Config` — pipeline settings

| Field | Meaning |
| --- | --- |
| `vault_path` | Informational (required): the vault used is set by `--vault` / `$RESEARCH_VAULT` / the default, not by this field. |
| `arxiv_max_results` | Cap on arXiv papers fetched per run. Default 1000; the command accepts 50 to 5000 (`rpipe sources set-max-results`). |
| `arxiv_categories` | arXiv categories to collect. Default `cs.LG, cs.CL, cs.CR, cs.OS, cs.SE`, or exactly `["all"]` for every computer-science category (`cs.*`). |
| `embedding_model` | Model used to turn text into vectors. Default `BAAI/bge-small-en-v1.5`; change with `rpipe model set-embedding`. |
| `rss_feeds` | Reserved for the future RSS source; not in the default template, still accepted in old files. |
| `llm_model`, `llm_url`, `llm_num_ctx` | The local model for summaries (`llm.md`). `llm_model` and `llm_num_ctx` default to `auto`; a pinned `llm_num_ctx` (for example 32768) must be at least 8192. Change them with `rpipe model set`, `rpipe model set-url` and `rpipe model set-context`. |
| `llm_effort` | Claude Code thinking effort: `low` (default), `medium` or `high`; set with `rpipe model set-effort`. Missing in an older vault means `low`. |
| `llm_backend`, `llm_cli_path` | Which backend writes summaries (default `ollama`; see `backends.md`) and the full path of a subscription CLI. Missing keys in an older vault mean Ollama. |
| `summarize_mode` | How papers are read: `auto` (default), `whole` or `split`; set with `rpipe model set-mode` (see "Choosing the mode" in `summaries.md`). Missing in an older vault means `auto`. |
| `diagrams` | Whether each paper also gets a diagram: `auto` (default; off on a CPU-only Ollama model), `on` or `off`; set with `rpipe model set-diagrams` (see "The diagrams setting" in `llm.md`). Missing in an older vault means `auto`. |
| `sources` | Enabled paper sources in collection order, default `[arxiv, hf_daily]`; non-empty, no repeats. Change with `rpipe sources enable NAME` / `disable NAME` (see `sources.md`, `plugins.md`). |

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

In words: a paper starts `new`. Triage moves it to `triaged` (kept, top N) or `skipped` (below the cut). A kept paper is listed in a digest and becomes `in_digest`. The summarize stage then moves each `triaged` paper to `fetched` (its full text is stored; a paper with no usable HTML version stores nothing in `fulltexts` and is summarised from its abstract) and then `summarized` (the model's summary and critique are stored). In `rpipe run` the summarize stage runs before the digest; the digest then lists `triaged`, `fetched` and `summarized` papers and marks them `in_digest`. A paper whose fetch or summary fails keeps its last status (`triaged` or `fetched`), gets `error` and `retry_count` updated, and the summarize stage offers it again on the next `rpipe summarize` or `rpipe run`, until it has failed 3 times. A paper already `in_digest` for *today* with no stored summary is also offered (its status does not change; only the summary is saved, and the digest rewrite shows it). Until then the digest lists the paper with its abstract only. Papers from an earlier day's digest are never summarised later (accepted limitation). If a higher version of a kept paper arrives, it goes back to `triaged` so it can be reconsidered. Any stage can end in `failed`.

| Status | Meaning | Set by |
| --- | --- | --- |
| `new` | Collected, not yet scored. | collect |
| `triaged` | Scored and kept in the top N. | triage |
| `fetched` | Full text stored (nothing is stored for an abstract-only paper); waiting for the model. | summarize |
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
| `retry_count` | Times a stage failed for this paper (`record_failure` in `db.py` adds one each time the summarize stage fails it; at 3 the paper is no longer retried); starts 0. |
| `error` | Last error message from a failed stage, or NULL; cleared when the summary succeeds. |
| `created_at`, `updated_at` | When the row was first stored / last changed. |

### Other tables

Phase 1 uses `runs` and `embeddings`; Phase 2 uses `fulltexts` and `summaries`; Phase 3 uses `votes` and `saves`; the review loop uses `skips`; the shelf ([shelf design](../superpowers/specs/phase-04-shelf-design.md)) uses `shelf_items` and `shelf_chunks`; the pipeline/app split (Phase 7) adds `requests`, the request list (below).

**Who writes which tables.** The code is split in two halves. `db.py` owns the schema, the connection and the pipeline's own writes (`papers`, `runs`, `summaries`, `fulltexts`, `embeddings`, and `papers_fts`). `library.py` owns the writes you make by hand: `votes`, `saves`, `skips`, `shelf_items` and `shelf_chunks`. The app commands (`rpipe vote`, `save`, `unsave`, `feedback`, `review`, `show` and `shelf`) do not call `db.py` or `library.py` themselves: they go through `library_api.py`, the app's one door, which hands back data objects and passes each change on to `library.py` or `shelf.py` (see "The app's door" in [architecture.md](architecture.md)). The `requests` table (the request list, below) is written by both halves: the app adds requests through `library_api.request`, and the pipeline later marks them running, done or failed. The pipeline (`triage.py`, `rank.py`, `digest.py`) reads only the names listed in `library.PIPELINE_READS`, so the nightly code never writes your votes. One thing in a nightly run does write library rows: just before triage, `rpipe run` (and `rpipe triage`) rebuild the shelf rows from your `my_work/` files with `library_api.shelf_sync`, the same call as `rpipe shelf sync`. That call is made by the command (`cli.py`), not by triage, which only reads the shelf. Both halves share one database file. `library.py` imports two helpers from `db.py`: `_now()` (the timestamp format) and `_require_paper()` (the check that a paper id exists).

| Table | Columns | Purpose |
| --- | --- | --- |
| `embeddings` | `item_id`, `embedding_model`, `vector` (BLOB), `dim`; primary key (`item_id`, `embedding_model`) | Cached vectors, one per item per model, so changing the model never mixes vectors. `item_id` is a paper id for papers and `shelf:<name>#<chunk_idx>` for a shelf chunk (built by `shelf_embedding_id()`), so the two never collide. |
| `summaries` | `paper_id`, `kind`, `text`, `model`, `created_at`; primary key (`paper_id`, `kind`) | A paper's model-written summary and critique ([summaries.md](summaries.md)): one row with `kind = summary` and one with `kind = critique`, `text` holding JSON and `model` the model name. The summary row's JSON also carries `basis` (`full_text` or `abstract`), `body_cut` (core text was trimmed to fit; rows written before the pruning change say `truncated`, which is read as `body_cut`), `needed_ctx` (only when `body_cut`: the context in tokens, a multiple of 1024, that would fit the core text uncut; `null` otherwise and read as `None` on rows that predate it), `dropped` (sections left out, each with `title`, `chars` and `reason` `non_paper` or `appendix`), `unverified` (summary and critique claims whose citations could not be verified), `notes_unverified` (reading in parts only: texts of notes whose citations could not be verified; not in the summary; absent on older rows and read as `[]`), `read_mode` (`whole`, `split` or `map_reduce`: how the paper was read, see "Which way a paper is read" in summaries.md; rows from before Part B.6 have none and read as `whole`), `parts` (notes calls made; 1 when read whole, and on older rows), `hook` (Phase 9: one catchy sentence, a claim with its cited sections; absent or `null` on older rows and when the model gave none), `key_numbers` (Phase 9: up to three `{value, label, sections}` figures; absent on older rows, read as `[]`), `checks` (Phase 9: plain-words reasons a hook, key number or diagram was left out; read as `[]` when absent; never part of the summary itself) and (since Phase 3) `provenance` (`backend`, `model`, `prompt_version`, `paper_version`, `num_ctx`, `gpu_share` (share of the model in GPU memory, 0 to 1, `null` when unknown or not Ollama; absent on rows from before Part B.5), `created_at`; absent on older rows). `save_summary()` replaces both rows (no commit; the caller commits); `get_summary()` returns a `PaperSummary`, or `None` unless both rows exist; `get_provenance()` returns the provenance dict or `None`. A third kind, `diagram` (Phase 9, no schema change), holds one paper's diagram as JSON (`shape` of `parts`, `steps` or `loop`; `nodes` with `id`, `label`, optional `detail` and `sections`; `edges` whose keys are `from` and `to`, with an optional `label`; and a `caption`). `save_diagram()` writes it (passing `None` deletes the row; no commit) and `get_diagram()` reads it back or returns `None`. A diagram row never affects `get_summary()`, which looks only at the `summary` and `critique` rows. |
| `fulltexts` | `paper_id` (primary key, references `papers`), `sections` (JSON list of `{title, text}`), `fetched_at` | A paper's arXiv HTML full text, hidden text already removed, split into titled sections (`fulltext.md`). `save_fulltext()` replaces any earlier copy; `get_fulltext()` returns `Section` objects or `None`. |
| `votes` | `id`, `paper_id`, `vote` (+1, -1 or 0), `created_at` | Your thumbs up/down. Append-only history: each click adds a row, nothing is edited. `0` means "cleared". `record_vote()` validates the value (else `ValueError`) and that the paper exists (else `UnknownPaperError`), then commits. `current_votes()` returns the latest vote per paper (highest `id`), leaving out papers whose latest vote is 0, so you can change your mind and the newest click wins. |
| `saves` | `paper_id` (primary key, references `papers`), `created_at` | Papers you bookmarked to keep. One row per saved paper; unsaving deletes the row. `set_saved()` (idempotent; saving an id that is not in `papers` raises `UnknownPaperError`), `is_saved()` and `list_saved()` (newest first). |
| `skips` | `paper_id` (primary key, references `papers`), `skipped_at` | Papers you chose to "review later" in `rpipe review`. One row per paper; skipping again refreshes `skipped_at` (the paper moves to the back of the later list). `record_skip()` (raises `UnknownPaperError` for a bad id) and `clear_skip()` (idempotent) both commit. A skip is not a vote and never feeds the profile. `review.py` reads it: `review_queue()` (digest papers with no active vote and no skip, oldest digest first, then triage score high to low), `later_queue()` (skipped papers with no active vote, oldest skip first) and `queue_progress()` (handled and total for one digest date). |
| `shelf_items` | `name` (primary key), `content_hash`, `added_at` | One row per piece of your own work on the shelf (a note, draft or paper of yours). `content_hash` lets a later sync skip unchanged files. `added_at` really means "last indexed": re-indexing an item deletes and re-inserts it, so the timestamp resets. |
| `shelf_chunks` | `item_name` (references `shelf_items`, cascade delete), `chunk_idx`, `text`; primary key (`item_name`, `chunk_idx`) | The pieces each item is cut into; each chunk is embedded and compared with papers. `upsert_shelf_item()` replaces all of an item's chunks and deletes its `shelf:<name>#%` embedding rows (so stale vectors never linger); `delete_shelf_item()` removes the item, its chunks and its embeddings (both are idempotent and commit). `list_shelf_items()` adds `n_chunks`; `shelf_chunk_rows()` returns every chunk. Embedding ids are `shelf:<name>#<chunk number>`. A `LIKE` match narrows the candidates (with `%` and `_` in the name escaped, so `a_b` never touches `axb`), then only ids whose text after `shelf:<name>#` is purely digits are deleted. That second check matters because file names may contain `#`: deleting item `notes` must not wipe item `notes#2`, whose ids (`shelf:notes#2#0`) also start with `shelf:notes#`. |
| `requests` | `id`, `kind`, `payload` (JSON text), `status`, `tries`, `error`, `result`, `created_at`, `updated_at` | The request list: work the app asks the pipeline to do later. See "The request list" below. |
| `links` | `src_id`, `dst_id`, `kind`, `created_at`; primary key (all three) | Relations between items, e.g. a paper and a note (later phase). |
| `runs` | `id`, `started_at`, `finished_at`, `counts` (JSON object), `errors` (JSON list) | One row per pipeline run: `start_run()` opens it, `finish_run()` fills in counts per stage and error messages. |
| `papers_fts` | `title`, `abstract` | Full-text search index (below). |

### Timestamps and schema version

Every `created_at` / `updated_at` is UTC ISO 8601 to the second with an explicit offset, e.g. `2026-11-21T09:30:00+00:00` (made by `_now()` in `db.py`: `datetime.now(UTC).isoformat(timespec="seconds")`). Anything that writes rows directly, such as a Datasette SQL insert into `votes` or `saves`, must use the same format so rows sort correctly; in SQL: `strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')`.

The database carries a version number in SQLite's built-in `PRAGMA user_version` (a spare integer stored in the file header; new files start at 0). `open_db()` compares it with `SCHEMA_VERSION` (now 4) and, if the file is older, runs `_migrate()` and stamps the new number. Tables are created with `CREATE TABLE IF NOT EXISTS`, so adding a table (as `saves`, `skips`, `shelf_items`, `shelf_chunks` and, in version 4, `requests` were) needs no work beyond bumping the version; changes to existing tables (new column, data fix) get an `if from_version < N:` step in `_migrate()`. An existing vault therefore upgrades itself the next time it is opened.

The reverse case is refused: if the file's `user_version` is *greater* than `SCHEMA_VERSION` (a vault last used by a newer `rpipe`), `open_db()` raises `SchemaTooNewError` and the CLI prints a message telling you to upgrade `rpipe`. The check runs before anything else touches the file (before the WAL setting, table creation or migration), so the file is left byte-for-byte unchanged. Code never downgrades a database.

**Going back from version 4 to 3, if you ever must.** Why it matters: if you install an older `rpipe` (one that knows only version 3) on a vault that a newer one has opened, the older one refuses the vault as "too new" and stops. The only thing version 4 added is the `requests` table (the request list), so going back is two SQL lines, after a backup and with no `rpipe` command running: `sqlite3 <vault>/vault.sqlite 'DROP TABLE requests; PRAGMA user_version=3;'`. This deletes the request list (waiting requests are lost; ask again later with `rpipe requests add`); papers, votes, saves and summaries are untouched. This is a rare recovery step for power users, not part of any normal workflow: the usual fix is to upgrade `rpipe` instead.

### The request list

The program is being split into two halves that share one database: the *pipeline* (collect, triage, summarise; it runs at night under a lock and may use a language model) and the *app* (the commands you use: vote, save, review, and later a request like "summarise this paper now"). The app must not run a model itself, so when it wants pipeline work done it writes a row in `requests` and the pipeline picks it up on its next run. That is the request list.

Each row has:

| Column | Meaning |
| --- | --- |
| `kind` | What to do. Only names in `db.REQUEST_KINDS` are accepted; today that is just `summarize`. |
| `payload` | The details as JSON, written *canonically* (`json.dumps(payload, sort_keys=True)`: keys in alphabetical order, so the same details always give exactly the same text). For `summarize`: `{"paper_id": "2611.25005"}`. |
| `status` | `pending` (waiting), `running` (the pipeline is working on it), `done` or `failed`. New rows start `pending`. |
| `tries` | How many times the pipeline has attempted it (starts at 0). |
| `error`, `result` | What went wrong, or what came out; empty until the pipeline fills them in. |
| `created_at`, `updated_at` | Timestamps in the usual format. |

How a request is added, step by step (`library_api.request(conn, "summarize", {"paper_id": "arXiv:2611.25005v2"})`, which is what `rpipe requests add arXiv:2611.25005v2` calls):

1. The kind is checked against `REQUEST_KINDS`; `fetch_url` would be refused with `ValueError`. The payload must be a dict (JSON object); anything else (text, a list, nothing) is also a `ValueError`.
2. For `summarize`, `paper_id` must be text, else `ValueError`.
3. The id is normalised with `ids.normalise_paper_id` (see "Paper-id spellings" below): `arXiv:2611.25005v2` becomes `2611.25005`. Text that is not an id at all (`hello`) raises `UnknownPaperError`, the same error as a missing paper, so callers handle one error type.
4. The paper must already be in `papers` (`get_paper`), else `UnknownPaperError`. There is no title search here: a request names a known paper.
5. `db.insert_request` writes the row with the normalised id and commits, and returns its `id`. If a `pending` or `running` row with the same kind and the same canonical payload already exists, it returns that row's id instead of adding a second one, so pressing "summarise" twice queues the work once. A `done` or `failed` request does not block a new one: asking again after it finished adds a fresh row.

`requests_by_status(conn, "pending", "running")` lists rows in any of the given statuses, oldest first (by `id`). The pipeline moves rows through the statuses with `mark_request(conn, id, status, error=..., result=..., add_try=..., undo_try=...)` (sets `updated_at` and commits; `error` and `result` are written as given, so leaving them out clears them; `add_try` counts one more try, `undo_try` gives one back) and `reset_running_requests(conn)` (puts rows left `running` by a run that died back to `pending`, returning how many). How the pipeline works through the list, and the retry rule, are in "The request list" in [running.md](running.md).

### Paper-id spellings

People write an arXiv id in several ways: `2611.25005`, `2611.25005v2` (a specific version), `arXiv:2611.25005`, `https://arxiv.org/abs/2611.25005v1`, `https://arxiv.org/pdf/2611.25005.pdf`. The database stores the bare id without a version (`2611.25005`; old-style ids look like `cs/0112017`). `ids.normalise_paper_id()` turns any of those spellings into the bare id and raises `ValueError` for anything else. `ids.py` uses only Python's standard library so both halves can import it. `profile_edit.normalise_arxiv_id()` (used for seeds and the gold set) now simply calls it, and `rpipe vote`, `rpipe save` and `rpipe show` use it too (see [review.md](review.md)).

### Active vote

A paper has an *active vote* when the latest `votes` row for it (highest `id`) has `vote != 0`. No row, or a latest row of 0 (cleared), means no active vote, so the paper counts as unvoted. `current_votes()` and the review queues in `review.py` use this same rule, so a cleared vote sends a paper back into the review queue.

### Foreign keys and paper ids

`votes`, `saves`, `skips`, `summaries` and `fulltexts` have a `paper_id` that *references* `papers(id)` (a foreign key: a promise that the id exists in the other table). SQLite ignores that promise unless you ask it to enforce it, separately on every connection. `open_db()` now runs `PRAGMA foreign_keys=ON`, so a row pointing at a missing paper is rejected with `sqlite3.IntegrityError`. On top of that, `record_vote()`, `record_skip()` and `set_saved(..., True)` check the id first and raise `UnknownPaperError` with a readable message (`rpipe vote`/`rpipe save` print it and exit 1). Unsaving an unknown id is harmless and stays silent. `papers` itself is never deleted from, so the paper tables never need cascading deletes. The one place that does rely on a cascade is the shelf: `shelf_chunks.item_name` is declared `ON DELETE CASCADE`, so deleting a `shelf_items` row removes its chunks, and that only works because `open_db()` turns `foreign_keys` on for every connection.

Where the summary shapes live: `Summary`, `Critique`, `PaperSummary` and `Claim` are defined in `summary_models.py`, a small module with no imports from the pipeline, so `db.py` (storage) does not depend on `summarize.py` (the stage that uses the storage). `summarize.py` re-imports them, so `from research_pipeline.summarize import Claim` still works.

### Upsert, dedupe and version rules

`upsert_paper(conn, paper)` is called for every paper a source returns, and returns `"inserted"`, `"updated"` or `"unchanged"`.

1. **Unknown id**: insert with status `new`, index title and abstract. Returns `inserted`.
2. **Known id, same or lower version**: nothing about the paper's text or status changes. Only two things merge: `hf_upvotes` keeps the maximum, and `repos` becomes the union. If either grew, `updated`, otherwise `unchanged`.
3. **Known id, higher version**: title, abstract, authors, url, version and published are replaced, upvotes and repos merge, and the search index entry is rewritten. Everything derived from the old text is thrown away: the stored `fulltexts` row and both `summaries` rows are deleted, and `retry_count` and `error` are reset to 0 and empty, so the new version is fetched and summarised afresh (and a paper that had given up on the old version gets its tries back). This applies whatever the status. Status: `in_digest`, `summarized`, `fetched` and `triaged` become `triaged`; `new` stays `new`; `skipped` stays `skipped`; `failed` is left as is. Returns `updated`.

Worked example (paper `2401.00001`):

| Step | Incoming | Stored result | Returns |
| --- | --- | --- | --- |
| 1 | v1, 3 upvotes | inserted, status `new`, 3 upvotes | `inserted` |
| 2 | v1 again, 3 upvotes (next night) | nothing changes | `unchanged` |
| 3 | v1, 10 upvotes | upvotes 10, status `new` | `updated` |
| 4 | (triage keeps it, digest lists it) | status `in_digest` | n/a |
| 5 | v2 arrives | new title/abstract, version 2, status `triaged`; old full text, summaries and failure count cleared | `updated` |

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
- **Version number + migration ladder**: tables used to be created up front on the assumption that no migration would ever be needed. Phase 3 proved that wrong (`saves`), so `user_version` records which schema a file has and `_migrate()` upgrades old vaults in place. Rejected: a migrations framework (Alembic), far too heavy for one file with a handful of tables.
- **Foreign keys on, plus explicit checks**: the `REFERENCES` clauses were decoration while enforcement was off, so a typo'd id could be stored silently. The pragma is the safety net for every writer that goes through `rpipe` (the Datasette connection behind `rpipe serve` does not set it, see `browsing.md`, which is why the web forms check ids themselves); the explicit check in `record_vote`/`set_saved` gives a clear message instead of a raw `IntegrityError`. Rejected: relying on the pragma alone (cryptic error), or checks alone (a future writer could forget).
- **Refuse a newer database, never downgrade**: an old `rpipe` cannot know what a newer schema changed, so touching the file could corrupt it. It stops and says to upgrade.
- **A request list instead of the app running the pipeline**: the app stays free of model code and never fights the nightly run for the lock; the pipeline does the work when it runs. Rejected: the app calling the summariser directly (it would need the model stack and the lock).
- **Dedupe on canonical JSON, only while pending or running**: sorting the keys makes "the same request" a plain text comparison. Finished requests do not count, so you can ask for a paper again (for example after a failure). Rejected: a unique index (it would also block re-asking after `done`).
- **Votes are append-only**: keeping the history lets later ranking use how your taste changed, and an undo is just another row.

## How to run / check the database

`uv run pytest tests/test_db.py tests/test_feedback_db.py tests/test_library.py tests/test_requests.py tests/test_requests_queue.py tests/test_ids.py -v`

To see a vault's request list: `rpipe requests list` (or `sqlite3 <vault>/vault.sqlite 'SELECT id, kind, payload, status, tries FROM requests'`).

To see a vault's schema version: `sqlite3 <vault>/vault.sqlite 'PRAGMA user_version'`.

## Where the database code is

`src/research_pipeline/db.py` (schema, `REQUEST_KINDS`, `insert_request`, `requests_by_status`), `src/research_pipeline/library.py`, `src/research_pipeline/library_api.py` (the app's door into the database: `request`, and the reads and writes described in [architecture.md](architecture.md)), `src/research_pipeline/ids.py` (`normalise_paper_id`), `src/research_pipeline/summary_models.py`, `tests/test_db.py`, `tests/test_feedback_db.py`, `tests/test_library.py`, `tests/test_requests.py`, `tests/test_ids.py`
