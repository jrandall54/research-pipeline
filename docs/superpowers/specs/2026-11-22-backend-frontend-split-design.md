# Pipeline and app: one database, split by function

Date: 2026-11-22. Status: draft for owner approval.

## Intent and why

The project does two kinds of work:

- **The pipeline** gets material: it collects papers every night, decides what is relevant, and fetches and summarizes it. It runs unattended, on a schedule, in batches.
- **The app** uses material: it shows the processed papers, takes votes and notes, makes quizzes and study material, and later helps collect research and write a thesis by comparing what was collected with the owner's own library (shelf) of interests and work.

The owner has a philosophy BA, is finishing CS prerequisites, and is applying for a master's starting fall 2027. Their goal in the owner's words: an app they interact with that shows the papers the pipeline processed, lets them vote, take notes and create quizzes and study materials, and later helps them collect research and produce a thesis from everything collected, compared with their personal collection.

Today the two kinds of work are mixed together in the code. Reading means a markdown file in a terminal. `rpipe summarize` can run while the nightly run is writing, because only `rpipe run` takes the lock. And the shelf is a side feature of ranking rather than the home of the owner's research.

This document fixes how the code is organized so the app can be built on the pipeline cleanly. It does **not** design the app's features or screens; each gets its own design later.

## Goals

- **Split by function, not by storage.** Pipeline code and app code are separate modules over one database. A test enforces the boundary.
- **One engine.** Only the pipeline fetches, converts and batch-processes material. The app asks for that work and never builds a second pipeline.
- **No collisions.** Two pipeline jobs never run at once on one vault.
- **One door for the app.** The app reaches data through one module (`library_api`), so a web page, an MCP tool or a later service all use the same functions.
- Everything stays explainable (`docs/guide/`) and settable through `rpipe` (CLAUDE.md).

## Non-goals (for this work)

- Building the app, its screens or choosing its medium.
- An always-running service or HTTP API. `library_api` keeps that possible later.
- Multi-user vaults.
- Changing how collect, triage, fetch or summarize work.
- Moving any data or changing the database file.

## Anti-goals (failure even if it technically works)

- Two code paths that fetch or summarize material.
- A structure that adds complexity without a problem it solves. The owner asked for this cut explicitly: the earlier draft with two database files was dropped for this reason.
- Library or notes content going to a cloud model without the owner opting in. The existing `my_work/` local-only rule stands.
- A layout that only the builder understands. The split must be explainable on one page.

## Constraints

- CLAUDE.md: docs in the same commit as code; no documented workflow may require hand-editing files; nothing personal in the repo; tests offline (`uv run pytest -q`).
- The owner's real vault (`vault.sqlite`) is used as is: no schema migration in this work except the new `requests` table, added the usual way (`CREATE TABLE IF NOT EXISTS` plus a `user_version` step in `db.py`).
- Branches `phase-5-shareable` and `summarize-backlog` are unmerged. This work starts after they are merged to `main`.

## Approaches

| | Approach | Trade-off | Verdict |
|---|---|---|---|
| **A1** | One project, one database, code split by function; the app uses `library_api`. | Boundary is a code rule (enforced by tests), not a storage wall. Simplest. | **Chosen** by the owner. |
| A2 | Same, but two database files (`vault.sqlite` for the pipeline, `library.sqlite` for the app) so ownership is physical. | Protects against one half writing the other's data, at the cost of a data migration, no foreign keys across files, read-only attach quirks and Datasette workarounds. | Drafted, then dropped: solves a problem a single-user local app does not have. |
| B | Pipeline runs as a service with an HTTP API; the app only talks to the API. | Textbook split, easy multi-device; always-running process, much more code. | Later, if needed. A1 grows into it by wrapping `library_api`. |
| C | Two separate apps exchanging files (e.g. into Obsidian). | Two copies of data, sync problems, features rebuilt on the other side. | Dropped. |

## The design (A1)

```
                     THE APP (what the owner interacts with)
   browse processed papers · vote · notes · quizzes/study · research · thesis
                                   │
                         library_api (one door)
                                   │
          ┌──────────── vault.sqlite (one database) ─────────────┐
          │ papers, summaries, fulltexts, runs ← written by pipeline │
          │ votes, saves, skips, shelf, notes  ← written by the app  │
          │ requests                           ← app adds, pipeline works │
          └───────────────────────────────────────────────────────────┘
                                   ▲
          THE PIPELINE (nightly and on demand): collect → triage → fetch → summarize → digest
```

### Which code is which

Existing modules stay where they are; the split is declared and tested, not done by moving files (my call: moving dozens of files churns history and docs for no behaviour change).

| Group | Modules | May import |
|---|---|---|
| **Shared** | `models`, `summary_models`, `db` (connection, schema, timestamps), `vault`, config and profile loading, `embed`, `llm`, `backends`, `registry` | shared only |
| **Pipeline** | `collect`, `sources`, `triage`, `rank`, `fulltext`, `summarize`, `prompts`, `digest`, `evaluate`, `gold`, `schedule`, `ollama_fit`, `machine`, `run_lock`, `requests_queue` (new) | shared, plus the read functions of `library` |
| **App** | `library` (new: user-action reads and writes moved out of `db.py`), `shelf`, `review`, `serve`, `library_api` (new), `workbench/` (new, empty package for future features) | shared, `library`, `library_api`; **not** pipeline stage modules |

- **`cli.py` is the composition layer.** It may import both groups and wire them together (for example, `rpipe run` syncs the shelf and then runs the pipeline stages). The rules above apply to every other module.
- **`library.py`** holds the user-action functions now in `db.py`: `record_vote`, `current_votes`, `set_saved`, `list_saved`, `record_skip`, `clear_skip` and the `shelf_*` functions. The pipeline uses only its read functions (`current_votes`, saved ids, `shelf_chunk_rows`) for ranking.
- **Shelf sync moves out of `triage.py`.** Today triage calls `sync_shelf` so the index follows your files even on quiet nights. `rpipe run` and `rpipe triage` (composition layer) now call it just before triage, so triage itself only reads.
- **Enforced by tests** that inspect each module's imports, like the existing `test_shelf_is_local_only`. Pipeline modules may not import `library_api`, `review`, `serve` or `workbench`, and may import from `library` only its listed read functions. App modules may not import pipeline stage modules.

### `library_api`: the app's one door

A module of plain Python functions. The first version covers what today's app-side commands need:

- read: get a paper with its summary and critique, list a digest's papers, list saved papers, search papers (the existing full-text index), list shelf items;
- write: vote, save and unsave, skip, shelf add and remove (delegating to `library`);
- ask the pipeline for work: `request(kind, payload)`.

`vote`, `save`, `unsave`, `feedback`, `review` and `shelf` route through it. Exact names and signatures are left to the builder. The app's future features (notes, quizzes, concept tree, citations) add functions here, and each adds its own tables to `vault.sqlite` owned by the app group.

### The request list

The app asks the pipeline for heavy work by adding a row; the pipeline does it. This keeps one engine and puts slow model work under the pipeline's lock.

Table `requests` in `vault.sqlite`:

| Column | Meaning |
|---|---|
| `id` | integer primary key |
| `kind` | `summarize` (a known paper id) now; later `fetch_url` (an article or repo link), `deep_analysis` (study material), `lit_search` (thesis stage) |
| `payload` | JSON arguments, e.g. `{"paper_id": "2611.25005"}` |
| `status` | `pending` → `running` → `done` or `failed` |
| `tries`, `error`, `result` | set by the pipeline |
| `created_at`, `updated_at` | timestamps in `db.py` format |

Rules:
- `request()` checks its payload first. A `summarize` request for an unknown paper is refused with `UnknownPaperError`.
- **Duplicates.** If the same kind and payload is already `pending` or `running`, `request()` returns that row's id and adds nothing.
- **A paper that already has a summary** is summarized again, the same as `rpipe summarize <id>` today. Asking is an explicit choice.
- **Who runs requests.** The pipeline works through pending requests at the end of `rpipe run`, and whenever the owner runs `rpipe requests run`. `rpipe requests list` shows the list. Both commands are new.
- **Crash recovery.** A run first resets any `running` rows to `pending`. Only one run can hold the lock, so a leftover `running` row belongs to a process that died.
- **Retries.** A failure is retried on later runs up to 3 tries (`MAX_SUMMARY_TRIES`), then the request stays `failed` with its error.
- **Usage limits.** `LlmLimitReached` is handled as in summarize: the remaining requests stay `pending`.
- **Timing.** Whether the app runs a request right away in the background or waits for the night is the app's user-experience decision, made in its own design.

Only `summarize` is built now, so the request list is proven end to end. The other kinds are named so the table does not change later.

### One lock for every pipeline job

`run_lock` covers only `rpipe run` today. After this work every command that runs pipeline work takes it: `run`, `collect`, `triage`, `summarize`, `digest`, `eval run`, `model fit`, `requests run`, and `gold add` (it fetches). A second job stops at once with the existing message and exit code 75. The lock file records which command holds it, and the message names that command.

**One workflow changes.** Running `rpipe summarize` by hand while the nightly run is working now stops at once instead of running alongside it. The nightly run starts at 03:00, so this is rare.

App commands (`vote`, `save`, `review`, `shelf`, `serve`) take no lock. Their writes are single short transactions. The database is put in WAL mode with a 5-second busy timeout (my call), so an app write during a nightly run waits a moment rather than failing. A WAL database lets readers and one writer work at the same time.

### Ready for a real app later

The owner wants the eventual app to look good and be easy to use, and it will look nothing like Datasette. No decisions about the app's look, technology or screens are made now. These rules keep any future front end possible:

1. **`library_api` returns data, never formatted text.** Its functions return Pydantic objects (which turn into JSON directly), never strings laid out for a terminal. The `rpipe` commands format those objects for the terminal; a future app formats the same objects however it likes. Test: no `library_api` function returns a `str` built for display.
2. **Long work reports progress as data.** Requests carry a status, and pipeline jobs report progress through a callback (the project-wide progress requirement in the handoff). A terminal shows that as a spinner; an app can show a progress bar from the same events.
3. **Everything the app shows has a stable id.** Papers, summaries, votes and shelf items already do; future notes, quizzes and library items must too, so an app can link to and update them.
4. **Datasette is a stopgap.** `rpipe serve` stays as it is for browsing and debugging, with no further work on it. It is not the app and nothing is designed around it.
5. **The likely shape later** (not decided, written down so this work does not block it): a local web app whose server is a thin layer over `library_api`. The browser part can then be built with any modern UI toolkit and reached from a phone over Tailscale, like `rpipe serve` today. A desktop or phone app would sit on the same layer.

### Privacy

Library and shelf content goes only to a local model unless the owner opts in per use. `require_local` exists but is not yet wired. It gets wired in `library_api` at the first function that sends library content to a model. That function belongs to an app feature, not to this work; this work adds a test that `library_api` cannot reach a cloud backend without the opt-in flag.

## The app, at high level (not designed here)

Recorded so the structure above serves it. Each item becomes its own design later.

- **Browse and vote**: the processed papers with summary, critique and text, readable outside the terminal (the medium is undecided; `rpipe serve` is the likely start). Voting and saving happen in the same view.
- **Notes**: per paper and free-standing, stored in the library and embedded for search.
- **Capture**: add an article, repo or idea from wherever you find it; links become `fetch_url` requests. The shelf grows into one library of items (`paper`, `article`, `repo`, `idea`, `note`).
- **Study material** (the teacher's ideas): per-question templates with rating scales and 3 to 5 gold-standard examples, used in layered analysis (prerequisites, concepts, learning objectives, misconceptions). The results feed a **concept tree** linked to coursework, plus quizzes. Heavy layers run as `deep_analysis` requests.
- **Search, compare, ask**: keyword and semantic search over papers and the library; "what in my library is close to this?"; questions answered with citations to sections.
- **Thesis stage**: `lit_search` requests (arXiv and Semantic Scholar on a topic, plus citation chasing), a gap finder over limitations sections, and citation export (BibTeX).
- **Other doors**: an MCP server over `library_api` (the teacher's suggestion), so agents and humans share the same search.

## What this work delivers (scope for the plan)

1. `library.py` (user-action functions moved from `db.py`) and `library_api.py`; `vote`, `save`, `unsave`, `feedback`, `review` and `shelf` routed through them; shelf sync moved from `triage.py` to the composition layer.
2. The `requests` table (a `user_version` step), `requests_queue.py`, `rpipe requests run|list`, the `summarize` kind, and the drain at the end of `rpipe run`.
3. The run lock on every pipeline command, with the holder named in the message; WAL mode and a busy timeout.
4. Import-boundary tests, the privacy test for `library_api`, and the test that `library_api` returns data objects, not display strings.
5. Docs: a new `docs/guide/architecture.md` (the one-page explanation: the two groups, the one door, the request list, the lock), plus updates to `data-model.md`, `shelf.md`, `running.md`, `review.md` and `glossary.md` (pipeline, app, composition layer, request list, WAL).
6. The master plan (Claude Docs) updated with the two halves, an app section and a revised roadmap: finish the pipeline, then this split, then the app designs.

## Pipeline work that follows (separate plans, already in the handoff)

Merge the two open branches; verify the Ollama path for real; add progress feedback everywhere; handle long papers (bigger context first, chunked summaries second). These are pipeline-only. The one rule this design adds for them: any new pipeline command takes the lock.

## Left to the builder

- Exact `library_api` and `library` function names and signatures, beyond `request(kind, payload)`.
- The order of plan tasks, as long as `library.py` exists before commands are routed through `library_api`.
- How the lock file records its holder.
