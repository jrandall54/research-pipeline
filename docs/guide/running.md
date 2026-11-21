# Running the pipeline

## What it does

`rpipe run` is the one command you run each night. It collects new papers, ranks them against your profile, has a local model summarise and critique the kept ones, and writes a digest, then records how the run went. The stage order is collect, seed lookup, triage, summarize, digest. The same stages are also available one at a time (`collect`, `triage`, `summarize`, `digest`) for debugging.

## How it works

### Install

```
uv sync --extra embed
```

`uv sync` installs the pipeline. The `embed` extra adds `sentence-transformers` (and with it PyTorch), the library that runs the embedding model. It is an **optional extra** because it is large (gigabytes, mostly PyTorch) and the tests, and every part of the pipeline except triage, do not need it. Without it `run` and `triage` stop with a clear message (see Troubleshooting); `collect` and `digest` never load the model and work without it. On a machine with a GPU, PyTorch uses it automatically; a CPU is also fine for a few hundred papers a night. The first run downloads the embedding model (about 130 MB for the default) from Hugging Face and caches it.

### The `rpipe` command

The command is `rpipe` (short for "research pipeline"). `research-pipeline` still works as a long alias, so older notes and scripts keep working.

Inside the repo you can always prefix it with `uv run` (`uv run rpipe run`). To type plain `rpipe` from any folder, install it once as a user tool:

```
uv tool install --editable '.[embed]'
```

`--editable` means the installed command runs the code in this checkout, so a `git pull` takes effect without reinstalling. Re-run the command only when dependencies change. The examples below use `uv run rpipe`, which works either way.

**Why a short name:** a command people run every day should be quick to type and easy to remember. `rpipe` is short, says what it is, and does not clash with a common Unix tool. The Python package keeps its descriptive name (`research_pipeline`), because nobody types that.

### Create your vault

```
uv run rpipe init
```

It creates the vault (default `~/research-vault`, or `--vault PATH`, or the `RESEARCH_VAULT` environment variable) with `config.yaml`, `profile.yaml`, `vault.sqlite` and the folders described in `vault-and-config.md`. In a terminal it then guides you through your topics, arXiv categories and seed papers (a sample session is in `vault-and-config.md`), saving each answer as you give it; Ctrl-C stops cleanly and keeps what you entered. It is safe to re-run: on an existing vault it shows your settings and asks before changing anything.

### Set up your profile

The `rpipe init` wizard covers steps 1 and 2 below the first time. You never need to open `profile.yaml`; use the commands any time later (details and sample output in `profile.md`):

1. Add 10-20 papers you like as seeds. Paste an arXiv link, an id, or a title and confirm: `rpipe profile add-seed https://arxiv.org/abs/2307.15043` (`vault-and-config.md` has advice on choosing them).
2. Make the topics specific phrases ("prompt injection attacks and defenses for LLM agents" beats "security"): `rpipe profile add-topic "..."`, and drop the examples with `rpipe profile remove-topic 1`.
3. Optionally follow authors (`add-author`), add keywords (`add-keyword`), and set `rpipe profile set-top-n 10` or `rpipe profile set-categories cs.CR cs.LG` (or `all` for every computer-science category if you are not sure which fit; it fetches much more each night, see "Choosing categories" in `profile.md`).
4. Check the result with `rpipe profile show`.

### The commands

Every command takes `--vault PATH` (same resolution rules as `init`).

| Command | What it does | Writes |
| --- | --- | --- |
| `run` | `start_run`, then collect, seed lookup, triage, summarize, digest for today, then `finish_run` with the merged counts and errors | the `runs` row, papers, summaries, `digests/<today>.md` |
| `collect` | fetch from arXiv and Hugging Face into the database | papers (status `new`) |
| `triage` | rank the `new` papers, keep the top N | statuses, scores, reasons, embeddings |
| `summarize` | summarise and critique every paper still waiting (`triaged`, `fetched`, or in today's digest without a summary) with the local model (fetching arXiv HTML full text first; abstract only if there is none) | full texts, summaries, statuses `fetched` then `summarized` |
| `digest [--date YYYY-MM-DD]` | write the digest from papers already `triaged` (date defaults to today) | `digests/<date>.md`, `in_digest` |

**The summarize stage.** `rpipe run` checks that Ollama is up and the model installed before this stage. If not, it skips the stage, records the cause in the health line and `runs.errors`, and still writes the digest (abstracts only), so a stopped Ollama never costs you the night's digest. A single paper that fails (model error, network error fetching its text) keeps its status, gets its `error` and `retry_count` set, and the rest carry on.

**Which papers the stage works on.** Papers that are `triaged` or `fetched`, plus papers already listed in *today's* digest that have no summary yet (they stay `in_digest`; the summary is just saved). A paper that has failed 3 times (`retry_count` 3) is skipped and the errors say `gave up after 3 failed tries (last error: ...)`. Papers from an *earlier* day's digest are never touched: if Ollama was down that night, those papers stay abstract-only (accepted limitation).

`rpipe summarize` on its own prints "Nothing to summarize" and exits 0 when there is nothing of that kind; it exits 1 with the cause if Ollama is unavailable. The model setting `llm_num_ctx` must be at least 8192 (see `llm.md`).

Model commands (`rpipe model show`, `rpipe model set NAME`, `rpipe model check`) view, change and test the local Ollama model used for summaries; see `llm.md`. `check` exits 1 with a fix hint if Ollama is not running or the model is not installed.

Evaluation commands (`rpipe eval run`, `rpipe eval grade`, `rpipe eval report`) check summary accuracy against your gold set; see `evaluation.md`. `eval run` exits 1 if the gold set is empty or the model is unavailable.

Only `run` writes a row to `runs`; the single-stage commands print their counts and errors instead (a deliberate choice: the table is the history of nightly runs). Only `run` and `triage` need the `embed` extra: the embedding model is created lazily (`Pipeline.make_embedder`), so `collect` and `digest` work on a machine without it.

**Exit codes.** `0` normally, **including when a source failed or Ollama was down** (summaries are then skipped; `rpipe summarize` alone exits 1 in that case) (the failure is recorded, see below). `1` when the vault is missing or has no `config.yaml`, a config file is invalid, a `profile` command is given bad input, the `embed` extra is missing, or `--date` is not a date.

### A real run, step by step

This is a real run on a clean vault (profile: LLM security topics, four well-known seed papers, `top_n` 15). Nothing personal appears here.

```
$ uv run rpipe run
Digest written to <vault>/digests/2026-11-21.md
  inserted=339, updated=11, unchanged=0, source_errors=0, triaged=15, skipped=324, embedded=339
```

It took about 13 seconds with the model already downloaded (about 2 minutes the very first time, which includes the model download and a cold start). Where the time goes:

1. **collect** fetches the newest arXiv papers across the configured categories, in pages of 100 spaced 3 seconds apart because of arXiv's rate limit. It stops after the first page that contains a paper already in the vault, or at `arxiv_max_results` (default 1000), whichever comes first. (This sample run predates that rule and used a fixed limit of 300, so it fetched 3 pages.) It also fetches about 50 Hugging Face Daily Papers. `inserted=339` papers were new; `updated=11` papers appeared in both sources, so the second source merged upvotes and repos into the first source's row.
2. **seeds**: the four seed ids were not in the database, so one arXiv request fetched their abstracts and saved them to `<vault>/cache/seeds.json`. A second run makes no seed request.
3. **triage** embedded all 339 papers (`embedded=339`; stored vectors are reused on later nights), scored them, kept 15 and skipped 324.
4. **summarize** (added in Phase 2, after this sample run was taken, so its counts and time are not in the numbers above) fetched each kept paper's arXiv HTML, asked the local model for a cited summary and critique, and moved the paper to `summarized`. It is by far the slowest stage: expect on the order of a minute per paper on a laptop-class GPU.
5. **digest** wrote the file and marked the papers `in_digest`.

The top of the digest file:

```
Run 2026-11-21: collected 339 new, 11 updated · kept 15 · skipped 324 · summarized 0 · errors: none

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

`counts` merges the collect counts (`inserted`, `updated`, `unchanged`, `source_errors`) with the triage counts (`triaged`, `skipped`, `embedded`) and, since Phase 2, the summarize counts (`fetched`, `summarized`, `abstract_only`, `summary_failed`); see `sources.md`, `triage.md` and the summarize stage above. The sample row above predates Phase 2 so it has no summarize counts, and the sample health line shows `summarized 0` for the same reason. A row with no `finished_at` means that run crashed before finishing. Look at it with `sqlite3 <vault>/vault.sqlite "select * from runs"`.

### Running it nightly

The pipeline stops at the command; scheduling it (for example with a systemd timer or cron entry running `rpipe run`) is a later phase. Running twice on the same day is safe: no paper is stored twice and the day's digest is rewritten with everything from both runs (`digest.md`, "Same-day re-runs").

## Why it's built this way

- **One command that records itself**: `run` owns the `runs` row, so counts from every stage and every error end up in one place. The individual stage functions return counts and errors rather than finishing the run.
- **A failed source does not fail the run**: a nightly job that exits non-zero because one website was down would train you to ignore failures. The error is written to the digest's first line and to `runs.errors` instead; exit code 1 is kept for "nothing could run".
- **`build_pipeline` as the single seam**: `cli.py` builds the real sources, a lazy embedder factory, seed lookup, a lazy model-client factory (`make_llm`) and the full-text fetcher in one function. The end-to-end tests replace that one function with fixture-backed stubs, so the *real* CLI, database, ranking and digest code run offline.
- **Optional `embed` extra**: keeps installs and tests light. Rejected: making PyTorch a hard dependency.
- **No `feedparser` dependency**: the arXiv response is parsed with the standard library (see `sources.md`); RSS is a later phase and can add the library then.

## How to run / check it

```
uv run rpipe run --vault /tmp/try-vault   # after init, on a scratch vault
uv run pytest -q                                      # whole suite, offline
uv run pytest tests/test_e2e.py -q                    # the end-to-end tests
```

`tests/test_e2e.py` runs the real CLI on a temporary vault with fixture sources and the test embedder, and checks: the digest is written and the top paper is the on-topic one; the `runs` row is finished; a second run the same day adds no duplicates; an uninitialised vault exits 1; a failing source exits 0 with the error recorded; a failed seed lookup does not stop the run; a missing `embed` extra gives a clear error; with a scripted fake model the digest contains a cited summary; with the model unavailable `run` still exits 0, writes the digest and names the cause in the health line; `rpipe summarize` runs the stage alone.

### Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `No vault found at ... (no config.yaml). Run rpipe init first.` (exit 1) | The vault does not exist yet, or you pointed `--vault` / `RESEARCH_VAULT` at the wrong folder. Run `init`, or fix the path. |
| `invalid settings` / `invalid YAML` naming a file (exit 1) | One of the two settings files holds an invalid value (this should not happen if you only use `rpipe profile ...`, which refuses bad input). The message starts with the file path and says which field. Every `rpipe profile` command refuses to change a file that is already broken (and tells you which file), so this is the one case where you correct the named field in the file itself; the file reference in `vault-and-config.md` lists every field and its allowed values. After that the commands work again. |
| `sentence-transformers is not installed; run uv sync --extra embed` (exit 1) | Install the extra (see Install). |
| Health line says `errors: arxiv: arXiv API returned HTTP 5xx` (or a timeout) | arXiv (or Hugging Face) was down. The digest was built from the other source. Re-run later: `run` is safe to repeat. |
| Health line says `seeds: no paper found for 0000.00001, ...` | Those seed ids are not real arXiv papers, for example a mistyped id. Remove it with `rpipe profile remove-seed` and add the right one with `rpipe profile add-seed` (`rpipe profile show` numbers the seeds). Ranking continues using your topics and any seeds that did resolve. |
| Health line says `seeds: could not fetch ...: <reason>` | The seed lookup failed (network). Ranking used the seeds it already had; it retries next run. |
| Health line says `summaries skipped, model unavailable: cannot reach Ollama at ...` | Ollama is not running. Start it with `ollama serve` (or the desktop app), check the URL with `rpipe model show` and test with `rpipe model check`. Tonight's digest was still written, with abstracts only. **To recover the same day:** start Ollama (`ollama serve`), then run `rpipe summarize` and `rpipe digest` (or just `rpipe run` again). The papers in today's digest get their summaries and the file is rewritten. If you wait until the next day, those papers are not summarised (accepted limitation); only newly kept papers are. |
| Health line says `model unavailable: model 'qwen3:14b' is not installed: run ollama pull ...` | The model is not downloaded. Run the `ollama pull` command shown, or pick another with `rpipe model set NAME`. |
| Health line shows `summary failures N` or `abstract-only N` | `summary failures`: the model errored or timed out on N papers (see the `summarize <id>: ...` entries in the errors); they show abstracts only. `abstract-only`: N papers have no arXiv HTML version (non-arXiv ids, or too new), so their summary rests on the abstract and says so in the digest. |
| Digest says "Nothing new matched your profile today." | No paper was `triaged`: either no new papers arrived since the last run (they are already in an earlier digest) or every source failed (check the health line). |
| Health line says `arxiv: reached max_results (N) before already-seen papers; some may be missed` | arXiv paging hit `arxiv_max_results` without reaching a paper you already had. Expected on the first run (empty database). Otherwise you were away a long time and the cap is too low; `arxiv_max_results` is an advanced setting with no command yet (see the file reference in `vault-and-config.md`). |
| Health line says `written from stored papers` | You re-ran `digest` on its own; it replaced that day's original health line. The original errors remain in the `runs` table. |
| `could not load embedding model <name>: ...` (exit 1) | The model failed to download or load (offline, disk, broken torch install). Fix the cause and re-run. |
| The digest has fewer papers than `top_n` | Fewer than `top_n` new papers arrived since the previous run. |
| Ranking looks off | See `triage.md`: add representative seeds, make topics more specific, check `triage_score` / `triage_reasons` in the database. |

## Where the code is

- `src/research_pipeline/cli.py`: `run`, `collect`, `triage`, `summarize`, `digest`, `build_pipeline`, `health_line` (the `profile` commands are in `profile.md`)
- `src/research_pipeline/seeds.py`: seed text lookup and cache
- `src/research_pipeline/summarize.py`: `run_summarize` (the per-paper loop)
- `src/research_pipeline/evaluate.py`: `rpipe eval` (see `evaluation.md`)
- `tests/test_e2e.py`, `tests/test_summarize.py`, `tests/test_seeds.py`
