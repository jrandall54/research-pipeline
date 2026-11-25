# Architecture: the pipeline, the app and the door between them

> This page is being built in steps. This version covers **the app's door** (`library_api.py`). The split into a pipeline group and an app group, the import rules and the full list of side doors are added in a later step.

## The app's door

### What it does

rpipe has two kinds of commands. The **pipeline** gets material on its own, usually at night (`rpipe run`, `collect`, `triage`, `summarize`, `digest`). The **app** is what you do with that material: read it, vote, save, review, keep notes on the shelf. Both use the same database file, `vault.sqlite`.

`src/research_pipeline/library_api.py` is the app's one door into that database. Every app command (`rpipe vote`, `save`, `unsave`, `feedback`, `review`, `show`, and `shelf add|list|remove|sync`) asks `library_api` for data or asks it to make a change, then prints the answer. A future web page, desktop app or MCP tool (a way for AI agents to call our functions) is meant to be built on the same functions, so it behaves exactly like the terminal commands.

### How it works

`library_api` hands back **data objects**, never text laid out for a terminal. A data object is a Pydantic class: a fixed set of named fields with types, which turns into JSON with one call (`.model_dump_json()`).

| Object | Fields | Returned by |
| --- | --- | --- |
| `PaperView` | `id`, `title`, `abstract`, `authors`, `url`, `published`, `status`, `digest_date`, `triage_score`, `triage_reasons` (why triage kept it), `summary` (the stored `PaperSummary`, or none), `vote` (-1, 0 or 1; 0 = none or cleared), `saved` | `get_paper`, `digest_papers`, `recent_papers`, `saved_papers`, `search_papers`, `review_queue`, `later_queue` |
| `VoteView` | `paper_id`, `title`, `vote` (-1 or 1), `voted_at` | `list_votes` |
| `QueueProgress` | `handled`, `total` | `queue_progress` |
| `ShelfItemView` | `name`, `added_at`, `chunks` | `shelf_items`, `shelf_add` |
| `SyncResult` | `added`, `updated`, `removed`, `ignored` (lists of names) | `shelf_sync` |

The changes it makes: `vote(conn, id, "up" | "down" | "clear")`, `save(conn, id, True | False)`, `skip` and `unskip` (the review-later list), `shelf_add`, `shelf_remove`, `shelf_sync`, and `request` (ask the pipeline to do work later; see "The request list" in [data-model.md](data-model.md)). Each change is one short transaction and commits. A paper id that is not in the database raises `UnknownPaperError`.

`library_api` does not hold rules of its own about votes or the shelf. It passes each change on to the module that already owns it: `library.py` for votes, saves and skips, `shelf.py` for the shelf, `review.py` for the queue queries, `db.py` for papers and summaries.

Worked example, `rpipe show 2611.25005`:

```
cli.show
  -> _resolve_paper(conn, "2611.25005")        terminal feature: id, then title search + picker
  -> library_api.get_paper(conn, "2611.25005") -> PaperView(title=..., triage_reasons=["close to seed"],
                                                            summary=PaperSummary(read_mode="split", parts=3, ...), ...)
  -> _show_paper(view, "Paper 2611.25005")     prints title, "id: ...", "why: close to seed",
                                               the summary (render_summary), "read in parts: split, 3 notes calls", url
```

The same `PaperView`, turned into JSON, is what a web page would receive:

```json
{"id": "2611.25005", "title": "Fuzzing eBPF Verifiers", "vote": 1, "saved": true,
 "triage_reasons": ["close to seed"], "summary": {"read_mode": "split", "parts": 3, "...": "..."}, "...": "..."}
```

**Searching.** `search_papers(conn, query, limit=20)` uses SQLite's full-text index (FTS5, see the glossary). Only the words of the query are used, each wrapped in quotes: `fuzz* OR (` becomes `"fuzz" "OR"`. So search syntax typed by a person is treated as plain words and can never make the query fail; a query with no words returns an empty list. Results are ordered best match first (FTS5's `rank`).

### Why it's built this way

- **Data, not text.** If `library_api` returned ready-printed lines, a web page would have to pick them apart again. Returning objects lets each front end lay out the same facts its own way; the terminal layout lives only in `cli.py`. Rejected: moving the print code into `library_api` (ties every future app to the terminal look).
- **One door, not many.** If each command reached into `db.py`, `library.py` and `review.py` itself, a new front end would have to copy those choices and they would drift apart. One module means one place to read, test and change. A test (`tests/test_architecture.py::test_app_commands_use_only_library_api`) reads `cli.py` and fails if an app command calls a database or pipeline function directly.
- **A thin layer.** `library_api` passes changes on to the existing functions instead of repeating their SQL, so the rules (for example "a vote of 0 clears") still live in one place.
- **Light imports.** `library_api` loads only `db`, `library`, `ids`, `review`, `shelf` and the data shapes. It never loads the language-model code, the profile editor or the pipeline stages, so an app built on it starts fast, works without a model, and cannot send your library to a model by accident.
- **The shelf's file work stays in `shelf.py`.** `shelf_add`, `shelf_remove` and `shelf_sync` copy, move and index files in `my_work/`; `library_api` only wraps them and returns data. Loading the embedding model and showing progress stay in `cli.py`, which hands the model in.

**Side doors that remain** (known, written down on purpose):

- `_resolve_paper` in `cli.py` runs its own SQL: it turns an id or a title fragment into a paper id, and asks you to pick from a numbered list when several titles match. Picking from a list is a terminal feature, so it stays with the terminal.
- `_refresh_hint` in `cli.py` (used by `rpipe summarize`) reads stored summaries with its own SQL to suggest a refresh. It belongs to a pipeline command, not the app.
- Datasette (`rpipe serve`) writes votes and saves with its own SQL, bypassing `library_api`. It is a stopgap until the app has its own web view; see [browsing.md](browsing.md).

### How to run / check it

```
uv run pytest tests/test_library_api.py tests/test_architecture.py -q
```

From Python, against a copy of your vault (`cp ~/research-vault/vault.sqlite /tmp/vault-copy.sqlite` first, so trying things out never changes the real one), run with `uv run python`:

```python
from pathlib import Path
from research_pipeline import library_api
from research_pipeline.db import open_db

conn = open_db(Path("/tmp/vault-copy.sqlite"))
for p in library_api.digest_papers(conn):          # the latest digest, best first
    print(p.id, p.title, p.vote, p.saved)
print(library_api.get_paper(conn, "2611.25005").model_dump_json(indent=2))
```

### Where the code is

- `src/research_pipeline/library_api.py`: the data objects and every function above.
- `src/research_pipeline/cli.py`: the app commands (`vote`, `_save_command`, `feedback`, `review`, `show`, `_show_paper`, `shelf_add`, `shelf_list`, `shelf_remove`, `shelf_sync`), which call `library_api` and print.
- Tests: `tests/test_library_api.py`, `tests/test_architecture.py`, and the CLI tests (`tests/test_cli_feedback.py`, `test_cli_review.py`, `test_cli_show.py`, `test_cli_shelf.py`), which prove the terminal output did not change.
