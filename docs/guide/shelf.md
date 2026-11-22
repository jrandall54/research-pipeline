# The shelf: your own notes and writing

## What it does

The shelf is a folder of your own material (`my_work/` in the vault): rough ideas, notes, coursework, drafts. Files on it are split into small pieces, turned into embeddings (numbers capturing meaning) and stored so the pipeline can later ask "which new papers sit close to something I am working on?". You can add to it and remove from it at any time. This page covers the indexing half: keeping the shelf and its stored vectors in step with the files. Using it to boost rankings is a later task and is described in [triage.md](triage.md) once built.

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

**Add** (`add_to_shelf`). If the argument is an existing `.md` or `.txt` file it is copied into `my_work/` under its own name; a clash gets `-2`, `-3` ... so nothing is overwritten. Otherwise the argument is note text, saved as `my_work/<slug>.md` where the slug is the first six words, lowercased, letters, digits and hyphens only (`note` if nothing is left), made unique the same way. Empty text, or a file that is not `.md`/`.txt`, raises an error that says what to do. Then sync runs and the item name is returned.

**Remove** (`remove_from_shelf`). The file is moved to `my_work/.removed/` (renamed `-2` ... if that name is taken there), then the item, its chunks and its vectors are deleted. Your file is never deleted; to bring it back, move it out of `.removed/` and run `rpipe shelf sync`. An unknown name raises an error listing the closest names (`difflib.get_close_matches`).

## Why it's built this way

- **Files are the source of truth, the database is an index.** You can open, back up or drop files into `my_work/` with any tool, and sync rebuilds the index. Rejected: storing notes only in SQLite (opaque, cannot be opened as ordinary files); rejected: a file-watcher daemon (needless, syncing before each run is enough).
- **Content hash, not modified time.** A changed file is detected by the sha256 of its bytes, so copying or touching a file does not cause re-embedding and an edit never goes unnoticed. The skip also requires vectors for the current embedder, so switching embedding model re-embeds the shelf by itself.
- **Edits replace, not append.** Re-indexing an item deletes its old chunks and vectors first, so stale text cannot keep influencing results (an anti-goal in the design).
- **Remove moves, never deletes.** `.removed/` makes a mistake recoverable.
- **Heading prefix on each chunk.** Without it a paragraph reading "It fails on long inputs." has no subject once separated from its note.
- **Plain size constants.** 200 and 1500 characters are rules of thumb; there is no setting for them, so there is nothing for a user to hand-edit.

## How to run / check it

Tests: `uv run pytest tests/test_shelf.py -q` (offline, fake embedder). The `rpipe shelf add|remove|list|sync` commands arrive in a later task; until then the functions below are the whole interface.

## Where the code is

`src/research_pipeline/shelf.py`: `chunk_text`, `sync_shelf`, `add_to_shelf`, `remove_from_shelf`, `SyncResult`. Storage helpers are in `db.py` (`upsert_shelf_item`, `delete_shelf_item`, `list_shelf_items`, `shelf_chunk_rows`); the tables are described in [data-model.md](data-model.md).
