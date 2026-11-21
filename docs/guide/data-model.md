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
| `seeds` | arXiv ids of papers you like; ranking measures similarity to them (required, may be empty). |
| `followed_authors` | Authors whose papers get a boost. |
| `keywords` | Words that give a boost when they appear. |
| `top_n` | How many papers to keep per run. Default 15, must be at least 1. |

### `Config` — pipeline settings

| Field | Meaning |
| --- | --- |
| `vault_path` | Where your vault lives (required). |
| `arxiv_categories` | arXiv categories to collect. Default `cs.LG, cs.CL, cs.CR, cs.OS, cs.SE`. |
| `embedding_model` | Model used to turn text into vectors. Default `BAAI/bge-small-en-v1.5`. |
| `rss_feeds` | Extra RSS feed URLs; default none. |

### `Status` — the paper lifecycle

```
new -> triaged -> fetched -> summarized -> in_digest
  \-> skipped                      (any stage) -> failed
```

| Status | Meaning | Used in Phase 1? |
| --- | --- | --- |
| `new` | Collected, not yet scored. | yes |
| `triaged` | Scored and kept in the top N. | yes |
| `fetched` | Full text downloaded. | no (later phase) |
| `summarized` | Summary written. | no (later phase) |
| `in_digest` | Listed in a digest. | yes |
| `skipped` | Scored but below the cut; kept and searchable. | yes |
| `failed` | Processing errored. | no |

A new version of a kept paper sets it back to `triaged`.

## Why it's built this way

- Typed models catch malformed source data at the boundary.
- `skipped` papers are retained (not deleted) so search still finds them.
- Status values are a `StrEnum`, so they store as plain strings in the database.

## How to run / check it

`uv run pytest tests/test_models.py -v`

## Where the code is

`src/research_pipeline/models.py`, `tests/test_models.py`

## Database tables — added in Task 2

_Not yet written. Task 2 documents every table and column, and the dedupe/version rules with a worked example._
