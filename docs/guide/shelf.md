# The shelf: your own notes and writing

## What it does

The shelf is a folder of your own material (`my_work/` in the vault): rough ideas, notes, coursework, drafts. Files on it are split into small pieces, turned into embeddings (numbers capturing meaning) and stored so the pipeline can later ask "which new papers sit close to something I am working on?". You can add to it and remove from it at any time with `rpipe shelf`. This page covers indexing and the commands; how the shelf boosts rankings is in [triage.md](triage.md).

Everything stays on your machine. The shelf code uses only the local embedding model; it never calls the language model or the network, and a test (`test_shelf_is_local_only`) checks that `shelf.py` imports no HTTP library and no LLM client.

## How it works

```
 my_work/
   idea.md  notes.txt  paper.pdf  .removed/
        |
        v  sync_shelf(): look at .md and .txt files directly in my_work/
        |    - skip dotfiles, .removed/, subfolders; list other extensions as "ignored"
        |    - sha256 of the bytes unchanged AND vectors stored for this embedder? skip
        |    - else chunk_text() -> upsert_shelf_item() -> embed chunks -> embeddings table
        |    - item whose file is gone? delete its rows
        v
 shelf_items, shelf_chunks, embeddings (item_id "shelf:<name>#<n>")
```

The item **name** is the file name without its extension (`idea.md` is item `idea`).

**Chunking** (`chunk_text`). A chunk is a piece of a note small enough for the embedding model to read in one go (about 512 tokens for bge-small). The steps:

1. Split on markdown heading lines (`#`, `##`, ...). Each heading starts a new section.
2. Inside a section, split on blank lines into paragraphs.
3. Merge a paragraph that is still under `MIN_CHUNK_CHARS` (200) with the next one, while the result stays within the limit.
4. A paragraph longer than `MAX_CHUNK_CHARS` (1500) is split at sentence ends (`. `, `! `, `? `). A single "sentence" longer than the limit is cut hard.
5. Every chunk is prefixed with the item name and its nearest heading so a bare paragraph keeps its context.

Worked example. `kernel-fuzzing.md`:

```
# Idea
Use coverage feedback to fuzz drivers.

# Related work
(a 600-character paragraph)
```

gives two chunks (a short section is never merged across a heading):

```
kernel-fuzzing / Idea
Use coverage feedback to fuzz drivers.
```
```
kernel-fuzzing / Related work
(the 600-character paragraph)
```

A one-line note with no heading gives one chunk, `idea\nTry retrieval for malware triage.`. An empty or whitespace-only file is stored with zero chunks (it is still listed, and does not crash anything).

**Add** (`add_to_shelf`). If the argument is an existing `.md` or `.txt` file it is copied into `my_work/` under its own name; a clash gets `-2`, `-3` ... so nothing is overwritten. Names are compared ignoring case (`Idea.md` and `idea.md` clash, as they are the same file on macOS and Windows), and files are created in exclusive mode, so an existing file is never overwritten whatever the filesystem. Otherwise the argument is note text, saved as `my_work/<slug>.md` where the slug is the first six words, lowercased, letters, digits and hyphens only (`note` if nothing is left), made unique the same way. Empty text, or a file that is not `.md`/`.txt`, raises an error that says what to do. Then sync runs and the item name is returned.

**Remove** (`remove_from_shelf`). Every `.md`/`.txt` file with that name (so both `a.md` and `a.txt`, otherwise the leftover would come back as item `a` at the next sync) is moved to `my_work/.removed/` (renamed `-2` ... if that name is taken there, ignoring case, so an earlier removal is never overwritten); the names it now has are returned and printed. Then the item, its chunks and its vectors are deleted. Your file is never deleted; to bring it back, move it out of `.removed/` and run `rpipe shelf sync`. An unknown name raises an error listing the closest names (`difflib.get_close_matches`).

## Why it's built this way

- **Files are the source of truth, the database is an index.** You can open, back up or drop files into `my_work/` with any tool, and sync rebuilds the index. Rejected: storing notes only in SQLite (opaque, cannot be opened as ordinary files); rejected: a file-watcher daemon (needless, syncing before each run is enough).
- **Content hash, not modified time.** A changed file is detected by the sha256 of its bytes, so copying or touching a file does not cause re-embedding and an edit never goes unnoticed. The skip also requires vectors for the current embedder, so switching embedding model re-embeds the shelf by itself.
- **Edits replace, not append.** Re-indexing an item deletes its old chunks and vectors first, so stale text cannot keep influencing results (an anti-goal in the design).
- **Remove moves, never deletes.** `.removed/` makes a mistake recoverable.
- **Heading prefix on each chunk.** Without it a paragraph reading "It fails on long inputs." has no subject once separated from its note.
- **Commands go through the app's door.** `rpipe shelf add|list|remove|sync` call `library_api.shelf_add`, `shelf_items`, `shelf_remove` and `shelf_sync`, which wrap `shelf.py` and return data (`ShelfItemView`: name, added date, chunk count; `SyncResult`) for `cli.py` to print. A future app can manage the shelf with the same calls. Loading the embedding model and showing progress stay in `cli.py`. See "The app's door" in [architecture.md](architecture.md). `rpipe run` and `rpipe triage` sync the shelf the same way, with `library_api.shelf_sync`, just before triage; triage itself only reads the shelf (see [triage.md](triage.md)).
- **Plain size constants.** 200 and 1500 characters are rules of thumb; there is no setting for them, so there is nothing for a user to hand-edit.

## What never leaves your machine

Your shelf is personal, so no text derived from it may be sent to a backend that is not local (for example a subscription cloud model). Today that holds trivially: shelf text is only embedded by the local embedding model and compared with numbers; no shelf-derived text reaches any language model. The audit of every `.generate(` call site in `src/`:

| Call site | What it sends | Shelf text? |
| --- | --- | --- |
| `summarize._generate_checked` | a paper's sections (or abstract) and the fixed instructions | no |
| `onboarding.propose` | the interests description you typed | no |

Future code that would send shelf text to a model must first call `llm.require_local(llm, "your shelf")`, which raises `PrivacyError` (and sends nothing) unless the backend's `local` attribute is True. Re-run this audit (`grep -rn "generate(" src/`) whenever a new call site is added.

**The boundary test guards this, within limits.** `tests/test_boundaries.py` parses (with `ast`, Python's syntax-tree reader) every module that builds a prompt or calls a model (`prompts.py`, `llm.py`, `summarize.py`, `onboarding.py`, `evaluate.py` and everything in `backends/`) and fails if any of them statically imports `research_pipeline.shelf`, in the absolute forms (`import research_pipeline.shelf as s`, `from research_pipeline import shelf`, `from research_pipeline.shelf import X`) and the relative forms (`from . import shelf`, `from ..shelf import X`). A second check fails if the source text of those modules contains the strings `shelf_chunks` (the table that holds shelf text) or `my_work`. Planted-file tests put a forbidden import or name in a temporary file and check each checker flags it, so neither can pass by accident. **What it does not catch:** `import research_pipeline` followed by attribute access (`research_pipeline.shelf...`), dynamic imports (`importlib.import_module`), reading the shelf table through SQL built from a string that avoids the two names, or shelf text passed in by a caller. It is a tripwire for the obvious mistakes, not a proof; the call-site audit above is the real review. A third test checks that `progress.py` imports nothing from the package, because the planned app layer treats it as shared code. Ranking (`rank.py`, `triage.py`) is not checked on purpose: it reads shelf embeddings, which are numbers, not text.

When the first feature that needs shelf text in a prompt is written, it must call `llm.require_local` and update `tests/test_boundaries.py` in the same commit. Until then the test stays as it is and a failure means shelf text is reaching model code: remove the import, do not weaken the test.

## The commands

Nobody has to touch files by hand: four `rpipe shelf` commands cover everything.

- `rpipe shelf add <text-or-file>`: save a note (any text) or copy in a `.md`/`.txt` file, then index it.
- `rpipe shelf list`: show every item with its chunk count and the day it was last indexed (a re-index after an edit refreshes it).
- `rpipe shelf remove <name>`: move the item's file to `my_work/.removed/` and drop it from the index.
- `rpipe shelf sync`: re-index after you dropped, edited or deleted files in `my_work/` yourself (`rpipe run` and `rpipe triage` also do this just before triage).

Real output (fake embedder, fresh vault):

```
$ rpipe shelf list
  Your shelf is empty. Add a note or file with: rpipe shelf add "your idea" (or a .md/.txt path).

$ rpipe shelf add "Try retrieval for malware triage"
✓ Added 'try-retrieval-for-malware-triage' to your shelf. Papers close to it will now rank higher.

$ rpipe shelf add notes/idea.md
File not found: notes/idea.md. Check the path, or put the note in quotes with spaces, e.g. rpipe shelf add "my idea about fuzzing".

$ rpipe shelf sync          # after dropping gamma.md and paper.pdf into my_work/
✓ Shelf synced: 1 added, 0 updated, 0 removed.
  • Added: gamma
! Ignored 1 file(s): paper.pdf. Only .md and .txt files directly in my_work/ are indexed (if a .md and a .txt share a name, the .md is used).

$ rpipe shelf list

── Shelf: 2 items ──────────────────────────────────────────────────────────────
  gamma                             1 chunk  added 2026-11-22
  try-retrieval-for-malware-triage  1 chunk  added 2026-11-22

$ rpipe shelf remove gamma
✓ Removed 'gamma'. The file is in my_work/.removed/ if you want it back.
```

Two guards the command line adds on top of `add_to_shelf`:

- **Mistyped paths.** `add_to_shelf` treats anything that is not an existing file as note text. That is wrong for `notes/idea.md` with a typo in the folder: you would get a note whose text is the path. So the command first checks: if the argument is one word (no whitespace) ending `.md` or `.txt`, or names a directory, and is not an existing file, it prints "File not found" and exits with code 1. A note with spaces, like `rewrite the README.md`, is still a note.
- **Nothing skipped silently.** `sync` prints the `ignored` files (other extensions such as a PDF, or `a.txt` when `a.md` exists, because both would be item `a`) with a one-line reason. A file that cannot be read (permission denied, or deleted while syncing) is listed the same way, as `name (could not be read: ...)`; it is skipped and the rest of the shelf still syncs, so one bad file cannot stop the nightly run.

An unknown name for `remove` exits with code 1 and lists the closest names. `list` and `remove` do not load the embedding model; `add` and `sync` do.

## How to run / check it

- Progress (stderr). `rpipe shelf sync` first shows `embedding model <name>` while the embedding model loads, then a `shelf` stage with one step per new or edited file (`[1/2] a.md`, `[2/2] b.txt`); files that did not change are not listed, and when nothing changed the stage starts and finishes with no steps. The **first run downloads the embedding model** (hundreds of MB; about 130 MB for the default). That can take minutes, and the "still waiting" heartbeat (every 30 s) shows it is working. `rpipe shelf add` shows the model-loading stage only. `sync_shelf` takes an optional `reporter`; `rpipe run` and `rpipe triage` pass theirs down, so their `shelf` stage looks the same.

Use the commands above. Tests: `uv run pytest tests/test_shelf.py tests/test_cli_shelf.py -q` (offline, fake embedder).

## Where the code is

The commands are `shelf_app` in `src/research_pipeline/cli.py`; they reach the shelf only through `src/research_pipeline/library_api.py` (`shelf_add`, `shelf_items`, `shelf_remove`, `shelf_sync`, `ShelfItemView`). Logic is in `src/research_pipeline/shelf.py`: `chunk_text`, `sync_shelf`, `add_to_shelf`, `remove_from_shelf`, `SyncResult`. Storage helpers are in `library.py` (`upsert_shelf_item`, `delete_shelf_item`, `list_shelf_items`, `shelf_chunk_rows`), next to the other functions you write by hand; the tables are described in [data-model.md](data-model.md).
