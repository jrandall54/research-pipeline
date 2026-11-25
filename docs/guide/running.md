# Running the pipeline

## What it does

`rpipe run` is the one command you run each night. It collects new papers, ranks them against your profile, has a local model summarise and critique the kept ones, and writes a digest, then records how the run went. The stage order is collect, seed lookup, triage, summarize, digest. The same stages are also available one at a time (`collect`, `triage`, `summarize`, `digest`) for debugging.

## How it works

### Install

```
uv tool install "research-pipeline[embed] @ git+https://github.com/jrandall54/research-pipeline.git"   # use [embed,serve] for the phone browser
```

That is the stranger's install (see the top-level `README.md`). If you are working on the code, use a checkout instead:

```
uv sync --extra embed                          # in the repo
uv tool install --editable '.[embed]' --force  # for the plain `rpipe` command (and the nightly timer)
```

The first command installs a copy of `rpipe` from GitHub for everyday use; the two below it are for developers. `uv sync` installs the pipeline into the repo's own environment; the plain `rpipe` on your PATH (from `uv tool install`) lives in a separate environment that `uv sync` never touches, so install it with the second command. `uv sync` is an *exact* sync: it removes extras you did not name, so always list them all (for example `uv sync --extra embed --extra serve`). The `embed` extra adds `sentence-transformers` (and with it PyTorch), the library that runs the embedding model. It is an **optional extra** because it is large (gigabytes, mostly PyTorch) and the tests, and every part of the pipeline except triage, do not need it. Without it `run` and `triage` stop with a clear message (see Troubleshooting); `collect` and `digest` never load the model and work without it. The embedding model always runs on the CPU, even when there is a GPU (about 20 s for a night's few hundred papers), so the GPU stays free for the summarizing model; see "Why it's built this way" in `triage.md`. The first run downloads the embedding model (about 130 MB for the default) from Hugging Face and caches it.

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

It creates the vault (default `~/research-vault`, or `--vault PATH`, or the `RESEARCH_VAULT` environment variable) with `config.yaml`, `profile.yaml`, `vault.sqlite` and the folders described in `vault-and-config.md`. In a terminal it then guides you through the summary backend first (`rpipe model setup`, see `setup.md`: it checks your computer and recommends Ollama, Claude Code or abstracts only, because the next step already calls the model), then an optional description of your research, your topics, arXiv categories and seed papers (a sample session is in `vault-and-config.md`), saving each answer as you give it; Ctrl-C stops cleanly and keeps what you entered. It is safe to re-run: on an existing vault it shows your settings and asks before changing anything, including "Keep current backend (X)? [Y/n]" (the default, Enter, keeps the backend and any model you pinned and just re-tests it).

### Set up your profile

The `rpipe init` wizard covers steps 1 and 2 below the first time, and it can start from a description: answer its first question with a few sentences about your research and the summary model proposes topics and seed papers for you to approve (the same thing is available later as `rpipe profile suggest "..."`; it needs a working summary backend, and without one you just continue by hand). You never need to open `profile.yaml`; use the commands any time later (details and sample output in `profile.md`):

1. Add 10-20 papers you like as seeds. Paste an arXiv link, an id, or a title and confirm: `rpipe profile add-seed https://arxiv.org/abs/2307.15043` (`vault-and-config.md` has advice on choosing them).
2. Make the topics specific phrases ("prompt injection attacks and defenses for LLM agents" beats "security"): `rpipe profile add-topic "..."`, and drop the examples with `rpipe profile remove-topic 1`.
3. Optionally follow authors (`add-author`), add keywords (`add-keyword`), and set `rpipe profile set-top-n 10` or `rpipe profile set-categories cs.CR cs.LG` (or `all` for every computer-science category if you are not sure which fit; it fetches much more each night, see "Choosing categories" in `profile.md`).
4. Check the result with `rpipe profile show`.

### The commands

Every command takes `--vault PATH` (same resolution rules as `init`).

| Command | What it does | Writes |
| --- | --- | --- |
| `run` | `start_run`, then collect, seed lookup, shelf sync, triage, summarize, requests waiting in the request list (see "The request list" below), reruns of cut papers (Ollama only, at night; see "Reading cut papers whole: reruns" below), digest for today, then `finish_run` with the merged counts and errors (always, even after a crash; exit code 1 if a stage crashed) | the `runs` row, papers, summaries, `digests/<today>.md` |
| `collect` | fetch from arXiv and Hugging Face into the database | papers (status `new`) |
| `triage` | rank the `new` papers, keep the top N | statuses, scores, reasons, embeddings |
| `summarize` | summarise and critique every paper still waiting (`triaged`, `fetched`, or in today's digest without a summary) with the configured summary model (fetching arXiv HTML full text first; abstract only if there is none); `--backlog [--limit N]` adds older digests' unsummarized papers, and `summarize ID...` does exactly the papers you name (`--refetch` first re-downloads their full text) | full texts, summaries, statuses `fetched` then `summarized` (papers already in a digest, or skipped, keep their status) |
| `runs [--limit N]` | list recent runs, newest first | nothing |
| `serve [--host H] [--port 8001]` | browse the vault and vote/save from a phone with Datasette over Tailscale (needs the `serve` extra: `uv sync --extra embed --extra serve`, or `uv tool install --editable '.[embed,serve]' --force`; see [browsing.md](browsing.md)) | `votes`, `saves` (only when you press a button) |
| `requests add PAPER` | ask for one paper to be summarized (id in any spelling, arXiv URL, or part of the title); it is done in tonight's `run` or now with `requests run` (see "The request list" below) | one `requests` row |
| `requests run` | do the waiting requests now instead of at night (see "The request list" below) | summaries, request statuses |
| `requests list` | list every request, newest first, with its status, tries and last error | nothing |
| `review [--later]` | read each unreviewed digest paper in the terminal and vote up/down, skip or quit; `--later` goes through skipped papers (needs a terminal; see [review.md](review.md)) | `votes`, `skips` |
| `digest [--date YYYY-MM-DD]` | write the digest from papers already `triaged` (date defaults to today) | `digests/<date>.md`, `in_digest` |

**Abstracts-only mode.** If the backend is `none` (`rpipe model set-backend none`), the summarize stage is skipped on purpose: the run records `summaries_off=1`, the health line ends with `summaries off (abstracts-only mode)` and the errors field stays `none`. See `backends.md`.

**The summarize stage.** `rpipe run` checks that the summary backend is ready before this stage (for Ollama: the server is up and the model installed; for Claude Code: the `claude` program is found, logged in, and answers one tiny test call, which spends one real call a night; see `backends.md`). If not, it skips the stage, records the cause in the health line and `runs.errors`, and still writes the digest (abstracts only), so a stopped Ollama never costs you the night's digest. A single paper that fails (model error, network error fetching its text) keeps its status, gets its `error` and `retry_count` set, and the rest carry on.

**Reading mode.** Right after that check, a short `reading mode` stage decides how this run reads papers, from the `summarize_mode` setting (`rpipe model set-mode auto|whole|split`, default `auto`): whole (one call per paper) or split (notes on the front matter and the body, then combined). With `auto` and Ollama, the model is loaded (a note `loading MODEL to see where it sits (GPU or RAM)` comes first; the load waits at most 600 s) and Ollama is asked how much of it sits in GPU memory; any of it in RAM, a CPU-only machine or a context below 26,624 tokens gives split, otherwise whole; Claude Code reads whole. The stage prints one note with the choice and the reason, for example `split: part of the model runs from RAM (GPU share 0.62)`. If the share cannot be read the run reads whole and the note says why. When nothing is queued to summarize, the stage is skipped and the model is not loaded. `rpipe summarize` does the same. Details and worked examples: "Choosing the mode" in `summaries.md`.

**Which papers the stage works on.** Papers that are `triaged` or `fetched`, plus papers already listed in *today's* digest that have no summary yet (they stay `in_digest`; the summary is just saved). A paper that has failed 3 times (`retry_count` 3) is skipped and the errors say `gave up after 3 failed tries (last error: ...)`. Papers from an *earlier* day's digest are not touched by `rpipe run` or plain `rpipe summarize`; to catch those up, use `rpipe summarize --backlog` (below).

`rpipe summarize` on its own prints "Nothing to summarize" and exits 0 only when no paper is a candidate at all. A paper that has hit the 3-failure cap still counts as a candidate, so in that case the command does not print "Nothing to summarize": it checks Ollama, runs the stage, and the output lists `gave up after 3 failed tries (last error: ...)` for each such paper. It exits 1 with the cause if Ollama is unavailable. A pinned `llm_num_ctx` must be at least 8192; change it with `rpipe model set-context N` or `auto` (see `llm.md`).

```
$ uv run rpipe summarize
Summarized:
  fetched=3, summarized=3, abstract_only=0, body_cut=0, summary_failed=0
```

Illustrative output; your numbers will differ. The candidates are tried in order of parameter count, so an older but larger model (here `coder-16b`, 15.7B) can win over newer 14B ones; pin the one you prefer with `rpipe model set NAME`. The progress lines go to stderr, the final line to stdout. The context is the measured maximum (20480 here) less a 1024-token safety margin.

**Catching up: `rpipe summarize --backlog` and `rpipe summarize ID...`**

*What it does.* The nightly run only summarises new papers and today's digest. A paper that was listed in an earlier digest without a summary (the model failed that night, or the paper was pulled before summaries existed) would stay abstract-only forever. Two modes fix that:

- `rpipe summarize --backlog [--limit N]` does the normal work *and also* summarises up to N (default 10) `in_digest` papers from earlier digests that have no summary, newest digest first. It prints a preface first, for example `Backlog: 10 of 15 papers missing a summary (newest first; raise --limit for more)`, or `No older digest papers are missing a summary.` when there are none. `--limit` must be a positive whole number and only works with `--backlog`.
- `rpipe summarize ID [ID ...]` summarises exactly the papers you name (the ids shown in the digest), whatever their status (including `skipped`), ignoring the 3-failure cap, and replacing an existing summary if there is one (a re-summary). An unknown id is an error naming it, and nothing is summarised. It cannot be combined with `--backlog`.
- `rpipe summarize ID [ID ...] --refetch` first re-downloads and re-parses each named paper's arXiv HTML and replaces its stored full text (useful after the extractor improved, see [fulltext.md](fulltext.md)), then summarises. It is an error without ids or with `--backlog`. If a paper has no usable HTML any more, the run prints a note and uses the stored text (or the abstract if none). If the download fails (network error) and a text is stored, the stored text is used with a note; with nothing stored it is an ordinary failure, retried later.

*How it works.* The backlog is a database query (`papers_missing_summary`): status `in_digest`, digest date not today, no `kind='summary'` row, fewer than 3 failures, ordered by digest date then row, newest first, cut at N. The cap applies only to those backlog papers; the normal candidates are not counted against it. Each paper then goes through the same loop as always (`run_summarize`): stored full text or fetch it, summarise, critique, save. Status handling: only `triaged`/`fetched` papers become `summarized`; an `in_digest` or `skipped` paper keeps its status and just gains a summary. A digest file is only rewritten by `rpipe digest`, so after the run `rpipe summarize` lists the older digests that gained summaries, ready to copy:

```
Older digests do not show new summaries until you refresh them:
  rpipe digest --date 2026-11-21
  rpipe digest --date 2026-11-20
```

Why a hint and not an automatic rewrite: rewriting a past digest replaces its health line (the record of that night's run) with today's, which would falsify the history.
 Failures are recorded and the rest carry on; a usage limit stops the run with no failure recorded and the papers unchanged. Worked example: 15 `in_digest` papers with no summary, `rpipe summarize --backlog` summarises the 10 from the newest digests; run it again to do the other 5.

*Why it is built this way.* The cap exists because on a subscription backend (Claude Code) every summary spends plan quota, so one careless command must not burn a week's allowance; a local Ollama backend only costs time. The default of 10 is small on purpose and you raise it knowingly. `skipped` papers are excluded from `--backlog` (there are hundreds, and you never saw them) but can be named explicitly. Rejected: making plain `rpipe summarize` (and the nightly run) sweep all history, which would silently spend quota and change what `rpipe run` does.

*The counts.* `fetched` counts papers that have a full text to work from, whether it was already stored or was just downloaded; `abstract_only` counts papers with no full text (the summary rests on the abstract). So `fetched + abstract_only` is the number of papers attempted, and the `fetching <id>` note appears only for the ones that needed a download.

*Progress lines.* While a paper is being worked on, `rpipe summarize` (and the summarize stage of `rpipe run`) shows each step on stderr: `fetching 2611.26408` (only when the full text is not stored yet), `writing the summary for 2611.26408`, `writing the critique for 2611.26408`, and `retrying citations for 2611.26408` when the citation check sent the model back for a second try. On a terminal they are printed as permanent indented lines above the spinner (which shows the latest); in a log or pipe they are the same plain indented lines plus a "still waiting" heartbeat. When a paper finishes, one permanent line is printed: `[2/3] 2611.26408 done (summary + critique, 41s)`, `[2/3] 2611.26408 failed: <short reason>`, or `[2/3] 2611.26408 stopped: usage limit reached`. The count is over everything queued, backlog and named ids included, and the seconds are that paper's own time. These lines went to stdout before; they now go to stderr with the rest of the progress (the counts that `rpipe summarize` prints at the end stay on stdout; its error lines go to stderr). `run_summarize` takes an optional `reporter` (default none, silent) and emits the events; the old `on_progress` callback is gone.

*Check it.* `uv run pytest tests/test_summarize.py tests/test_e2e.py -q`. With the backend set to `none`, both modes refuse like plain `summarize` does.

*Where the code is.* `summarize` in `cli.py`; `run_summarize(..., backlog_limit=, paper_ids=)` in `summarize.py`; `papers_missing_summary`, `count_missing_summary`, `papers_by_ids` in `db.py`.

Model commands (`rpipe model setup`, `rpipe model set-backend NAME`, `rpipe model show`, `rpipe model set NAME|auto`, `rpipe model set-effort low|medium|high` (Claude Code only: how much it thinks per call, default `low`; see `backends.md`), `rpipe model set-url URL`, `rpipe model set-context N|auto`, `rpipe model set-embedding NAME` (the ranking model, see `triage.md`), `rpipe model check`, `rpipe model fit`) choose the backend (`setup` is the guided walkthrough, `set-backend` the scriptable switch) and view, change and test the model used for summaries; the rest are Ollama-specific, see `llm.md` and `backends.md`. By default the model and context are `auto`, so the first `rpipe run` after installing a model spends a few extra minutes probing which model and context fit your GPU; later runs reuse the cached answer. `rpipe model fit` re-probes on demand. `check` exits 1 with a fix hint if Ollama is not running or the model is not installed.

```
$ uv run rpipe model show
model: auto
server: http://localhost:11434
context window (num_ctx): auto
last choice: not probed yet; runs on first use, or `rpipe model fit`
$ uv run rpipe model fit
Probing Ollama: this loads and unloads models and can take a few minutes.
  trying dense-27b at 16384 tokens
  trying coder-16b at 16384 tokens
  searching the largest context for coder-16b
Using model coder-16b with a context of 19456 tokens
$ uv run rpipe model set qwen3:8b
model is now qwen3:8b. Check it works with `rpipe model check`.
$ uv run rpipe model set-context 16384
context window is now 16384 tokens.
$ uv run rpipe model check
Ollama is running at http://localhost:11434 and model qwen3:8b is installed.
```

Illustrative output; your numbers will differ.

Feedback commands (`rpipe vote ID up|down|clear`, `rpipe save ID`, `rpipe unsave ID`, `rpipe feedback`) record what you thought of a digest item. `ID` is the `id:` shown under each item (an arXiv id, also accepted as `2611.25005v2`, `arXiv:2611.25005` or an arxiv.org link) or part of the title; an ambiguous title shows a numbered list, an unknown one changes nothing. See `digest.md`.

```
$ uv run rpipe vote 2307.15043 up
$ uv run rpipe save 2307.15043
$ uv run rpipe feedback          # lists current votes and saved papers with titles, and the review-later count
$ uv run rpipe review            # step through unvoted digest papers: u, d, s or q (see review.md)
```

Gold-set commands (`rpipe gold add`, `rpipe gold list`, `rpipe gold remove`, `rpipe gold add-fact`, `rpipe gold edit-fact`, `rpipe gold remove-fact`) build the answer key of papers and key facts that `rpipe eval` checks summaries against; see `evaluation.md`.

```
$ uv run rpipe gold add 2307.15043          # paste a link, id or title; then type key facts, one per line
$ uv run rpipe gold list
$ uv run rpipe gold add-fact 2307.15043 "Suffixes found on open models transfer to closed models"
$ uv run rpipe gold remove 2307.15043
```

Illustrative commands; see `evaluation.md` for sample output.

Evaluation commands (`rpipe eval run`, `rpipe eval grade`, `rpipe eval report`) check summary accuracy against your gold set; see `evaluation.md`. `eval run` exits 1 if the gold set is empty or the model is unavailable; `eval run --mode auto|whole|split` picks the reading mode for that eval only.

```
$ uv run rpipe eval run      # summarize every gold paper, write eval/<date>.md
$ uv run rpipe eval grade    # answer y/n per paper; saved one by one
$ uv run rpipe eval report   # accuracy against the 90% pass mark
```

Illustrative commands; see `evaluation.md` for a sample report.

Only `run` writes a row to `runs`; the single-stage commands print their counts and errors instead (a deliberate choice: the table is the history of nightly runs). Only `run` and `triage` need the `embed` extra: the embedding model is created lazily (`Pipeline.make_embedder`), so `collect` and `digest` work on a machine without it.

**Exit codes.** `0` normally, **including when a source failed or Ollama was down** (summaries are then skipped; `rpipe summarize` alone exits 1 in that case) (the failure is recorded, see below). `75` when another pipeline command already holds the vault lock (the lock file is `.run.lock` in the vault; see "One pipeline job at a time" below for which commands take it). `1` when the vault is missing or has no `config.yaml`, a config file is invalid, a `profile` command is given bad input, the `embed` extra is missing, `--date` is not a date, or `vault.sqlite` was written by a newer rpipe (it stops and says to upgrade rather than touch it).

### Reading cut papers whole: reruns

**What it does.** The normal summarize pass reads every paper with a context window (how much text the model can read at once) that fits entirely in GPU memory, because that is fast. A few papers are longer than that window, so part of their core text is cut before the model reads them (a *body cut*; the summary says so). Such a paper also records how big a window it would need (`needed_ctx`, see `summaries.md`). After the normal pass, `rpipe run` reads those papers again, whole, at a bigger window. A bigger window only fits if part of the model sits in ordinary RAM (a *spill*), which makes the model several times slower. That is fine at night, so this *rerun* stage uses free RAM (never the last 4 GiB, so the computer never swaps) and must finish by the morning deadline (`rpipe schedule set-deadline`, default 07:00; see `scheduling.md`). Papers that do not make it keep their cut summary and are tried again on a later night. It runs only with the local Ollama backend: a cloud backend already reads long papers whole.

**How it works, step by step.** `spill.run_reruns`, called by `rpipe run` right after the summarize stage:

1. **Find the candidates.** There is no queue table: the stage looks at the stored summaries. A paper is a candidate when its summary has `body_cut`, a recorded `needed_ctx`, and its provenance shows it was written by the Ollama backend (a cloud backend reads long papers whole already) at a context smaller than what it needs and smaller than the model's limit (`min(trained context, 131072)`, read from Ollama's `/api/show`). A paper already rerun at that limit is not retried every night. Older summaries without these fields are ignored. No candidates: the stage prints nothing.
2. **Decide whether to start at all.** The normal pass stopped after a model timeout: all wait, because the model may be hung. No deadline tonight (a daytime run, or `off`): all candidates wait, and Ollama is not asked anything at all (the limit in step 1 is then taken as 131,072). Free RAM cannot be read (only Linux is read for now): all wait. Another model is loaded in Ollama ("GPU busy"): all wait, because the measurements below would be wrong. A normal context of 8,192 tokens or less: all wait (the memory growth cannot be measured, step 4). Not enough time left for even the smallest paper (the time estimate below): all wait, and nothing is loaded. In each of these cases the progress shows one note with the reason and a `waiting` line per paper.
3. **Group into buckets.** Each paper's rerun context is its `needed_ctx` rounded up to a multiple of 8,192 (`RERUN_STEP`), at most the model's limit. Papers with the same rerun context form a *bucket*. Changing the context means Ollama reloads the model (tens of seconds), so a bucket costs one load, not one per paper. Buckets go smallest first; inside a bucket, smallest paper first.
4. **Measure memory, not guess it.** The model is loaded at the normal context and Ollama's `/api/ps` gives its total size `S_fit` and the part in GPU memory `V_fit`. It is loaded once more at 8,192 tokens, giving `S_8k`. The difference is the memory for the extra context, so the bytes per token are `k = (S_fit - S_8k) / (normal context - 8192)`. At a context `c` the part in RAM is estimated as `S_fit + (c - normal context) x k - V_fit` (`spill.ram_part_bytes`).
5. **Check each bucket before loading it.** RAM: the estimate must be at most free RAM (Linux `MemAvailable`, read again before each bucket) minus 4 GiB. From the second bucket on, the model from the previous bucket still holds some RAM, which would make free RAM look smaller than it will be. So before reading free RAM the model is first unloaded, and the stage asks Ollama (`/api/ps`) every half second, for at most 10 seconds (`UNLOAD_WAIT`), until the model is no longer listed (if it still is, a note says so and the reading is taken anyway, which can only make the check stricter). The cost is one extra reload per bucket, which the bucket's load in step 6 needs anyway, since a change of context reloads the model. Rejected: adding the previous bucket's RAM part back to the reading, because part of that memory may already be counted as available (model files the system can drop from memory), so it could count the same memory twice and push the computer into swap. Time: the first paper's estimate must end before the deadline (below). If either fails, this bucket and all larger ones wait.
6. **Load and verify.** The model is loaded at the bucket's context and `/api/ps` is read again. Loading may take at most 600 seconds (one normal model call's limit) or the time left, whichever is shorter, so a hung Ollama cannot hold up the digest until morning. If the real RAM part is above the allowance from step 5, the model is unloaded at once and the stage stops (all remaining papers wait).
7. **Rerun the papers one by one.** Before each, the time check runs again. Each model call (summary, critique, any citation retry) may take up to the seconds left before the deadline at the moment it starts (at least 60). The new summary replaces the old one, with provenance `num_ctx` = the bucket context and `gpu_share` = the share of the model on the GPU (below 1.0 means it spilled).
8. **Unload** the model at the end, whatever happened.

**The time estimate.** Time is estimated per token of paper text (`needed_ctx - 4000`, the 4,000 being the room kept for instructions and the answer). After the first rerun tonight, the measured time of the last finished rerun is used. Before any rerun, the speed from `rpipe model bench` is used (a benchmark saved as `foo` counts for the model `foo:latest`, since Ollama treats the two names as the same model), multiplied by 3 (`SPILL_GUESS`) because a spilled model is slower than the benchmark saw. With no benchmark at all, the first rerun starts only if at least an hour (`FIRST_RERUN_MARGIN`) is left; after it, its own measured time is used.

**Worked example.** The model fits fully on the GPU at 57,344 tokens. Three papers were cut, needing 61,440, 63,488 and 90,112 tokens.

- Buckets: 61,440 / 8,192 = 7.5, rounded up to 8 x 8,192 = **65,536**; 63,488 / 8,192 = 7.75, also **65,536**; 90,112 / 8,192 = 11 exactly, so **90,112**. Two loads in all: the first two papers share one.
- Memory: at 57,344 tokens the model is 14 GB, all on the GPU (`S_fit` = `V_fit` = 14 GB); at 8,192 tokens it is 12 GB. So `k` = 2 GB / 49,152 tokens = about 40,700 bytes per token. At 65,536 tokens the RAM part is (65,536 - 57,344) x 40,700 = about 0.33 GB; at 90,112 tokens it is 32,768 x 40,700 = about 1.33 GB. With 20 GiB of RAM free, up to 16 GiB may be used, so both buckets are allowed. (Before the 90,112 bucket is checked, the 65,536 load is unloaded first, so its 0.33 GB in RAM does not shrink the reading.)
- Time: the benchmark says 60 seconds for a 20,000-token paper: 0.003 s per token, times 3 = 0.009 s. The first paper has 61,440 - 4,000 = 57,440 tokens of text, so about 517 s (9 minutes). The run reaches the reruns at 03:10 with the deadline at 07:00: 13,800 s left, so it starts. It really takes 720 s, so the estimate becomes 720 / 57,440 = 0.0125 s per token. The second paper (59,488 tokens) needs about 745 s and the third (86,112 tokens) about 1,080 s; all three finish by about 03:55. Had the run reached the reruns at 06:45 (900 s left), the first paper would start (517 s fits in 900 s) and take 720 s; the second would then need about 745 s with only 180 s left, so it and the third would wait for the next night.

The health line shows ` · rerun 2` (papers read whole tonight) and ` · rerun waiting 1` (candidates left for a later night: skipped, out of time or RAM, or failed). A rerun never falls back to reading the paper in parts (it calls `summarize_paper` with `fallback_above=None`): it exists to read the paper whole. A paper that needs more than the model's context cap (`spill.model_cap`) is read in parts in the normal pass instead of being cut (when the deadline is set, not `off`, `rpipe run` passes the cap as `fallback_above`, even in a daytime run: a paper it cuts is rerun on a later night), so it never becomes a rerun candidate; see "Which way a paper is read" in `summaries.md`. The progress output has one `rerun` stage with a note for each load and measurement and one line per paper: `done`, `waiting` or `failed`.

**When a rerun fails.** The old (cut) summary stays; the paper's `retry_count` and `error` are not touched (a rerun is a bonus, not the paper's normal summary). The errors get `rerun <id>: <reason>`. A timeout stops the whole stage, since a hung model would make every later paper wait too. Any other problem with the stage itself (Ollama not answering, or an unexpected error) stops the stage with `rerun: <reason>` in the errors; the papers already rerun that night still count, the model is unloaded, and the digest is always written.

**Why it's built this way.**

- **Normal papers stay fast.** The normal pass still demands a context that fits entirely on the GPU; reruns are the only place the model spills, and only for the few papers that need it. Rejected: a bigger context for every paper (slows every paper, and still too small for the longest).
- **Buckets of 8,192 tokens.** Every change of context reloads the model, so one context per paper would mean one reload per paper. Rounding to 8,192 lets papers of similar length share a load while not wasting much memory.
- **Measured memory, not a table of model sizes.** Two loads give the real numbers for this model, this Ollama and its settings; a table would go stale. The load is checked again afterwards because the estimate assumes the GPU part stays the same.
- **Recomputed from summaries, no new table.** The stored summary already says whether it was cut and how much it needs, so nothing can get out of step with it.
- **A deadline, not a count.** You care when the computer is free again, not how many papers it read. Rejected: "at most N reruns a night", which says nothing about time on a slow machine.

**How to check it.** `uv run pytest tests/test_spill.py -q` (fake Ollama, fake clock and fake RAM reading: candidates, buckets, the memory formula, a full night, the deadline stopping midway, no RAM reading, free RAM read only after the previous bucket's model is unloaded, a benchmark saved without the `:latest` tag, a load that uses more RAM than estimated, another model loaded, a failure and a timeout, and the `run` wiring). After a real night: the health line, and `rpipe show ID` for a rerun paper (its summary no longer ends with "part of it was left out"). Today's digest is written after the reruns, so it shows the new summaries; a digest file from an earlier day is not rewritten (it is that night's record), but the new summary is what `rpipe show` and the browser show.

**Where the code is.** `src/research_pipeline/spill.py` (`rerun_candidates`, `buckets`, `ram_part_bytes`, `run_reruns`, `_unload_and_wait`, constants `RERUN_STEP`, `SPILL_GUESS`, `FIRST_RERUN_MARGIN`, `MIN_CALL_TIMEOUT`, `UNLOAD_WAIT`, `UNLOAD_POLL`), its call in `_run_locked` and the health-line additions in `cli.py`, `ollama_fit.unload`.

### The request list: work the app asked for

**What it does.** The app half of rpipe (today `rpipe requests add PAPER`; later a button in the app, both calling `library_api.request`) never runs the model itself. When you ask for a paper to be summarized, it writes a `pending` row in the `requests` table (see "The request list" in [data-model.md](data-model.md)) and the pipeline does the work: every night inside `rpipe run`, or straight away with `rpipe requests run`. This is called **draining** the request list (working through it until nothing is left to do). `rpipe requests list` shows every request and how it went.

**How it works.** `requests_queue.drain_requests` does this, step by step:

1. Rows left `running` (a run died while working on them) go back to `pending` (`db.reset_running_requests`). The try they used still counts, so a row whose third try was cut short has no tries left: it is marked `failed` with the error `stopped part-way through 3 times (the run was interrupted or crashed); gave up` and counted in `requests_failed`, instead of sitting `pending` for ever.
2. It takes the `pending` `summarize` rows that have had fewer than 3 tries (`MAX_SUMMARY_TRIES`), oldest first, and for each one:
   - marks it `running` and counts one try (`db.mark_request(..., add_try=True)`);
   - summarizes that one paper with `run_summarize(paper_ids=[id])`, exactly what `rpipe summarize ID` does: any status, an existing summary is replaced, and a paper's own retry cap is ignored because you asked for it;
   - **summarized** → `done`, `result` = `{"summarized": true}`, `error` cleared;
   - **failed** (network error, model error, the paper is not in the vault) → back to `pending` with the error line, or `failed` if this was the third try;
   - **usage limit reached** (Claude Code) → back to `pending` with the try given back (not the request's fault), and the drain stops: the next request would hit the same limit;
   - **model timed out** → counted as a failed try like any failure, then the drain stops: a hung model would make every later request wait out the full timeout too.
3. **The morning deadline.** In the nightly `rpipe run`, before *starting* each request the drain looks at the clock. If the morning deadline (`night_deadline`, default 07:00, see "The morning deadline" in [scheduling.md](scheduling.md)) has been reached, it stops: the rest stay `pending` with no try used, the stage shows `N requests wait for a later night: the morning deadline 07:00 has passed`, and the health line gets `requests: stopped at the night deadline 07:00; N waiting` (worded like the rerun stage's deadline notes). A request already started is never cut off, so the drain can end a few minutes past the deadline. `rpipe requests run` is a hand run, so it passes no deadline and never looks at the clock; neither does a run with no deadline tonight (set to `off`, or more than 12 hours away, as in a daytime run).
4. It counts `requests_done` and `requests_failed` (gave up after 3 tries, including tries cut short) for this drain, and `requests_waiting` (pending rows left), and returns them with the error lines for the health line.

The **retry rule** in one line: a request gets 3 tries; a usage-limit stop does not use one up; a request that failed 3 times is left `failed` and never tried again (ask again to queue a fresh one). `requests.tries` is the counter that matters; the paper's own `retry_count` still goes up on a failure (as with `rpipe summarize ID`) but the drain does not look at it.

Worked example. On Monday you ask for `2611.25005` (row 7) and `2611.30001` (row 8). That night:

```
summarize…           (the normal stage: tonight's kept papers)
requests…
  writing the summary for 2611.25005
  writing the critique for 2611.25005
  [1/2] 2611.25005 done
  fetching 2611.30001
  [2/2] 2611.30001 failed: arXiv returned 503
```

You asked for them with:

```
$ rpipe requests add 2611.25005
Request 7: summarize 2611.25005.
It runs during tonight's `rpipe run`, or now with `rpipe requests run`. `rpipe requests list` shows how it went.
$ rpipe requests add "https://arxiv.org/abs/2611.30001v2"
Request 8: summarize 2611.30001.
It runs during tonight's `rpipe run`, or now with `rpipe requests run`. `rpipe requests list` shows how it went.
$ rpipe requests add 2611.25005
Request 7 to summarize 2611.25005 is already waiting; nothing new was added.
It runs during tonight's `rpipe run`, or now with `rpipe requests run`. `rpipe requests list` shows how it went.
```

The third line asked again for a paper that was still waiting, so it got request 7 back instead of a second copy.

Row 7 is `done` (1 try). Row 8 is `pending` with 1 try and the 503 as its error; the health line says `requests waiting 1`. Tuesday night it works: `done`, 2 tries. Had it failed on Tuesday and Wednesday too, it would be `failed` after Wednesday's third try and the health line would say `requests failed 1`.

**In `rpipe run`** the order is summarize → requests → reruns → digest. The drain uses the same reading mode and `fallback_above` as the summarize stage, so a requested paper that had to be cut is read whole by the rerun stage like any other. A quiet night with no new papers still loads the model when requests are waiting (the summarize stage counts them as work). Only requests that can still run count: `requests_queue.runnable_requests` (pending or running, `summarize`, fewer than 3 tries) is the one check used by `rpipe run`, `rpipe requests run` and the drain itself, so a request with no tries left never makes a night load the model for nothing. The drain is **skipped** when the summarize stage just stopped on a usage limit or a model timeout: the model has stopped answering, so trying requests would only use up their tries; the rows stay `pending` with their tries untouched and the health line shows `requests waiting N`. A drain that times out is passed on to the rerun stage, which then skips (as after a summarize timeout). With the `none` backend, the model unavailable, or a usage limit hit before the first call, no drain runs, but the health line still shows `requests waiting N` so you can see they are queued. If the drain itself crashes (for example a database error), the run records `requests: <message>` in its errors and carries on with the reruns and the digest; the night still exits 0, like a rerun problem.

**Progress.** The drain is its own `requests` stage with one `[i/N]` step per request (outcome `done`, `failed` or `stopped`) and, above each, the notes from the summary (`fetching …`, `writing the summary for …`). The inner one-paper summarize run would otherwise show its own `summarize` stage with a `[1/1]` line for every request; a small adapter (`_NotesOnly`) passes only its notes into the `requests` stage. With nothing queued the stage is not shown at all.

**Commands.**

```
$ rpipe requests add 2611.30001
Request 8 to summarize 2611.30001 is already waiting; nothing new was added.
It runs during tonight's `rpipe run`, or now with `rpipe requests run`. `rpipe requests list` shows how it went.
$ rpipe requests list
8  pending  summarize  2611.30001  1  2026-11-23T09:12:40+00:00  arXiv returned 503
7  done     summarize  2611.25005  1  2026-11-23T09:12:02+00:00
$ rpipe requests run
Requests:
  requests_done=1, requests_failed=0, requests_waiting=0
```

`rpipe requests add PAPER` (not locked, no model) finds the paper the way `rpipe vote` does (`_resolve_paper`: an id in any spelling such as `2611.25005v2`, `arXiv:2611.25005` or an arxiv.org URL, else part of the title, with a numbered list to pick from when several titles match), then calls `library_api.request(conn, "summarize", {"paper_id": ...})`. It prints the request id and when it will run. If the same paper is already waiting (or being worked on) it says `already waiting` and gives that request's id; a paper not in your vault gives `No paper in your vault matches '...'. ... Nothing was changed.` and exit 1, with no row added. It only adds one row, a single short write, so it runs safely beside the nightly job. `rpipe requests list` (not locked, no model) prints every request newest first: id, status, kind, paper id, tries, when it was asked, and the last error if any; `No requests.` when there are none. `rpipe requests run` takes the pipeline lock, so a pipeline command started meanwhile is told `rpipe requests run is already working on <vault>; nothing was started.`; it needs a model (exit 1 with the `none` backend, or when the model is unavailable), says `No requests waiting.` and stops if no request can still run, else picks the reading mode like `rpipe summarize` and drains with progress.

**Why it's built this way.**
- **`requests.tries` is the counter, not the paper's `retry_count`.** A request is something you asked for; its own row is where you look to see how it went, and `rpipe requests list` shows it. Rejected: reusing the paper's counter (a paper that failed three times in the nightly stage could then never be requested again).
- **A usage limit gives the try back; a timeout does not.** A limit says nothing about the paper; a timeout might (a huge paper) and costs real time, so it counts like the nightly stage counts it.
- **Requests after the summarize stage, before reruns.** Tonight's new papers come first (they are the digest); requests are extra. Running before the reruns lets a cut requested paper be reread whole the same night. Rejected: a separate nightly timer for requests (two jobs fighting over one lock and one GPU).
- **The drain stops at the morning deadline, the normal summarize stage does not.** Tonight's new papers are the digest, so they always run; requests are extra and can wait a day, like reruns. Checking only before a request starts means a summary is never thrown away half-written. Rejected: a time budget per request (a slow paper would be cut off and its try wasted).
- **`requests add` is an app command, not locked.** It writes one row and never touches the model, so it must work while the nightly run holds the lock (you may want to queue a paper while it runs). Rejected: making it run the summary straight away (that is `rpipe requests run`, which needs the lock and the GPU).
- **One step per request, not a stage per request.** Twenty requests would otherwise print twenty `summarize` stages of `[1/1]`.

**How to check it.** `uv run pytest tests/test_requests_queue.py tests/test_cli_requests.py -q` (done, a stale `running` row, three failures then `failed`, a usage limit keeping both rows pending with no try used, a timeout stopping the drain, the progress events, a request whose last try was cut short marked `failed`, the deadline stopping the drain before the first and between requests (fake clock), no clock read without a deadline, `requests add` (a row added, a versioned id and a title, an unknown paper, a repeat giving the same id, no lock), the deadline passed by `rpipe run` and not by `rpipe requests run`, `requests waiting` on nights with no model, the nightly wiring including a quiet night, the skip after a usage limit and after a summarize timeout, a drain timeout passed on to the reruns, a crashing drain recorded while reruns and the digest still run, the list output and the lock).

**Where the code is.** `src/research_pipeline/requests_queue.py` (`drain_requests`, `runnable_requests`, `GAVE_UP_CUT_SHORT`, `_NotesOnly`); `db.mark_request`, `db.reset_running_requests`; `library_api.request`, `library_api.open_request_ids`; in `cli.py`, the `requests` stage in `_run_locked`, `_count_waiting_requests`, `requests_add`, `requests_run`, `requests_list` and the health-line counts.

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

`counts` merges the collect counts (`inserted`, `updated`, `unchanged`, `source_errors`) with the triage counts (`triaged`, `skipped`, `embedded`) and, since Phase 2, the summarize counts (`fetched`, `summarized`, `abstract_only`, `body_cut`, `summary_failed`, and since Part B.6 `in_parts`: cut papers read in parts by the map-reduce fallback) and, with Ollama, the rerun counts (`rerun`, `rerun_waiting`); see `sources.md`, `triage.md` and the summarize stage above. The sample row above predates Phase 2 so it has no summarize counts, and the sample health line shows `summarized 0` for the same reason. Look at the history with `rpipe runs` (below) or `sqlite3 <vault>/vault.sqlite "select * from runs"`.

### Crash-safe runs and `rpipe runs`

**What happens if a stage crashes.** Source failures, a missing model and a failed seed lookup are already handled one by one (see above). Anything *else* unexpected (a bug, a full disk) used to leave the run row with no `finished_at`. Now `run` wraps the stages: the exception is recorded in `runs.errors` as `<stage>: <message>` (the same plain-string shape as `arxiv: arXiv API returned HTTP 500`, with the stage being `collect`, `triage` or `summarize`; the rerun stage catches its own problems as `rerun: ...` and never counts as a crash), the later stages are skipped, `run` still tries to write the digest from whatever is in the database (so the health line shows the error), `finish_run` always executes, and the command exits with code 1 so cron or systemd notices. If even the digest fails, that is recorded as `digest: ...`.

**What is guaranteed.** Both a crash and Ctrl-C finish the run row (`finish_run` sits in a `finally`, so it executes exactly once whichever way the command ends). After Ctrl-C the error is `<stage>: interrupted`, no digest is attempted (you wanted out), and the interrupt then propagates, so the exit code is the usual one for an interrupt. Counts are kept for stages that completed before the failure; the partial counts of the stage that crashed mid-way are lost (stage functions only return counts when they finish). `SIGTERM` (what `systemctl stop` and a plain `kill` send) is treated like Ctrl-C while `run` executes, so it is recorded the same way. An interrupt that lands while the digest is being written is recorded as `digest: interrupted`. Failing to load the embedding model is recorded as `embed: ...` (the run row is created before the model is loaded, so this and every later failure leaves a record). Only a hard kill (power cut, `kill -9`) leaves `finished_at` empty. If `finish_run` itself fails (for example a full disk) while another error is already propagating, the failure is printed as `warning: could not record the run: ...` and the original error still wins; if nothing else went wrong, the `finish_run` failure is raised.

**One pipeline job at a time.** Every command that does pipeline work takes the same vault lock before it starts: `flock` (an operating-system file lock, see the glossary) on `<vault>/.run.lock`, non-blocking, so a second job never waits, it stops. While a command holds the lock, the lock file contains that command's name (the **lock holder**, for example `rpipe run`); the file is emptied when the command ends. A refused command reads the name and prints it, then exits at once with code `75` (the standard "try again later" code), without creating a `runs` row or touching the database:

```
$ rpipe summarize
rpipe run is already working on /home/you/research-vault; nothing was started. Wait for it to finish (see `rpipe runs`).
```

Worked example: the nightly run starts at 03:00 and is still summarizing at 07:30 when you type `rpipe model tune`. Without the lock, tune would restart Ollama under the run and every remaining paper would fail. With it, tune prints the line above (naming `rpipe run`) and changes nothing; you run it again once `rpipe runs` shows the night finished. The same happens the other way round: a `Persistent=true` catch-up run that wakes up while your hand-started `rpipe summarize --backlog` is going exits 75 naming `rpipe summarize`, and the next night runs normally. The operating system drops the lock when the process ends for any reason (crash, `kill -9`, SIGTERM), so a stale lock file never blocks a later run; the file itself is harmless and stays in the vault. If the file is empty while locked (an older rpipe holds it, or the holder has not written its name yet) the message says `unknown is already working on ...`.

Which commands take the lock:

| Command | Locked? | Why |
| --- | --- | --- |
| `run`, `collect`, `triage`, `digest` | yes | pipeline stages: fetch into the vault, load the embedder, rewrite `papers` and the digest |
| `summarize` (all forms, including `--backlog` and `--refetch`) | yes | uses the model (GPU memory) and fetches full text |
| `eval run` | yes | summarizes the gold set with the model |
| `model fit`, `model check`, `model bench` | yes | load and unload models in Ollama; a probe during a run would push the run's model out of GPU memory |
| `model tune` (and `--undo`) | yes | restarts the Ollama service, which would break a running job |
| `model recommend` | yes | can download a model and re-measure its context |
| `model setup` | yes | can download, measure and test a model |
| `profile suggest` | yes | uses the model |
| `gold add`, `gold suggest`, `gold auto`, `gold draft` | yes | fetch from arXiv or call the judge; grouped with the other gold-set builders so one job builds the set at a time |
| `requests run` | yes | uses the model (GPU memory) and fetches full text |
| `shelf add`, `shelf sync` | yes | load the embedder and rewrite shelf rows; `rpipe run` also syncs the shelf, so two syncs could collide |
| `vote`, `save`, `unsave`, `feedback`, `review`, `show`, `runs`, `serve`, `shelf list`, `shelf remove`, `requests add`, `requests list` | no | app commands: quick reads and single-row writes that are safe beside a running job |
| `profile show`, `profile add-seed`, `profile set-*`, `model show`, `model set*`, `model scan`, `model catalog show`, `model catalog update`, `gold list`, `gold remove`, `gold add-fact`, `gold edit-fact`, `gold remove-fact`, `eval grade`, `eval report`, `schedule *`, `sources *`, `init`, `plugins` | no | settings and read-only commands. `profile add-seed` does look the paper up on arXiv, but it writes only `profile.yaml`, never the database, so it cannot collide with a run |

A locked command takes the lock right after it opens the vault (`_open`) and before anything that leaves the process: no network request, no model or embedder load, no Ollama probe or restart happens while another job holds the lock. **A command lets go of the lock while it waits for you.** Interactive locked commands (`gold add`, `gold draft`, `model tune`, `model setup`, `model recommend`, `profile suggest`) ask questions between stretches of work. Every question goes through `_prompt` or `_confirm` in `cli.py`, which releases the lock (and empties the file) just before asking and takes it back right after you answer. So the lock is held only while the command is actually working (fetching, calling the model, downloading, restarting Ollama), never while a question sits on screen. Worked example: at 23:00 you start `rpipe gold draft 2401.00001`, it drafts the facts (lock held), then asks `Keep, edit or drop?` and you go to bed. At 03:00 the nightly run finds the lock free, runs and finishes. If you answer while the run is still working, the command cannot take the lock back, so it stops with `rpipe run is already working on <vault>; nothing further was started.` and exit 75; whatever it had saved before the question stays saved, and you run it again later. Outside a locked command (for example the `rpipe init` wizard, which is not locked) `_prompt` and `_confirm` behave exactly like Typer's own prompts.

The `sudo` password prompt during `model tune` is the one exception: it belongs to the restart itself, so the lock stays held (sudo gives up on its own after a few minutes).

Unlocked commands can still write to `vault.sqlite` while a job runs. SQLite allows one writer at a time; the database is in WAL mode (readers never wait) and every connection has a **busy timeout** of 5 seconds (`open_db` passes `timeout=5.0`): a write that finds the database busy retries for up to 5 s before failing with "database is locked". The pipeline's writes are short, so in practice a vote or save during a run just waits a moment.

`tests/test_run_lock.py` checks the lock itself (the holder is recorded, read back by a refused caller, cleared on release and after a crash, `unknown` when empty) and the waiting rule on `rpipe gold add`: at each question another job can take the lock, after the answer the lock is held again, and a job that took it during a question stops the command with `nothing further was started`; `test_locked_commands_stop` in `tests/test_runs.py` holds the lock as `rpipe run` and invokes every locked command in the table: each must exit 75 naming `rpipe run`, with no new `papers` or `runs` rows and no digest, and every network, model, embedder and Ollama entry point is replaced by one that fails the test, so a command that reached one before the lock would fail loudly.

**A hung model stops after one timeout.** The first time the model does not answer within its time limit (`LlmTimeout`; 600 s, or more on a machine `rpipe model bench` measured as slow, see "The time limit for one call" in `llm.md`), `run_summarize` records the paper's failure, adds `summarize: stopped calling the model after the first timeout; remaining papers show abstracts only` to the errors, and stops calling the model for the rest of that run (the rerun stage is skipped too: its cut papers wait for another night). Collecting, triage and the digest carry on; the unsummarized papers keep their status and are retried on the next run. Rejected: continuing, which would make every remaining paper wait out the full timeout (hours for a night's batch).

**`rpipe runs [--limit 10]`** prints one line per run, newest first: start time (UTC), how long it took (or `did not finish`), a counts summary (`new`, `kept`, `summarized`) and the number of errors, for example `2026-11-21T19:11:05+00:00  11s  new 339, kept 15, summarized 15  errors 0`.

**A run that never finished.** If the process was killed outright (power cut, `kill -9`) nothing can record anything, so `finished_at` stays empty. The next `run` notices that the run immediately before it has no `finished_at` and adds `previous run did not finish` to the end of the digest's health line. Only the immediately preceding run is checked (the current run is itself unfinished while it writes the digest, so it is excluded), so the warning appears once and clears after the next healthy run.

### Running it nightly

To run it every night automatically, use `rpipe schedule install` (a systemd user timer; see [scheduling.md](scheduling.md)). Running twice on the same day is safe: no paper is stored twice and the day's digest is rewritten with everything from both runs (`digest.md`, "Same-day re-runs").

## Why it's built this way

- **One command that records itself**: `run` owns the `runs` row, so counts from every stage and every error end up in one place. The individual stage functions return counts and errors rather than finishing the run.
- **A failed source does not fail the run**: a nightly job that exits non-zero because one website was down would train you to ignore failures. The error is written to the digest's first line and to `runs.errors` instead; exit code 1 is kept for "nothing could run". Exit `75` means only "another pipeline job was in progress".
- **`flock`, not a PID file**: the OS releases a `flock` when its owner dies, so there is no stale-lock cleanup to get wrong. Rejected: a PID file (needs liveness checks and can go stale after a crash) and a blocking lock (the catch-up run would sit waiting for hours).
- **One lock for every pipeline command, not just `run`**: before Phase 7 only `run` took the lock, so a hand-started `rpipe summarize` or `rpipe model tune` could run beside the nightly job, two processes fought over GPU memory, and a tune restarted Ollama mid-run. One lock, one rule: anything that does pipeline work, uses the model or GPU, restarts Ollama or fetches into the vault takes it. Rejected: a separate lock per resource (GPU, Ollama, database), which is harder to explain and still lets two jobs interleave their writes to `papers`; and making the app commands (`vote`, `save`, `review`) wait for the lock, which would block a quick vote for hours during a run.
- **Let go of the lock at every question, not around hand-marked work blocks**: an unanswered question must never cost a night's run, which is worse than the collision the lock prevents. Rejected: marking each stretch of work in every interactive command by hand, because the questions sit deep inside helpers shared with the unlocked `init` wizard, and a new question added later would silently hold the lock again. Routing every question through `_prompt`/`_confirm` makes the rule hold everywhere at once. The cost: an answer given while another job works is refused and the step must be redone.
- **The lock file names its holder**: "another job is running" leaves you guessing whether it is the nightly run (wait) or a command you forgot in another terminal (go and stop it). Writing the command's name into the lock file costs nothing and the refused command can read it. It is emptied on release so a leftover name never misleads. Rejected: also writing the process id (a reader would be tempted to `kill` it; `rpipe runs` is the safer place to look).
- **A 5-second busy timeout, written out**: it is Python's default, but now that the app commands write while a pipeline job runs, the value matters and is stated where the database is opened rather than left implicit.
- **`build_pipeline` as the single seam**: `cli.py` builds the real sources, a lazy embedder factory, seed lookup, a lazy model-client factory (`make_llm`) and the full-text fetcher in one function. The end-to-end tests replace that one function with fixture-backed stubs, so the *real* CLI, database, ranking and digest code run offline.
- **Optional `embed` extra**: keeps installs and tests light. Rejected: making PyTorch a hard dependency.
- **No `feedparser` dependency**: the arXiv response is parsed with the standard library (see `sources.md`); RSS is a later phase and can add the library then.

## How to run / check it

```
uv run rpipe run --vault /tmp/try-vault   # after init, on a scratch vault
uv run pytest -q                                      # whole suite, offline
uv run pytest tests/test_e2e.py -q                    # the end-to-end tests
uv run ruff check .                                   # lint (settings in pyproject.toml)
```

Lint is `ruff` (a fast Python linter, installed with the dev group by `uv sync`). It looks for real mistakes such as unused variables and imports, ambiguous names like `l`, and unsorted imports; it does not reformat code. CI runs `uv run ruff check .` before the tests, so run it locally first. `uv run ruff check . --fix` applies the safe automatic fixes.

`tests/test_e2e.py` runs the real CLI on a temporary vault with fixture sources and the test embedder, and checks: the digest is written and the top paper is the on-topic one; the `runs` row is finished; a second run the same day adds no duplicates; an uninitialised vault exits 1; a failing source exits 0 with the error recorded; a failed seed lookup does not stop the run; a missing `embed` extra gives a clear error; with a scripted fake model the digest contains a cited summary; with the model unavailable `run` still exits 0, writes the digest and names the cause in the health line; `rpipe summarize` runs the stage alone. `tests/test_spill.py` checks that `run` calls the rerun stage only for the Ollama backend, that a crash in it becomes a `rerun: ...` error while the digest is still written, and the `rerun` / `rerun waiting` health-line parts; `tests/test_summarize.py` checks the `in parts` part.

### Progress

**What it does.** Long steps (collecting, ranking, summarising) tell you what they are doing, so a quiet terminal never means "frozen". Progress always goes to stderr; what a command prints on stdout is unchanged.

**How it works.** Stages do not print. They emit small progress events (`start`, `step`, `note`, `done`; see `progress.py`) to a reporter, and the CLI picks how to show them with `make_reporter()`:

- On a terminal (stderr is a TTY) `LiveRenderer` redraws one line in place: a spinner, the elapsed time and, only when the number of items is known, a bar. Finished items (for example each paper) and every note (a `trying ...` line, a `fetching ...` line) are printed above it as permanent lines, and the spinner text follows the latest note.
- Anywhere else (`journalctl --user -u research-pipeline`, a pipe, a file) `LineRenderer` writes plain lines with no colour codes and no carriage returns, for example `collect…`, `  arxiv ok: 100 papers`, `collect done (0s)`, `summarize…`, `  writing the summary for 2611.25005`. The per-paper summarize line keeps its exact form: `[2/3] 2611.25005 done (summary + critique, 41s)`.
- Heartbeat: if nothing has been written for 30 seconds while a stage is open, a background thread prints `still waiting: writing the critique for 2611.25005 (1m30s)`. A single model call can take minutes; the heartbeat shows the job is alive. Elapsed time reads `41s` under a minute, then `1m30s`.

**Why it's built this way.** Events are plain data, so the same events can later drive a different display (the planned app) and tests can check them without any terminal. Rejected: printing from inside the stages (hard to test, and no way to change the display) and a progress bar always on (carriage returns and colour codes make journals unreadable). The heartbeat thread is a daemon and is stopped by `close()` (called by `cli._reporter()` even after an error or Ctrl-C), so it can never keep the process alive. Library progress bars are switched off during the embedding model load: sentence-transformers and huggingface_hub draw a `Loading weights` bar with carriage returns and print an "unauthenticated requests ... HF_TOKEN" warning, which turn into garbage in a journal or pipe. `SentenceTransformerEmbedder` sets `HF_HUB_DISABLE_PROGRESS_BARS=1`, `TRANSFORMERS_VERBOSITY=error` and `HF_HUB_VERBOSITY=error` (with `os.environ.setdefault`, so a variable you export yourself wins) before importing the library. It is always on, not only off a terminal, because our `embedding model: …` stage line and heartbeat already cover the load.

**How to run / check it.** `uv run pytest tests/test_progress_render.py -q`. By eye: run a command on a terminal, then again with `2>&1 | cat` to see the plain form.

**Where the code is.** `src/research_pipeline/progress_render.py` (`format_line`, `LineRenderer`, `LiveRenderer`, `make_reporter`), `cli._reporter()`, `embed.quiet_model_load`, and `tests/test_progress_render.py`, `tests/test_embed_quiet.py`.

### Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `No vault found at ... (no config.yaml). Run rpipe init first.` (exit 1) | The vault does not exist yet, or you pointed `--vault` / `RESEARCH_VAULT` at the wrong folder. Run `init`, or fix the path. |
| `invalid settings` / `invalid YAML` naming a file (exit 1) | One of the two settings files holds an invalid value (this should not happen if you only use `rpipe profile ...`, which refuses bad input). The message starts with the file path and says which field. Every `rpipe profile` command refuses to change a file that is already broken (and tells you which file), so this is the one case where you correct the named field in the file itself; the file reference in `vault-and-config.md` lists every field and its allowed values. After that the commands work again. |
| `sentence-transformers is not installed; reinstall with the extra: ...` (exit 1; for `rpipe run` it is also recorded as `embed: ...` in `runs.errors`, so `rpipe runs` and the digest health line show it) | The `rpipe` that ran has no embed extra. Reinstall: `uv tool install --editable '.[embed]' --force` (plain `rpipe`, and the nightly timer), or `uv sync --extra embed` (repo use). `rpipe schedule install` refuses to run from an environment without it. |
| Health line says `errors: arxiv: arXiv API returned HTTP 5xx` (or a timeout) | arXiv (or Hugging Face) was down. The digest was built from the other source. Re-run later: `run` is safe to repeat. |
| Health line says `seeds: no paper found for 0000.00001, ...` | Those seed ids are not real arXiv papers, for example a mistyped id. Remove it with `rpipe profile remove-seed` and add the right one with `rpipe profile add-seed` (`rpipe profile show` numbers the seeds). Ranking continues using your topics and any seeds that did resolve. |
| Health line says `seeds: could not fetch ...: <reason>` | The seed lookup failed (network). Ranking used the seeds it already had; it retries next run. |
| Health line says `summaries skipped, model unavailable: cannot reach Ollama at ...` | Ollama is not running. Start it with `ollama serve` (or the desktop app), check the URL with `rpipe model show` (change it with `rpipe model set-url URL`) and test with `rpipe model check`. Tonight's digest was still written, with abstracts only. **To recover the same day:** start Ollama (`ollama serve`), then run `rpipe summarize` and `rpipe digest` (or just `rpipe run` again). The papers in today's digest get their summaries and the file is rewritten. If you wait until the next day, those papers are not summarised (accepted limitation for `rpipe run`); only newly kept papers are. You can still catch them up later with `rpipe summarize --backlog`. |
| Health line says `model unavailable: model 'qwen3:14b' is not installed: run ollama pull ...` | The model is not downloaded. Run the `ollama pull` command shown, or pick another with `rpipe model set NAME`. This is the message for a model you pinned. With the default `auto` you instead see `model unavailable: no usable model is installed in Ollama (embedding-only models cannot write summaries). Install one with ollama pull NAME, e.g. ollama pull qwen3:14b.` |
| Health line says `no installed model fits fully in GPU memory` | Free GPU memory and run `rpipe model fit`, or pin **both** a smaller model and a context (`rpipe model set NAME`, `rpipe model set-context N`); see "Troubleshooting" in `llm.md`. The outcome is remembered, so it is reported at once each night until you change something or run `rpipe model fit`. |
| Health line shows `summary failures N` or `abstract-only N` | `summary failures`: the model errored or timed out on N papers (see the `summarize <id>: ...` entries in the errors); they show abstracts only. `abstract-only`: N papers have no arXiv HTML version (non-arXiv ids, or too new), so their summary rests on the abstract and says so in the digest. |
| Health line shows `in parts N` | N papers had to be cut to fit the context and no rerun could read them whole (they need more than the model's context cap, or reruns are off), so they were read in parts (*map-reduce*: size-based chunks, notes on each, then the summary and critique from the notes). Their core text was all read (unless the notes themselves were too long and had to be trimmed; then the paper also counts in `body cut`); appendices were left out. Nothing to fix. `rpipe show ID` says `read in parts: map-reduce, N notes calls` for such a paper. Papers read in parts because you chose split mode are not counted here. See "Which way a paper is read" in `summaries.md`. |
| Health line shows `rerun waiting N` | N cut papers could not be read whole tonight: no deadline (a daytime run, or `rpipe schedule set-deadline off`), not enough time or free RAM, another model loaded in Ollama, or a failed rerun (see the `rerun ...` entries in the errors). They keep their cut summary and are tried again on a later night; nothing to do unless it never goes down. See "Reading cut papers whole: reruns". |
| Digest says "Nothing new matched your profile today." | No paper was `triaged`: either no new papers arrived since the last run (they are already in an earlier digest) or every source failed (check the health line). |
| Health line says `arxiv: reached max_results (N) before already-seen papers; some may be missed` | arXiv paging hit `arxiv_max_results` without reaching a paper you already had. Expected on the first run (empty database). Otherwise you were away a long time and the cap is too low; raise it with `rpipe sources set-max-results N` (50 to 5000). |
| Health line says `written from stored papers` | You re-ran `digest` on its own; it replaced that day's original health line. The original errors remain in the `runs` table. |
| `could not load embedding model <name>: ...` (exit 1) | The model failed to download or load (offline, disk, broken torch install). Fix the cause and re-run. |
| The digest has fewer papers than `top_n` | Fewer than `top_n` new papers arrived since the previous run. |
| Ranking looks off | See `triage.md`: add representative seeds, make topics more specific, check `triage_score` / `triage_reasons` in the database. |

## Where the code is

- `src/research_pipeline/cli.py`: `run`, `collect`, `triage`, `summarize`, `digest`, `model`, `gold` and `eval` commands, `build_pipeline`, `health_line`, `runs` (the `profile` commands are in `profile.md`)
- `src/research_pipeline/seeds.py`: seed text lookup and cache
- `src/research_pipeline/summarize.py`: `run_summarize` (the per-paper loop)
- `src/research_pipeline/spill.py`: `run_reruns` (reading cut papers whole at night)
- `src/research_pipeline/requests_queue.py`: `drain_requests` (the request list; `rpipe requests add|run|list` are in `cli.py`)
- `src/research_pipeline/llm.py`, `fulltext.py`, `prompts.py`: model client, full-text fetching, prompts (see `llm.md`, `fulltext.md`, `summaries.md`)
- `src/research_pipeline/gold.py`, `evaluate.py`: `rpipe gold` and `rpipe eval` (see `evaluation.md`)
- `src/research_pipeline/run_lock.py`: `VaultLock` (take, let go, take again), `run_lock`, `RunLocked` (with `.holder`); in `cli.py`, `_pipeline_lock` turns a refusal into the message and exit 75, and `_prompt`/`_confirm` (through `_waiting_for_you`) let go of the lock while a question waits
- `src/research_pipeline/db.py`: `start_run`, `finish_run`, `list_runs`, `previous_run_unfinished`
- `tests/test_runs.py`, `tests/test_run_lock.py`, `tests/test_e2e.py`, `tests/test_summarize.py`, `tests/test_seeds.py`, `tests/test_requests_queue.py`, `tests/test_cli_requests.py`
