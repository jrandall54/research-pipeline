# Architecture: the pipeline, the app and the door between them

## What it does

rpipe does two kinds of work over one database file (`vault.sqlite`):

- **The pipeline** gets material. It collects papers every night, decides which are relevant, fetches and summarizes them and writes the digest. It runs unattended, in batches, under a lock.
- **The app** uses material. Today that is the terminal commands you read and vote with (`rpipe show`, `vote`, `save`, `review`, `feedback`, `shelf`) and the browser view (`rpipe serve`). Later it is a real app: notes, quizzes, study material, help with a thesis.

The code is split the same way: every module belongs to one group, and tests fail when a module talks to a group it should not. This page explains the groups, the rules between them, and the three things that join them: the **one door** the app uses (`library_api`), the **request list** the app uses to ask the pipeline for work, and the **lock** that keeps pipeline jobs from overlapping.

## How it works

### The picture

```
                     THE APP (what you interact with)
   browse processed papers · vote · notes · quizzes/study · research · thesis
                                   │
                         library_api (one door)
                                   │
          ┌──────────── vault.sqlite (one database) ──────────────────┐
          │ papers, summaries, fulltexts, runs ← written by pipeline  │
          │ votes, saves, skips, shelf, notes  ← written by the app   │
          │ requests                           ← app adds, pipeline works │
          └────────────────────────────────────────────────────────────┘
                                   ▲
          THE PIPELINE (nightly and on demand): collect → triage → fetch → summarize → digest
```

`cli.py` sits above both halves: it is the **composition layer**, the one place allowed to use both groups and wire them together (for example, `rpipe run` syncs the shelf, an app job, and then runs the pipeline stages).

### The groups

No files were moved; the groups are lists in `tests/test_architecture.py`, checked on every test run. Each module (`x.py`) and package (a folder of modules: `sources/`, `backends/`, `defaults/`, `workbench/`) in `src/research_pipeline` is in exactly one group.

| Group | Modules | In plain words | May import |
| --- | --- | --- | --- |
| **Shared** | `models`, `summary_models`, `db`, `vault`, `embed`, `llm`, `backends`, `defaults`, `seeds`, `profile_edit`, `ids`, `progress`, `categories`, `schedule`, `netguard`, `feed_format` (the files of a published feed: building, parsing, reason parsing) | The building blocks both halves need: data shapes, the database connection and schema, the vault folders, the embedding and language-model clients, the profile and config editors, paper-id clean-up, progress events, the host allowlist for servers with no login | shared only |
| **Pipeline** | `collect`, `sources`, `triage`, `rank`, `fulltext`, `summarize`, `prompts`, `digest`, `evaluate`, `gold`, `judge`, `pruning`, `parts`, `citations` (also `section_texts`, the text of cited sections), `numbers` (checks that numbers in a hook appear in the cited text), `extras` (drops a hook or key number that fails that check), `diagram` (the diagram call and its checks), `spill`, `ollama_fit`, `ollama_tune`, `bench`, `scan`, `catalog`, `recommender`, `machine`, `registry`, `run_lock`, `requests_queue`, `seed_input`, `onboarding`, `share_github` (the `git`/`gh` operations behind a fakeable runner), `share_publish` (build the feed from the database and push it), `share_fetch` (follow feeds and download them safely), `share_directory` (the directory of published feeds found by GitHub search) | Getting and processing papers, plus everything about choosing, fitting and tuning the model that does it, the lock, and draining the request list | shared, plus the read functions of `library` listed in `library.PIPELINE_READS` |
| **App** | `library`, `shelf`, `review`, `serve`, `library_api`, `workbench`, `webapp` | What you do with the papers: votes, saves, skips, the shelf, the review queue, the browser view, the one door, and `workbench/` (an empty package waiting for the app's future features) and `webapp/` (the web app server, see [app.md](app.md)) | shared and app; never pipeline |
| **Composition** | `cli`, `progress_render` | The `rpipe` commands, and turning progress events into terminal lines | anything |

Placements that are not obvious:

- `schedule` (`rpipe schedule`, `set-deadline`) is shared: it imports nothing from the package, `profile_edit` uses its time parser, and its settings are ones an app settings page would show. `categories` (the arXiv category list) is shared for the same reason.
- `llm` mentions `bench` only inside `if TYPE_CHECKING:`, a block that exists for type checkers and never runs. The import check skips such blocks, so `llm` stays shared.
- `registry` (which backends and sources are installed) imports `ollama_fit` and `sources`, so it is pipeline.
- `db` is shared, but it still holds the pipeline's own writes (`save_summary`, `start_run`, ...). Splitting `db.py` is left for later; see "Known side doors".

The rules, each one a test:

1. Every module is in exactly one group. Adding `src/research_pipeline/newthing.py` without listing it fails a test, so nobody forgets to decide.
2. Shared modules import only shared modules. Otherwise "shared" would quietly drag one half into the other.
3. Pipeline modules never import app modules, except `library`, and from `library` only the names in `PIPELINE_READS` (`current_votes`, `list_saved`, `is_saved`, `shelf_chunk_rows`, `shelf_embedding_id`). Ranking needs your votes and shelf; it may read them, never change them. Nor do they import the composition modules (`cli`, `progress_render`): those sit on top and wire the halves together, so nothing below may reach up into them.
4. App modules never import pipeline modules, nor the composition modules (`cli`, `progress_render`). Heavy work goes through the request list instead.
5. `library_api` never loads, even indirectly, `llm` or `backends`, so nothing in the app's door can send your library to a model.
6. Every public `library_api` function returns data, never a `str` of display text.

An "import" here means any `import` or `from ... import` line in the module's files, including ones inside functions (they still run when the function is called), relative ones (`from .rank import x`) and `research_pipeline.x.y` (counted as `x`). Lines inside `if TYPE_CHECKING:` are skipped.

Worked example: suppose `digest.py` gained `from research_pipeline.review import review_queue` to show the queue size. Rule 3 fails with `digest imports ['review']`. The fix is not to loosen the test: either the number moves into something the pipeline may read, or the composition layer (`cli.py`) fetches it and hands it in.

### The one door

The app reads and changes the database only through `library_api.py`, which returns data objects rather than printed text. Details, the objects and a worked example are in "The app's door in detail" below.

### The request list

The app never runs a model. To get work done (today: "summarize this paper", asked with `rpipe requests add PAPER`), it calls `library_api.request(...)`, which adds a `pending` row to the `requests` table. The pipeline does the work: every night inside `rpipe run`, or at once with `rpipe requests run`. See "The request list" in [data-model.md](data-model.md) (the table) and in [running.md](running.md) (how it is worked through).

### The lock

Two pipeline jobs never run at once on one vault. Every pipeline command takes a lock on `<vault>/.run.lock`; a second one exits straight away with code 75, naming the command that holds it. The quick app commands (`vote`, `save`, `review`, `show`, `shelf list`, ...) do not take it: voting while the nightly run works is fine, because each of those changes is one short database transaction. `shelf add` and `shelf sync` do take it, because they load the embedding model and rewrite the shelf rows that `rpipe run` also syncs. See "One pipeline job at a time" in [running.md](running.md).

### Known side doors

Places that still cross the line, written down on purpose:

- **`db.py` holds pipeline writes.** `db` is shared, but functions such as `save_summary`, `start_run` and `mark_request` are writes only the pipeline should make. No test stops an app module from calling them; `test_app_commands_use_only_library_api` covers the app commands in `cli.py` only. Moving them into a pipeline module is later work.
- **Datasette's canned write queries.** `rpipe serve` (Datasette) lets you vote and save from the browser with its own SQL (`serve.py`), not through `library_api`. The app (`rpipe app`) now has its own web view through `library_api`; `rpipe serve` stays for raw database browsing until it is retired; see [browsing.md](browsing.md).
- **`_resolve_paper` in `cli.py`** runs its own SQL to turn an id or a title fragment into a paper id, and asks you to pick when several titles match. Picking from a numbered list is a terminal feature, so it stays with the terminal.
- **`_refresh_hint` in `cli.py`** (used by `rpipe summarize`) reads stored summaries with its own SQL to suggest a refresh. It belongs to a pipeline command, not the app.
- **`test_app_commands_use_only_library_api` is a denylist.** It reads the bodies of the listed app command functions in `cli.py` and fails on a direct call to a database or pipeline function (by name, or by a write-looking name such as `record_...` or `save_...`). It does not follow calls into helper functions, does not see new commands until they are added to its list, and does not look at other modules. It catches the common slip, not every possible one.

## Why it's built this way

- **One database, split by function.** The pipeline and the app share `vault.sqlite`; the split is in the code. Rejected: two database files (`vault.sqlite` for the pipeline, `library.sqlite` for the app). That makes ownership physical, but costs a data migration, loses links between tables in different files, and needs workarounds for Datasette and read-only access. It protects against one half writing the other's data, a problem a single-person local app does not have.
- **Declared and tested, not moved.** Moving dozens of files into `pipeline/` and `app/` folders would rewrite every import, the docs and the history for no change in behaviour. A list plus a test gives the same guarantee and fails the moment someone breaks it.
- **Rules checked by reading code, not by running it.** The tests read each file with Python's `ast` module (it turns source code into a tree of statements) and look at its import lines. They run in a fraction of a second, need no model or network, and see imports inside functions that a run might never reach.
- **One door, returning data, not text.** A future web page, desktop app or MCP tool should behave exactly like the terminal commands, so all of them call the same functions. If those functions returned printed text, every new front end would have to pick it apart again. Rejected: each front end reaching into `db.py` and friends itself (their choices would drift apart).
- **The app asks; only the pipeline works.** One engine fetches and summarizes, under the lock and with the GPU to itself. Rejected: the app running the model directly (a second code path, and two jobs fighting over one GPU).
- **The library stays local.** Your votes, notes and shelf never go to a cloud model unless you opt in. Keeping `llm` and `backends` out of `library_api`'s imports makes that true by construction for now. When the first app feature needs a model, it must check the "local only" setting (as the shelf does) and replace that test.

## How to check it

```
uv run pytest tests/test_architecture.py -q
```

Nine tests, all offline: every module classified; shared imports only shared; the pipeline never imports the app; the pipeline imports only the listed `library` reads; the app never imports the pipeline; `library_api` is local only; `library_api` returns data, not text; `if TYPE_CHECKING:` lines are ignored; and app commands use only `library_api`.

When you add a module, put its name in one of the four sets at the top of the boundary tests in `tests/test_architecture.py` and in the table above.

To see the request list of a vault: `rpipe requests list`.

## Where the code is

- `tests/test_architecture.py`: the four groups (`SHARED`, `PIPELINE`, `APP`, `COMPOSITION`), the import reader (`_internal_imports`) and every rule above.
- `src/research_pipeline/library.py`: `PIPELINE_READS`, the `library` functions the pipeline may use.
- `src/research_pipeline/library_api.py`: the one door.
- `src/research_pipeline/requests_queue.py`: the pipeline side of the request list.
- `src/research_pipeline/run_lock.py`: the lock.
- `src/research_pipeline/workbench/`: empty home for future app features.
- `src/research_pipeline/webapp/`: the web app server, templates and static files ([app.md](app.md)).
- `src/research_pipeline/cli.py`: the composition layer.

## The app's door in detail

### What it does

rpipe has two kinds of commands. The **pipeline** gets material on its own, usually at night (`rpipe run`, `collect`, `triage`, `summarize`, `digest`). The **app** is what you do with that material: read it, vote, save, review, keep notes on the shelf. Both use the same database file, `vault.sqlite`.

`src/research_pipeline/library_api.py` is the app's one door into that database. Every app command (`rpipe vote`, `save`, `unsave`, `feedback`, `review`, `show`, and `shelf add|list|remove|sync`) asks `library_api` for data or asks it to make a change, then prints the answer. A future web page, desktop app or MCP tool (a way for AI agents to call our functions) is meant to be built on the same functions, so it behaves exactly like the terminal commands.

### How it works

`library_api` hands back **data objects**, never text laid out for a terminal. A data object is a Pydantic class: a fixed set of named fields with types, which turns into JSON with one call (`.model_dump_json()`).

| Object | Fields | Returned by |
| --- | --- | --- |
| `PaperView` | `id`, `title`, `abstract`, `authors`, `url`, `published`, `status`, `digest_date`, `triage_score`, `triage_reasons` (why triage kept it), `summary` (the stored `PaperSummary`, or none), `vote` (-1, 0 or 1; 0 = none or cleared), `saved`, `diagram` (the stored `Diagram`, or none) | `get_paper`, `digest_papers`, `recent_papers`, `saved_papers`, `search_papers`, `review_queue`, `later_queue` |
| `VoteView` | `paper_id`, `title`, `vote` (-1 or 1), `voted_at` | `list_votes` |
| `QueueProgress` | `handled`, `total` | `queue_progress` |
| `ShelfItemView` | `name`, `added_at`, `chunks` | `shelf_items`, `shelf_add` |
| `SyncResult` | `added`, `updated`, `removed`, `ignored` (lists of names) | `shelf_sync` |

The changes it makes: `vote(conn, id, "up" | "down" | "clear")`, `save(conn, id, True | False)`, `skip` and `unskip` (the review-later list), `shelf_add`, `shelf_remove`, `shelf_sync`, and `request` (ask the pipeline to do work later; see "The request list" in [data-model.md](data-model.md); the pipeline side, `requests_queue.drain_requests`, does the work in the nightly `rpipe run` or `rpipe requests run`, see "The request list" in [running.md](running.md)). Each change is one short transaction and commits. A paper id that is not in the database raises `UnknownPaperError`. One plain read goes with `request`: `open_request_ids(conn)` returns the ids (a set of numbers) of requests still waiting or being worked on, so `rpipe requests add` can tell "queued now" from "already waiting" (a repeat `request` returns the existing id).

Added for the web app's feed page (all plain reads, no writes):

- `plain_reasons(reasons)` turns the stored triage strings (`topic: agentic workflow (0.80)`, `liked similar: 2611.25905`, `HF upvotes: 56`, `followed author: ...`, `keyword: ...`, `near your shelf: ...`) into `ReasonView` objects (`kind`, `label`, `paper_id`) the page can word in plain language. It keeps the order and drops `disliked similar: ...` and anything it does not recognise.
- `digest_dates(conn, limit=60)` lists the distinct digest dates, newest first, for the "Earlier days" list.
- `summarize_states(conn, paper_ids)` gives the status (`pending`, `running`, `done`, `failed`) of the newest `summarize` request for each paper; a paper nobody asked about is absent.
- `summary_provenance(conn, paper_id)` returns a `ProvenanceView` (`model`, `created_at`) saying which model wrote the stored summary and when, or none when there is no summary or it predates provenance. The page shows it under "About this summary".

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

The places that still reach the database without `library_api` are listed under "Known side doors" above.

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
