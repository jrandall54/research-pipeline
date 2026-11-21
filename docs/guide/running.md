# Running the pipeline

## What it does

`research-pipeline run` is the one command you run each night. It collects new papers, ranks them against your profile and writes a digest, then records how the run went. The same stages are also available one at a time (`collect`, `triage`, `digest`) for debugging.

## How it works

### Install

```
uv sync --extra embed
```

`uv sync` installs the pipeline. The `embed` extra adds `sentence-transformers` (and with it PyTorch), the library that runs the embedding model. It is an **optional extra** because it is large (gigabytes, mostly PyTorch) and the tests, and every part of the pipeline except triage, do not need it. Without it `run` and `triage` stop with a clear message (see Troubleshooting). On a machine with a GPU, PyTorch uses it automatically; a CPU is also fine for a few hundred papers a night. The first run downloads the embedding model (about 130 MB for the default) from Hugging Face and caches it.

### Create your vault

```
uv run research-pipeline init
```

In a terminal it asks for your topics and arXiv categories; it then creates the vault (default `~/research-vault`, or `--vault PATH`, or the `RESEARCH_VAULT` environment variable) with `config.yaml`, `profile.yaml`, `vault.sqlite` and the folders described in `vault-and-config.md`. It is safe to re-run.

### Edit your profile

Open `<vault>/profile.yaml` and:

1. Replace the three placeholder seeds with the arXiv ids of 10-20 papers you like (`vault-and-config.md` has advice on choosing them).
2. Make the topics specific phrases ("prompt injection attacks and defenses for LLM agents" beats "security").
3. Optionally add followed authors and keywords, and set `top_n`.

### The commands

Every command takes `--vault PATH` (same resolution rules as `init`).

| Command | What it does | Writes |
| --- | --- | --- |
| `run` | `start_run`, then collect, seed lookup, triage, digest for today, then `finish_run` with the merged counts and errors | the `runs` row, papers, `digests/<today>.md` |
| `collect` | fetch from arXiv and Hugging Face into the database | papers (status `new`) |
| `triage` | rank the `new` papers, keep the top N | statuses, scores, reasons, embeddings |
| `digest [--date YYYY-MM-DD]` | write the digest from papers already `triaged` (date defaults to today) | `digests/<date>.md`, `in_digest` |

Only `run` writes a row to `runs`; the single-stage commands print their counts and errors instead. `collect` and `triage` build the same real sources and embedding model as `run`, so they also need the `embed` extra.

**Exit codes.** `0` normally, **including when a source failed** (the failure is recorded, see below). `1` when the vault is missing or has no `config.yaml`, a config file is invalid, the `embed` extra is missing, or `--date` is not a date.

### A real run, step by step

This is a real run on a clean vault (profile: LLM security topics, four well-known seed papers, `top_n` 15). Nothing personal appears here.

```
$ uv run research-pipeline run
Digest written to <vault>/digests/2026-11-21.md
  inserted=339, updated=11, unchanged=0, source_errors=0, triaged=15, skipped=324, embedded=339
```

It took about 13 seconds with the model already downloaded (about 2 minutes the very first time, which includes the model download and a cold start). Where the time goes:

1. **collect** fetches 300 newest arXiv papers across the configured categories (3 pages of 100, 3 seconds apart because of arXiv's rate limit) and about 50 Hugging Face Daily Papers. `inserted=339` papers were new; `updated=11` papers appeared in both sources, so the second source merged upvotes and repos into the first source's row.
2. **seeds**: the four seed ids were not in the database, so one arXiv request fetched their abstracts and saved them to `<vault>/cache/seeds.json`. A second run makes no seed request.
3. **triage** embedded all 339 papers (`embedded=339`; stored vectors are reused on later nights), scored them, kept 15 and skipped 324.
4. **digest** wrote the file and marked the 15 papers `in_digest`.

The top of the digest file:

```
Run 2026-11-21: collected 339 new, 11 updated · kept 15 · skipped 324 · errors: none

# Research digest — 2026-11-21

## 1. [BlockJack: Cross-Tenant Redirection Fault Attacks on Paged KV Cache Address Translation in ...]
**Why it matched** (score 0.85)
- closest seed: 2312.14197 (0.85)

## 2. [ToolPulse: Side-Channel Eavesdropping on Tool-Using LLM Agents]
**Why it matched** (score 0.85)
- closest seed: 2302.12173 (0.85)

## 3. [AltRA-Test: An Alternative Annotator Test for LLM Judges Without Shared Annotators]
**Why it matched** (score 0.85)
- closest seed: 2307.15043 (0.82)
- HF upvotes: 23
```

(Abstracts and links, omitted here, follow each heading; `digest.md` shows the full layout.) Reading it honestly: the top results are mostly LLM-security papers, which is right, but with only four seeds the model's notion of "close" is dominated by the word "LLM", so a few LLM-but-not-security papers (the annotator test above) rank high too. More seeds and sharper topics help; a proper comparison of embedding models is planned for Phase 2.

### The `runs` table

Each `run` adds one row, so you can see history without opening a digest. For the run above:

| Column | Value |
| --- | --- |
| `started_at`, `finished_at` | `2026-11-21T19:11:05+00:00`, `2026-11-21T19:11:16+00:00` (UTC) |
| `counts` | `{"inserted": 339, "updated": 11, "unchanged": 0, "source_errors": 0, "triaged": 15, "skipped": 324, "embedded": 339}` |
| `errors` | `[]` |

`counts` merges the collect counts (`inserted`, `updated`, `unchanged`, `source_errors`) with the triage counts (`triaged`, `skipped`, `embedded`); see `sources.md` and `triage.md`. A row with no `finished_at` means that run crashed before finishing. Look at it with `sqlite3 <vault>/vault.sqlite "select * from runs"`.

### Running it nightly

Phase 1 stops at the command; scheduling it (for example with a systemd timer or cron entry running `research-pipeline run`) is a later phase. Running twice on the same day is safe: no paper is stored twice and the day's digest is rewritten with everything from both runs (`digest.md`, "Same-day re-runs").

## Why it's built this way

- **One command that records itself**: `run` owns the `runs` row, so counts from every stage and every error end up in one place. The individual stage functions return counts and errors rather than finishing the run.
- **A failed source does not fail the run**: a nightly job that exits non-zero because one website was down would train you to ignore failures. The error is written to the digest's first line and to `runs.errors` instead; exit code 1 is kept for "nothing could run".
- **`build_pipeline` as the single seam**: `cli.py` builds the real sources, embedder and seed lookup in one function. The end-to-end tests replace that one function with fixture-backed stubs, so the *real* CLI, database, ranking and digest code run offline.
- **Optional `embed` extra**: keeps installs and tests light. Rejected: making PyTorch a hard dependency.
- **No `feedparser` dependency**: the arXiv response is parsed with the standard library (see `sources.md`); RSS is a later phase and can add the library then.

## How to run / check it

```
uv run research-pipeline run --vault /tmp/try-vault   # after init, on a scratch vault
uv run pytest -q                                      # whole suite, offline
uv run pytest tests/test_e2e.py -q                    # the end-to-end tests
```

`tests/test_e2e.py` runs the real CLI on a temporary vault with fixture sources and the test embedder, and checks: the digest is written and the top paper is the on-topic one; the `runs` row is finished; a second run the same day adds no duplicates; an uninitialised vault exits 1; a failing source exits 0 with the error recorded; a failed seed lookup does not stop the run; a missing `embed` extra gives a clear error.

### Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `No vault found at ... (no config.yaml). Run research-pipeline init first.` (exit 1) | The vault does not exist yet, or you pointed `--vault` / `RESEARCH_VAULT` at the wrong folder. Run `init`, or fix the path. |
| `invalid settings` / `invalid YAML` naming a file (exit 1) | A typo in `config.yaml` or `profile.yaml`; the message starts with the file path and says which field. |
| `sentence-transformers is not installed; run uv sync --extra embed` (exit 1) | Install the extra (see Install). |
| Health line says `errors: arxiv: arXiv API returned HTTP 5xx` (or a timeout) | arXiv (or Hugging Face) was down. The digest was built from the other source. Re-run later: `run` is safe to repeat. |
| Health line says `seeds: no paper found for 0000.00001, ...` | Those seed ids are not real arXiv papers, usually the placeholders from `init`. Replace them in `profile.yaml`. Ranking continues using your topics and any seeds that did resolve. |
| Health line says `seeds: could not fetch ...: <reason>` | The seed lookup failed (network). Ranking used the seeds it already had; it retries next run. |
| Digest says "Nothing new matched your profile today." | No paper was `triaged`: either no new papers arrived since the last run (they are already in an earlier digest) or every source failed (check the health line). |
| The digest has fewer papers than `top_n` | Fewer than `top_n` new papers arrived since the previous run. |
| Ranking looks off | See `triage.md`: add representative seeds, make topics more specific, check `triage_score` / `triage_reasons` in the database. |

## Where the code is

- `src/research_pipeline/cli.py`: `run`, `collect`, `triage`, `digest`, `build_pipeline`, `health_line`
- `src/research_pipeline/seeds.py`: seed text lookup and cache
- `tests/test_e2e.py`, `tests/test_seeds.py`
