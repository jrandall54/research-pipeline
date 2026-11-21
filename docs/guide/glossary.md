# Glossary

**Canonical id** — the single identifier used to recognise a paper across sources: the arXiv id if it has one, otherwise the DOI, otherwise a hash of its URL. Two records with the same canonical id are the same paper.

**Status** — where a paper is in its lifecycle (`new`, `triaged`, `skipped`, `in_digest`, ...). See `data-model.md`.

**Vault** — the folder (default `~/research-vault/`) holding everything personal: config, profile, database, digests. Kept outside the repo.

**Seed** — an arXiv id of a paper you like; new papers are ranked by similarity to your seeds.

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
