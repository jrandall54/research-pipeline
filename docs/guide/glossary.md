# Glossary

**Canonical id** — the single identifier used to recognise a paper across sources: the arXiv id if it has one, otherwise the DOI, otherwise a hash of its URL. Two records with the same canonical id are the same paper.

**Status** — where a paper is in its lifecycle (`new`, `triaged`, `skipped`, `in_digest`, ...). See `data-model.md`.

**Vault** — the folder (default `~/research-vault/`) holding everything personal: config, profile, database, digests. Kept outside the repo.

**arXiv category** — the subject tag every arXiv paper carries, such as `cs.CR` (security) or `cs.LG` (machine learning). Only papers in your configured categories are collected; the single value `all` means every category. See "Choosing categories" in `profile.md`.

**Seed** — an arXiv id of a paper you like; new papers are ranked by similarity to your seeds. You can add one from an arXiv link, an id or a title search (see `profile.md`).

**FTS5** — SQLite's full-text search module. It builds an index of the words in a table so you can find rows by word quickly (`MATCH 'attention'`) and rank them, instead of scanning every row.

**Rowid** — SQLite's internal integer row number. We give `papers` an explicit one so the search index can point at the same row reliably.

**WAL (write-ahead log)** — a SQLite journal mode where changes are appended to a log before being merged into the main file. Readers do not block the writer and crashes are safer.

**Upsert** — "update or insert": add the record if it is new, otherwise merge it into the existing one. See `upsert_paper` in `data-model.md`.

**Profile** — `profile.yaml` in the vault: your topics, seed papers, followed authors, keywords and `top_n`. It is what papers are ranked against. See `vault-and-config.md`.

**Idempotent** — safe to run repeatedly: running `init` twice gives the same result as once and never destroys your edits.

**Package data** — non-code files (here the default YAML templates) shipped inside the installed Python package and read with `importlib.resources`.

**Atom feed** — a standard XML format for lists of items. arXiv's API answers queries with an Atom feed: one `<entry>` per paper with title, summary, authors and dates. See `sources.md`.

**Rate limit** — a cap on how often you may call a service. arXiv asks for at most one API request per 3 seconds; the arXiv source enforces that gap itself. See `sources.md`.

**Pagination** — fetching a long result list in fixed-size chunks (pages) using an offset, instead of in one huge response. See `sources.md`.

**Upvotes** — the count of Hugging Face users who endorsed a paper on Daily Papers. Stored as `hf_upvotes` and used as a small ranking boost. See `sources.md`.

**Hugging Face Daily Papers** — a community-curated daily list of notable (mostly arXiv) papers with upvotes and optional GitHub links. See `sources.md`.

**Source isolation** — one source failing (network error, bad response) is logged and skipped while the other sources still run, so a single outage never empties the whole night's run. See `sources.md`.

**Embedding** — a list of numbers produced by a model from a piece of text so that texts with similar meaning get similar numbers. We embed every paper, seed and topic. See `triage.md`.

**Vector** — an ordered list of numbers, here an embedding (384 numbers for the default model). Think of it as an arrow in a many-dimensional space.

**Cosine similarity** — how closely two vectors point the same way: 1 is identical direction, 0 unrelated, -1 opposite. For unit-length vectors it equals the dot product. See `triage.md` for a worked example.

**L2 normalisation** — scaling a vector so its length is 1. Done to embeddings so cosine similarity reduces to a dot product and text length does not matter.

**Top-N** — keeping only the N highest-scoring items (here `top_n`, default 15 papers per night); the rest are marked `skipped` but stay searchable.

**Digest** — the markdown file (`digests/YYYY-MM-DD.md`) listing the day's kept papers with reasons and abstracts. See `digest.md`.

**Health line** — the first line of a digest: counts collected/kept/skipped and any source errors, so a failed night is visible. See `digest.md`.

**Re-run (same day)** — running the pipeline again on the same date. The digest then rewrites the same file, still including papers already listed that day. See `digest.md`.

**Pipeline stub (test double)** — a fake stand-in for a real part (a source, the embedding model) used in tests so they run offline. `build_pipeline` in `cli.py` is the one place the real parts are created; tests replace it. See `running.md`.

**Run** — one execution of `rpipe run`, recorded as a row in the `runs` table with start/end times, counts and errors. See `running.md`.

**rpipe** — the command-line tool (`rpipe init`, `rpipe run`, ...). `research-pipeline` is a long alias for the same program. See `running.md`, "The `rpipe` command".

**Round-trip YAML** — loading a YAML file in a way that remembers comments, blank lines and layout, so saving it back keeps them. Plain YAML loaders discard comments. We use `ruamel.yaml` for edits. See `profile.md`.

**Atomic write** — saving a file so it is either fully the old version or fully the new one, never half-written: write to a temporary file in the same folder, then rename it over the original in one step. See `profile.md`.

**Sub-command** — a command inside a command group: `rpipe profile add-topic` is the `add-topic` sub-command of `profile`. `rpipe profile --help` lists them. See `profile.md`.

**Exit code** — the number a command returns to the shell: `0` means success, non-zero means it failed. The `rpipe profile` commands exit 1 on bad input or an invalid file, and 0 for harmless no-ops like "already in your profile".
