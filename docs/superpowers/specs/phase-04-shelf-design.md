# The shelf (Phase 4, slice 1): design

> **Phase 4** · Status: approved, built · Plan: [phase-04-shelf.md](../plans/phase-04-shelf.md) · Roadmap: [ROADMAP.md](../ROADMAP.md)

Status: written 2026-11-22 after a conversation with the owner. Covers the shelf and the similarity boost. The "judge" (local model confirms overlaps and writes a callout) is slice 2 and gets its own design; this slice must not block it.

## Intent and why

The owner is moving into a CS master's programme. Today they will keep rough ideas and notes; later, their actual coursework and research. They want a place to keep materials that they can **add and remove at will**, and that the nightly pipeline uses to decide which new papers matter to *them*. In the owner's words: "a place where I can keep materials that I can add or remove that can be used to create similarities to the papers we are pulling every night."

So: a **shelf** of materials. Each night every new paper is compared with what is on the shelf; papers close to something on it rank higher, and the digest says which shelf item a paper is close to.

## Goals

- Add text or a file to the shelf; remove it; list what is on it. Removing takes effect on the next ranking (its stored text and vectors are deleted).
- Works for a one-line idea and for a long research draft alike.
- Papers near shelf items rank higher in triage; the reason line names the item.
- Shelf content never leaves the machine (see Constraints).
- Usable by AI-club members with no file editing (project rule in CLAUDE.md).

## Non-goals (this slice)

- The judge / "Overlaps your idea X; here is what they did differently" callouts (slice 2).
- Filling the `links` table (slice 2).
- Research-vs-ideas tiers. Dropped on purpose; a per-item "research" flag may come with the judge if ideas prove noisy.
- PDF or other formats. `.md` and `.txt` only.
- Feasibility tag and weekly synthesis (later Phase 4 slices).

## Anti-goals

- A shelf item that was removed still influencing rankings.
- Any path by which shelf text or vectors reach a cloud model or network service.
- Deleting a user's only copy of a file as a side effect of `remove`.

## Constraints

- Local only. Embedding uses the existing local `Embedder`. The shelf module must not import the LLM client or any HTTP library, and a test enforces that.
- Engine separate from data: shelf content lives in the vault (`my_work/` and `vault.sqlite`), never in the repo.
- Schema change goes through the `user_version` migration ladder (`db.py`): bump `SCHEMA_VERSION` 2 -> 3.
- Tests run offline with the fake embedder.
- Every change updates `docs/guide/` in the same commit; new terms go in `glossary.md`.
- No new user *settings* are introduced (weights are constants, like the other boosts), so no new settings commands are needed. An empty shelf means the feature is off.

## Approaches considered

For the whole Phase 4 overlap feature (the owner picked B):
- A. Similarity threshold only: cheap, noisy ("both about LLM security").
- **B. Similarity shortlist, then the local model judges (chosen).** Slice 1 builds the similarity half; slice 2 adds the judge.
- C. Model judges every paper against every item: too slow nightly on a 14B local model.

For where shelf items live (my call, for the builder to follow):
- **Files in `my_work/` are the source of truth, indexed into SQLite (chosen).** Users can drop files there or use `rpipe shelf add`; the folder is already in the vault layout. Rejected: SQLite-only storage (opaque, can't open or back up notes as ordinary files); rejected: a watcher daemon (needless; sync on each run is enough).

## Design

### Commands
- `rpipe shelf add <text-or-file>`: if the argument is an existing `.md`/`.txt` file, copy it into `my_work/` (keeping its name, made unique if taken); otherwise treat it as note text, write it to `my_work/<slug>.md` (slug from the first words, unique-ified), and tell the user the name. Then index it.
- `rpipe shelf remove <name>`: stop using the item. The file is **moved** to `my_work/.removed/` (recoverable, never deleted) and its chunks and vectors are deleted. Unknown name: error listing close matches.
- `rpipe shelf list`: names, chunk counts, added date.
- `rpipe shelf sync`: index new files, re-index changed ones (content hash), drop items whose file has vanished. Also runs automatically at the start of the triage stage, so dropped-in files just work. Skips `.removed/` and dotfiles; ignores other extensions with a one-line note.

### Data (schema v3)
- `shelf_items(name TEXT PRIMARY KEY, content_hash TEXT NOT NULL, added_at TEXT NOT NULL)`
- `shelf_chunks(item_name TEXT NOT NULL REFERENCES shelf_items(name) ON DELETE CASCADE, chunk_idx INTEGER NOT NULL, text TEXT NOT NULL, PRIMARY KEY (item_name, chunk_idx))`
- Chunk vectors go in the existing `embeddings` table with `item_id = "shelf:<name>#<chunk_idx>"` and the embedder's model name. Removing or re-indexing an item deletes its `shelf:<name>#%` embedding rows. Changing the embedding model re-embeds chunks lazily, as papers already are.

### Chunking
Split on markdown headings, then blank-line paragraphs; merge neighbours shorter than about 200 characters; split chunks longer than about 1500 characters at sentence boundaries (bge-small reads roughly 512 tokens). A one-line note is one chunk. Prefix each chunk with its item name and nearest heading when embedding, so a bare paragraph keeps its context. Exact numbers are the builder's call; document them as constants.

### Ranking
In `rank.py`, replace the `MY_WORK_WEIGHT = 0.0` stub with a real term modelled on the feedback term: `SHELF_BOOST * max over all shelf chunks of cosine(paper, chunk)` (nearest chunk only, so a big shelf doesn't dilute or drown the profile). Start at 0.20, the same as a like, and justify the size in the comment against the 0.045 similarity spread as the other weights do. If the nearest chunk's raw cosine is at least 0.70 (reuse the feedback reason floor idea), add the reason `near your shelf: <name>`. An empty shelf adds nothing and changes no existing scores.

### Wiring
`run_triage` takes shelf vectors alongside `FeedbackVectors` (built from the DB for the current embedder). `rpipe run` and `rpipe triage` call sync first.

### Docs
New `docs/guide/shelf.md` (page shape from CLAUDE.md, with a worked example and why-decisions above). Update `triage.md` (stub row becomes real), `data-model.md` (new tables, `links` still unused), `vault-and-config.md` (`my_work/` is now written and read, `.removed/`), `glossary.md` (chunk, shelf, nearest-neighbour), `README.md`, and `docs/superpowers/archive/handoff-log-to-2026-11-25.md`.

## Left to the builder
Exact chunk-size constants, slug rules, wording of messages, test layout, how sync reports its counts, whether `list` shows a preview.

## Next slice (not built now)
Judge: for each triaged paper whose nearest chunk passes a high bar, the local model reads the abstract beside the chunk and decides on a direct overlap; yes produces a prominent digest callout and a `links` row (`paper -> shelf item`, kind `overlaps`). It must stay local-only.
