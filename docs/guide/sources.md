# Sources

A source is something that can list recent papers. Phase 1 has two: arXiv and Hugging Face Daily Papers (both below). A third piece (Task 6) combines them.

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

## Hugging Face Daily Papers

### What it does

Fetches the papers currently featured on [Hugging Face Daily Papers](https://huggingface.co/papers), a community-curated list: someone submits a paper, the community upvotes it, and each paper page can link a GitHub repo. We turn each entry into a `PaperMeta` (see `data-model.md`).

The reason to pull it in is the **upvote count**. arXiv gets hundreds of papers a day and says nothing about which ones people found interesting. A paper that other researchers upvoted is a cheap, human-made quality signal, so triage gives it a small ranking boost (see `triage.md`). The linked GitHub repo is also useful: it tells you code exists.

### How it works

1. One plain HTTPS GET, no parameters and no authentication, to `https://huggingface.co/api/daily_papers`. It returns a JSON list (about 50 items) of the current featured papers.
2. Each item looks like this (trimmed to the fields we use; the real item has many more, such as avatars, organization and AI summaries):

   ```json
   {
     "paper": {
       "id": "2611.26232",
       "title": "Time Series Structure Learning ...",
       "summary": "Learning and inference on time series ...",
       "authors": [{"name": "Jacqueline Maasch", "hidden": false}],
       "publishedAt": "2026-11-19T00:00:00.000Z",
       "upvotes": 2,
       "githubRepo": "https://github.com/jmaasch/itpd"
     },
     "title": "...", "summary": "...", "publishedAt": "2026-11-18T19:00:00.000Z"
   }
   ```

3. Field mapping (everything is under the item's `paper` object unless noted):

   | Payload field | `PaperMeta` field | Notes |
   | --- | --- | --- |
   | `paper.id` | `id` | the arXiv id; a `vN` suffix, if ever present, is dropped |
   | `paper.title` (fallback: top-level `title`) | `title` | whitespace collapsed |
   | `paper.summary` | `abstract` | whitespace collapsed; missing gives `""` |
   | `paper.authors[].name` | `authors` | in payload order; entries without a name are skipped |
   | `paper.publishedAt` | `published` | date part only; missing or unparseable gives `None` |
   | `paper.upvotes` | `hf_upvotes` | missing gives `0` |
   | `paper.githubRepo` | `repos` | a one-element list if set, else empty |
   | (constant) | `source` | always `"hf_daily"` |
   | derived | `url` | `https://arxiv.org/abs/<id>` |

   `version` keeps its default of 1; HF does not report the arXiv revision.

4. Items with no usable arXiv id (missing, or not shaped like an arXiv id) are skipped silently. That is not an error: the rest of the list is still good.

#### How it overlaps with arXiv

Almost every Daily Paper is also an arXiv paper, so the same paper can arrive from both sources in one run. We set `id` to the **canonical id** (the bare arXiv id, e.g. `2611.26232`), exactly what the arXiv source produces. The database then sees one paper, not two: `upsert_paper` finds the existing row by id, keeps the higher `hf_upvotes` and unions the `repos` (see the worked example in `data-model.md`). So the arXiv record supplies text and version, the HF record supplies upvotes and the repo, and the stored paper carries both. Using the arXiv abs URL for `url` keeps the two records consistent too.

#### Errors

Every failure raises `SourceError`:

- HTTP error status (`Hugging Face API returned HTTP 503`)
- timeouts and connection failures (`Hugging Face API request failed: ...`)
- a body that is not JSON, or JSON whose top level is not a list (`could not parse Hugging Face response: ...`)

Requests carry the same generic `User-Agent` as the arXiv source (`research-pipeline/0.1`), with no personal information.

### Why it's built this way

- **The JSON API, not scraping the web page**: the page markup can change at any time; the JSON endpoint is what the site itself uses and gives upvotes and repo links as plain fields.
- **arXiv id as `id`**: it is the cross-source canonical id, which is what makes the dedupe above work. HF's own internal ids would never match arXiv's.
- **Skip, do not fail, on odd items**: one malformed item should not discard 49 good ones. Whole-response problems (not JSON, not a list) do fail, since then nothing can be trusted.
- **Tolerant of missing fields**: HF entries often lack a repo, and sometimes a summary; those are normal.
- **No pagination, no rate limiting**: the endpoint returns the current list in one call and we make one call per run.

### How to run / check it

```
uv run pytest tests/test_hf_daily.py -q
```

Tests are offline: HTTP is mocked with `respx` and `tests/fixtures/hf_daily.json` is a real response trimmed to three items (one with a GitHub repo and upvotes, one with no repo and no summary, one with no repo and 17 upvotes). To see the live shape: `curl https://huggingface.co/api/daily_papers | python -m json.tool | head -60`.

### Where the code is

- `src/research_pipeline/sources/hf_daily.py`: `HfDailySource`, `parse_hf_daily`
- `tests/test_hf_daily.py`, `tests/fixtures/hf_daily.json`

## How collection combines sources (Task 6)

_To be written in Task 6._
