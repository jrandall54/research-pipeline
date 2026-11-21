# Sources

A source is something that can list recent papers. Phase 1 has two: arXiv (this page, first section) and Hugging Face Daily Papers (Task 5). A third piece (Task 6) combines them.

## arXiv

### What it does

Asks arXiv's public API for the newest papers in your configured categories (default `cs.LG, cs.CL, cs.CR, cs.OS, cs.SE`) and turns each result into a `PaperMeta` (see `data-model.md`). It can also look up specific papers by id, which is how your profile's seed papers are resolved.

**Limitation:** the spec talks about arXiv's daily listings. Phase 1 approximates them by querying the API sorted by submission date, newest first, and taking the first `max_results` (default 300). If more than 300 papers arrive between two runs, the oldest are missed; raise `max_results` if that matters.

### How it works

The API is a plain HTTPS GET that returns an **Atom feed**: an XML document with one `<entry>` per paper.

1. Build the query from the categories, joined with `OR`:

   ```
   https://export.arxiv.org/api/query?search_query=cat:cs.LG+OR+cat:cs.CR&sortBy=submittedDate&sortOrder=descending&start=0&max_results=100
   ```

2. Parse each `<entry>` into a `PaperMeta`:

   | Atom element | `PaperMeta` field | Notes |
   | --- | --- | --- |
   | `<id>` `http://arxiv.org/abs/2501.01234v2` | `id`, `version` | URL prefix and `vN` suffix split apart (see below) |
   | `<title>` | `title` | whitespace collapsed (the API wraps long lines) |
   | `<summary>` | `abstract` | whitespace collapsed; empty summary gives `""` |
   | `<author><name>` (repeated) | `authors` | in feed order |
   | `<published>` | `published` | date part only; this is when v1 appeared |
   | (constant) | `source` | always `"arxiv"` |
   | derived | `url` | `https://arxiv.org/abs/<id>`, no version |

   `hf_upvotes` and `repos` are not provided by arXiv and keep their defaults.

3. Pagination: see below.

#### Ids and versions

arXiv ids look like `2501.01234` (year-month, then a running number). Each revision adds a suffix: `v1`, `v2`, ... The feed reports the id of the latest version, e.g. `http://arxiv.org/abs/2501.01234v2`. We store:

- `id = "2501.01234"` (the canonical id; stable across revisions, so the same paper is recognised on every run)
- `version = 2`
- `url = "https://arxiv.org/abs/2501.01234"` (the abs page without a version always shows the latest)

If the entry has no `vN` suffix, `version` is 1. A later run seeing `version = 3` for a stored paper is an update (see the dedupe rules in `data-model.md`). Old-style ids such as `cs/0301001v1` also work.

#### The 3-second rule

arXiv's API terms of use ask clients to make no more than one request every three seconds, and to use a single connection. Ignoring this gets your IP throttled or blocked, which would break the nightly run. So the source tracks when its last request finished and, before the next one, sleeps only the *remaining* part of the 3 seconds (never before the very first request). The gap holds across both `fetch` and `fetch_by_ids` on the same `ArxivSource` object. The sleep function and clock are injectable so tests run instantly.

The requests also carry a generic `User-Agent` (`research-pipeline/0.1`) so arXiv can identify the client; it contains no personal information.

#### Pagination

The API returns at most a page of results per call. We ask for pages of 100: `start=0`, `start=100`, ... Paging stops when either `max_results` papers have been collected (the last page is sized to fit) or a page comes back with fewer entries than requested, which means arXiv has no more. Example with `max_results=230`: three calls (100, 100, 30), two 3-second waits between them.

#### Seed lookup by id

`fetch_by_ids(["1706.03762", ...])` sends `id_list=<comma-separated ids>` instead of a search query, up to 100 ids per call, with the same spacing. It returns one `PaperMeta` per id arXiv knows. Seeds are not stored in the `papers` table (they would show up in triage and the digest); the caller caches them separately.

#### Errors

Every failure raises `SourceError` (from `sources/base.py`) with a message saying what happened:

- HTTP error status (e.g. `arXiv API returned HTTP 500`)
- timeouts and connection failures (`arXiv API request failed: ...`)
- a response that is not valid XML or has malformed entries (`could not parse arXiv response: ...`)

Callers catch `SourceError` to log the failure and keep other sources running (Task 6).

### Why it's built this way

- **Official API, not scraping or RSS**: stable, documented, supports category queries, paging and id lookup. RSS only covers the latest day per category.
- **HTTPS endpoint `export.arxiv.org`**: the plain-http URL now redirects (301).
- **Standard-library XML parser, not feedparser**: feedparser silently tolerates broken input; we want bad responses to fail loudly as `SourceError`.
- **Spacing by elapsed time, not a fixed sleep**: if a call took 2 s we only wait 1 s more; if the caller was idle for a minute we do not wait at all.
- **Sorted by submission date**: simple and deterministic; the exact daily-listing semantics are deferred.

### How to run / check it

```
uv run pytest tests/test_arxiv.py -q
```

Tests are offline: HTTP is mocked with `respx` and `tests/fixtures/arxiv_atom.xml` is a real API response trimmed to three entries (one with `v2`, one with an empty summary, one with a wrapped title). To see the live shape: `curl 'https://export.arxiv.org/api/query?search_query=cat:cs.CR&max_results=3'`.

### Where the code is

- `src/research_pipeline/sources/base.py`: `Source` protocol, `SourceError`
- `src/research_pipeline/sources/arxiv.py`: `ArxivSource`, `parse_arxiv_atom`
- `tests/test_arxiv.py`, `tests/fixtures/arxiv_atom.xml`

## Hugging Face Daily Papers (Task 5)

_To be written in Task 5._

## How collection combines sources (Task 6)

_To be written in Task 6._
