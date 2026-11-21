# Sources

A source is something that can list recent papers. Phase 1 has two: arXiv and Hugging Face Daily Papers (both below). The collect stage (last section) combines them. `cli.py` wires them up in `build_pipeline`: an `ArxivSource` for your configured categories followed by an `HfDailySource`.

## arXiv

### What it does

Asks arXiv's public API for the newest papers in your configured categories (default `cs.LG, cs.CL, cs.CR, cs.OS, cs.SE`) and turns each result into a `PaperMeta` (see `data-model.md`). It can also look up specific papers by id, which is how your profile's seed papers are resolved.

**Limitation:** the spec talks about arXiv's daily listings. Phase 1 approximates them by querying the API sorted by submission date, newest first, and paging back until it reaches papers it already has. The setting `arxiv_max_results` in `config.yaml` (default 1000) is a safety cap on how far back one run will page. A normal weekday brings roughly 320 submissions in the default categories, so the cap is rarely reached after the first run.

**Stopping at a known paper.** Before each run, `run_collect` gives the arXiv source a test, "is this id already in the database?" (`set_known_checker`). After every page, `fetch()` checks whether any paper on it is already stored; if so it stops, because everything older was collected on an earlier night. That last page is kept in full and `upsert_paper` dedupes it. If the cap is reached without ever seeing a known paper, `fetch()` sets `last_warning`, and the collect stage adds `arxiv: reached max_results (<n>) before already-seen papers; some may be missed` to the run's errors, so it shows in `runs.errors` and the health line. **On the very first run the database is empty, so this warning appears by design.** After a long gap (for example a week offline) it can appear again: raise `arxiv_max_results` or accept the gap.

### How it works

The API is a plain HTTPS GET that returns an **Atom feed**: an XML document with one `<entry>` per paper.

1. Build the query from the categories, joined with `OR`:

   ```
   https://export.arxiv.org/api/query?search_query=cat:cs.LG+OR+cat:cs.CR&sortBy=submittedDate&sortOrder=descending&start=0&max_results=100
   ```

   If `arxiv_categories` is `["all"]` (every computer-science category) the query is the prefix wildcard `cat:cs.*`, with the same `sortBy=submittedDate` descending. A live check returned about 985,000 matches, newest first, and every returned paper carried at least one `cs.*` category (some have a non-cs primary category such as `quant-ph` but are cross-listed to cs, exactly as with an explicit `cat:cs.CR` query). Physics, maths and stat categories are not included by `all`. Paging, stopping at a known paper and the `arxiv_max_results` cap work as above, but cs.* is a few hundred new papers a day, so the cap is more likely to bind and more papers get embedded and ranked than with a handful of categories.

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

arXiv's API terms of use ask clients to make no more than one request every three seconds, and to use a single connection. Ignoring this gets your IP throttled or blocked, which would break the nightly run. So the source tracks when its last request finished and, before the next one, sleeps only the *remaining* part of the 3 seconds (never before the very first request). The gap holds across both `fetch` and `fetch_by_ids` on the same `ArxivSource` object, which is why callers that make several requests (the `init` wizard adding seed after seed) share one object and call `close()` on it when done. The sleep function and clock are injectable so tests run instantly.

The requests also carry a generic `User-Agent` (`research-pipeline/0.1`) so arXiv can identify the client; it contains no personal information.

#### Pagination

The API returns at most a page of results per call. We ask for pages of 100: `start=0`, `start=100`, ... Paging stops when a page comes back with fewer entries than requested (arXiv has no more), when a page contains an already-stored id (see above), or when `max_results` papers have been collected (the last page is sized to fit). Example with `max_results=230`: three calls (100, 100, 30), two 3-second waits between them.

#### Seed lookup by id

`fetch_by_ids(["1706.03762", ...])` sends `id_list=<comma-separated ids>` instead of a search query, up to 100 ids per call, with the same spacing. It returns one `PaperMeta` per id arXiv knows. Seeds are not stored in the `papers` table (they would show up in triage and the digest); `resolve_seed_texts` in `seeds.py` caches them in `<vault>/cache/seeds.json` (see `triage.md`).

#### Seed lookup by title

`search_title("Universal and Transferable")` sends `search_query=ti:"Universal and Transferable"` and `max_results=5` (the `ti:` prefix restricts matching to the title field; the quotes ask for the words as a phrase). Results come back in arXiv's relevance order, parsed like any other response, and the same 3-second spacing and `SourceError` handling apply. It exists so `rpipe profile add-seed` can turn a title into candidate papers for the user to choose from (see `profile.md`). Limit: it matches title words, so exact or distinctive titles work best.

#### Errors

Every failure raises `SourceError` (from `sources/base.py`) with a message saying what happened:

- HTTP error status (e.g. `arXiv API returned HTTP 500`)
- timeouts and connection failures (`arXiv API request failed: ...`)
- a response that is not valid XML or has malformed entries (`could not parse arXiv response: ...`)

The collect stage catches these failures, logs them and keeps the other sources running (see the last section).

### Why it's built this way

- **Official API, not scraping or RSS**: stable, documented, supports category queries, paging and id lookup. RSS only covers the latest day per category.
- **HTTPS endpoint `export.arxiv.org`**: the plain-http URL now redirects (301).
- **Standard-library XML parser, not feedparser**: feedparser silently tolerates broken input; we want bad responses to fail loudly as `SourceError`. (`feedparser` is therefore not a dependency; if RSS feeds are added in a later phase, that phase can add it back.)
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
   | `paper.githubRepo` | `repos` | a one-element list if set and it starts with `http://` or `https://`, else empty (other schemes such as `javascript:` are dropped) |
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

## How collection combines sources

### What it does

The collect stage (`run_collect`) takes the list of sources, asks each one for its recent papers and stores them in the database. It is the only place that knows there is more than one source. It returns two things: a dict of counts and a list of error strings.

### How it works

1. **Order.** Sources run one after another, in the order they are given. The nightly run passes arXiv first, then Hugging Face Daily Papers. Order matters for one reason: the first source to see a paper creates its row (title, abstract, version, `source`), and later sources only merge into it.
2. **One source at a time.** For each source: call `fetch()`, upsert every returned paper with `upsert_paper` (see `data-model.md`), then commit. `upsert_paper` does not commit itself, so a source's papers are one transaction: they are saved together when the source finishes, and if the program dies during a later source, the earlier sources' papers are already saved. After a successful source the stage also reads its optional `last_warning` (arXiv sets one when it hits the cap) and adds it to the errors.
3. **Isolation.** Each source is wrapped in its own `try`. If `fetch()` raises (normally `SourceError`, but any exception is caught), the stage records `"<source name>: <message>"`, adds 1 to `source_errors`, rolls back the transaction so that source leaves no rows at all (tested with a source that stores one paper and then fails), and carries on with the next one.
4. **Result.** `(counts, errors)`. The stage does not close the `runs` row; the caller combines these with the counts from triage and digest and calls `finish_run` once.

#### Example: one source fails

arXiv is down (HTTP 500), Hugging Face is fine and returns 50 papers, 3 of them new:

```
errors = ["arxiv: arXiv API returned HTTP 500"]
counts = {"inserted": 3, "updated": 12, "unchanged": 35, "source_errors": 1}
```

The Hugging Face papers are still stored (the 12 updates are upvote changes on papers already in the database). The error string ends up in the `runs` row and in the digest's health line, so you see the gap instead of a silent empty night.

#### Example: the same paper from two sources

Paper `2501.01234` is both a new arXiv submission and featured on Hugging Face today with 9 upvotes and a GitHub repo.

| Step | Source | What `upsert_paper` does | Count |
| --- | --- | --- | --- |
| 1 | arXiv returns the paper (`version` 1, 0 upvotes, no repos) | no row with that id: insert, `source = "arxiv"` | `inserted` +1 |
| 2 | HF returns the same id (9 upvotes, repo) | row exists, same version: keep the higher upvotes (9), union the repos; text and `source` stay as arXiv wrote them | `updated` +1 |

Result: one row with arXiv's text and 9 upvotes plus the repo. This works because both sources use the arXiv id as the canonical id. The collect stage does no merging itself; it relies on `upsert_paper`.

#### What the counts mean

| Key | Meaning |
| --- | --- |
| `inserted` | papers not seen before (new rows) |
| `updated` | existing rows that changed: a higher `version`, or new upvotes / repos from a source. A paper counted `inserted` by arXiv and then merged by HF in the same run also adds an `updated` |
| `unchanged` | papers returned but already stored identically; this is most of the list on a second run |
| `source_errors` | number of sources that failed (not number of papers) |

Because the same paper can appear in two sources, the first three counts add up to the number of papers *returned*, which can exceed the number of distinct papers.

#### Where errors go

Errors are plain strings `"<source name>: <message>"` returned to the caller. They are not raised, so a failure never stops the run. The `run` command (see `running.md`) does three things with them: it appends any seed-lookup problem to the same list, writes the list to `runs.errors` (a JSON list) through `finish_run`, and prints it in the digest's health line after `errors:`. The `run` command still exits 0 when a source fails; it exits non-zero only when the vault is missing or unusable.

### Why it's built this way

- **Catch everything per source, not just `SourceError`**: a parsing bug in one source should cost that source's papers, not the whole night's digest.
- **Commit per source, not per paper**: bounds the loss from a crash to one source, makes the rollback real (a half-stored source never remains), and is much faster than a commit per paper.
- **Known-id checker injected by `run_collect`**: `build_pipeline` has no database connection, and the source should not own one. Rejected: giving the source a connection, or having the CLI reach into the source after opening the DB.
- **Return errors instead of finishing the run**: the run row covers all stages, so only the caller can write it once with combined counts and errors. Rejected: having `run_collect` call `finish_run`, which would close the run before triage and digest.
- **No merge logic here**: duplicates are the database's job (one rule, one place), so adding a third source needs no change to collect.
- **`run_id` accepted but unused (and optional)**: the standalone `collect` command passes none, since only `run` writes a `runs` row; reserved for tagging papers with the run that found them, without changing the signature later.

### How to run / check it

```
uv run pytest tests/test_collect.py -q
```

Tests use stub source classes (no network): cross-source dedupe with upvote merge, a failing source not stopping the others, a second run inserting nothing, and a version bump counting as updated.

### Where the code is

- `src/research_pipeline/collect.py`: `run_collect`
- `tests/test_collect.py`
