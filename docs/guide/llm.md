# The local model (Ollama)

## What it does

From Phase 2 the pipeline asks a language model to read papers and write summaries. The model runs on your own computer, served by **Ollama** (a program that downloads open models and answers requests on `http://localhost:11434`). `src/research_pipeline/llm.py` is the small client that talks to it. Its one job is `generate(system, user, schema)`: send instructions and text, get back an object that has already been checked against a schema. If anything goes wrong it raises `LlmError` with a hint on how to fix it.

Ollama is one of three backends (`ollama`, `claude-code`, `none`); how to choose between them, and which commands apply to which, is in `backends.md`. The rest of this page is about the `ollama` backend. You manage it with six commands (plus `rpipe model set-backend` to switch backend, and `rpipe model set-effort low|medium|high`, which only matters for the `claude-code` backend: see `backends.md`): `rpipe model show`, `rpipe model set NAME`, `rpipe model set-url URL`, `rpipe model set-context N`, `rpipe model check` and `rpipe model fit`. `rpipe model set-mode auto|whole|split` (every backend) chooses whether papers are read in one go or in parts; `auto` reads in parts when the model does not sit fully in GPU memory or the context is small (see "Choosing the mode" in `summaries.md`). `rpipe model set-diagrams auto|on|off` (every backend) chooses whether each paper also gets a diagram; `auto` draws one unless the model runs on the CPU only (see "The diagrams setting" below). By default the model and the context window are both `auto`: the pipeline picks them for you (see "Choosing the model and context automatically" below). You never have to edit a file to change any of this. (The owner's `o` script is an optional convenience for managing models by hand; the pipeline does not need it and never calls it.)

## How it works

One call, step by step:

1. The client builds a request to `POST {llm_url}/api/chat` with the system text, the user text and these settings: `stream: false` (one complete answer, not a trickle), `think: false` (no hidden reasoning), `options.num_ctx` (the context window), `options.temperature: 0`, `options.num_predict: 4096` (an output limit, below), and `format` (the JSON schema of the Pydantic class you want back).
2. Ollama answers with `message.content`, a JSON string.
3. The client parses it with Pydantic. If it is not valid, it asks **once more**, appending the validation error to the user text so the model can correct itself. A second failure raises `LlmError`.

Worked example: asking for `class Answer(BaseModel): text: str` sends `format = {"properties": {"text": {...}}, "required": ["text"], ...}`. The model can only produce JSON of that shape, and `Answer(text="...")` comes back.

Every client is a **backend** with two class attributes: `name` and `local`. This client is "the `ollama` backend" (`name = "ollama"`, `local = True`). `local` is True only when prompts never leave your machine; `require_local(llm, what)` raises `PrivacyError` unless it is, and a backend with no `local` attribute counts as not local (safe by default). Nothing calls `require_local` yet: it is the guard that future code handling shelf text must use (see `shelf.md`).

A **usage limit** (a subscription plan saying "you have used your allowance until later") is reported by a backend as `LlmLimitReached`, a subclass of `LlmError`. The summarize stage treats it differently from a timeout: it stops calling the model, records no failure and burns no retry, so the remaining papers wait for the next run (see `summaries.md`). The Ollama client never raises it.

Failures become `LlmError`: server not running (hint: `ollama serve`), model not installed (HTTP 404, hint: `ollama pull <model>`), timeout (the subclass `LlmTimeout`, when a call takes longer than its time limit, below; after the first one a `rpipe run` stops calling the model for the rest of that run, see `running.md`), other HTTP errors, and invalid output after the retry.

## Why it's built this way

- **Context window: `auto`, or a number you pin (`llm_num_ctx`).** The context window is how much text (in tokens, roughly word pieces) the model can read at once, prompt and answer together. Ollama's default is much smaller and silently cuts off the rest, which would make a summary of a long paper quietly miss its second half, so we always send an explicit window (a pinned 32768 fits most papers; `auto` finds the largest your card holds). A pinned number must be at least 8192: the prompt budget is `(num_ctx - 4000) * 3` characters (see `summaries.md`), and at a smaller window almost nothing is left for the paper, so smaller values are rejected.
- **JSON schema instead of parsing free text.** Asking for "a summary" in prose and then scraping it with patterns breaks whenever the model rephrases. With a schema the server constrains the output to the shape we validate, so code can rely on fields existing. Rejected: free-text parsing.
- **An output limit (`num_predict`, 4096 tokens).** A model that never stops writing would hold up the whole night's run. A summary or a critique is about 2,000 tokens at most, so the limit is a wide margin that only a runaway answer reaches. If it does, the JSON is cut off, the client sees invalid output twice and raises `LlmError` (that paper is retried on a later run) instead of hanging. It is a constant (`NUM_PREDICT` in `llm.py`), not a setting, because no user has a reason to change it.
- **Thinking off and temperature 0.** Qwen3 models can write a long hidden "thinking" section first; it costs time and can leak into the answer. Temperature 0 makes the model pick its most likely word each time, so the same paper gives the same summary, which matters when we measure accuracy.
- **Local, not a cloud API.** Papers and your profile stay on your machine, there is no per-call cost and no key to manage. The price is needing a decent computer. Rejected: a cloud API.
- **One retry, then fail.** A second try fixes most formatting slips; a third rarely does and wastes minutes.

## The time limit for one call

**What it does.** Each call to Ollama may take a limited time; after that the client gives up and raises `LlmTimeout`. The limit is 600 seconds (`CALL_TIMEOUT`), or longer when `rpipe model bench` has measured this machine as slow for this model. Without the longer limit, a slow computer (for example one with no graphics card) could need more than 600 s for one long paper, and that first timeout stops the model for the rest of the night.

**How it works.**
1. When the nightly run (or any command that summarizes) sets up the Ollama client, it resolves the model and context as usual, then looks in `VAULT/cache/bench.json` for a speed measurement of that same model. Names are compared with the tag spelled out, so a measurement saved as `llama3.2` counts for `llama3.2:latest`.
2. No measurement for that model: the limit is 600 s, as before.
3. With one, `call_timeout(num_ctx, bench)` works out the worst case for one call: reading a prompt that fills the whole context window at the measured reading speed, plus writing the most the model is allowed to write (`num_predict`, 4,096 tokens) at the measured writing speed. It adds half again as a safety margin (times 1.5). The limit is that number or 600 s, whichever is larger, so a fast machine never gets less time than before.

Worked example: a CPU-only machine measured at 80 tokens/s reading and 18 tokens/s writing, with a 32,768-token context. Reading a full context takes 32,768 / 80 ≈ 410 s and writing the maximum takes 4,096 / 18 ≈ 228 s, so the limit is 1.5 × (410 + 228) ≈ 957 s. A fast GPU reading 3,000 tokens/s and writing 40 tokens/s gets 1.5 × (11 + 102) ≈ 170 s, less than 600, so it keeps 600 s.

**Why it's built this way.**
- **Measured, not guessed.** The bench already records reading and writing speed per model; using it means the limit grows only on a machine that really is slow, and by how slow it is. Rejected: a bigger fixed limit for everyone, which would make a hung Ollama on a fast machine waste far more time before the run gives up.
- **The worst case, not the typical paper.** A full context and the full output limit bound what one call can legitimately need, so a slow but working call is never cut off. The 1.5 margin covers a busier machine than at bench time.
- **Never below 600 s.** The bench is one short measurement; it should only ever add time, never take away the margin a fast machine had.
- **A measurement of another model is ignored.** Speeds differ a lot between models, so the limit uses only the bench of the model actually in use.
- **Not a setting.** It follows from the measurement; run `rpipe model bench` again after changing hardware or model. The rerun stage for cut papers sets its own per-call limits from the time left before the morning deadline (see `running.md`).

**How to run / check it.** `rpipe model bench` measures the speeds; `rpipe model recommend` prints the longer limit when a typical paper takes more than 600 s to read. `uv run pytest tests/test_llm.py tests/test_registry.py -q`.

**Where the code is.** `call_timeout` and `CALL_TIMEOUT` in `src/research_pipeline/llm.py`; `make_ollama` in `src/research_pipeline/registry.py` looks up the measurement.

## Choosing the model and context automatically

### What it does

Instead of you naming a model and a context window, the pipeline can ask Ollama what is installed, pick the largest model that runs entirely on your graphics card, and find the largest context window (see the glossary) that still fits. It needs no input from you and never changes anything in Ollama. This is the code in `ollama_fit.py`. It is on by default: `llm_model` and `llm_num_ctx` both start as `auto`. `build_llm` in `cli.py` calls `ollama_fit.resolve` and builds the client from the answer; the resolved context is `client.num_ctx`, and the summarize and eval stages use that value. If nothing can be chosen (Ollama down, no model installed, nothing fits) `build_llm` raises `LlmError`, so `rpipe run` skips the summaries, still writes the digest and names the cause in the health line.

### How it works

1. **Discovery.** `GET /api/tags` lists the installed models and their digests (a fingerprint of the model file). For each one, `POST /api/show` gives the parameter count (its size, in "billions of parameters"), the longest context it was trained for and its capabilities. Only models that can `completion` and are not `embedding` models are candidates: an embedding model turns text into numbers and cannot write a summary.
2. **Order.** Candidates are sorted by parameter count, largest first.
3. **Probe.** For a candidate we load it at a chosen context with an empty prompt, then ask `GET /api/ps` how much of it sits in GPU memory (`size_vram`) compared with its total `size`. If `size_vram >= size` it fits fully. Whatever happens (success, error, a single-request timeout of at most 300 seconds) the model is then unloaded with `keep_alive: 0`. A failed or timed-out probe simply counts as "does not fit". If the unload itself fails, a warning goes to stderr (`! could not unload NAME after probing ...`, using the same `!` mark as the other warnings), once per probing session however many probes fail: the model then stays in GPU memory until Ollama drops it (about 5 minutes), which could make the next probes look smaller.
   **Overall time limit.** A whole probing session (discovery plus every probe) must finish within 15 minutes (`FIT_DEADLINE = 900` seconds in `ollama_fit.py`); `rpipe model show` waits at most 10 seconds (`SHOW_DEADLINE`). When the limit runs out the session stops with `probing Ollama took longer than N seconds and was stopped (is Ollama hung or the model very slow to load?) ...`, nothing is cached (a hung server says nothing about what fits) and `rpipe model fit` exits 1 with that line. `show` prints `last choice: unknown. Ollama did not answer in time: ...` instead. The limits are fixed constants, not settings: no user has a reason to change them, and a legitimate first probe takes a few minutes, so the limit only exists to bound a hung Ollama (which used to stall for about 30 minutes); a very slow machine can pin both settings to skip probing. The deadline is checked between requests, not during one, so a session can overshoot it by up to 30 seconds: the unload call after each probe and the "which models are loaded" check have their own 30 second timeouts that ignore the session cap.
4. **Search.** A model must first fit at 16384 tokens (8192 if you pinned the model), otherwise it is skipped and the next candidate is tried. A model that passes gets a binary search between that already-proven size (not probed a second time) and `min(trained context, 131072)` in steps of 1024: test the middle, go up if it fits, down if not. At most about 7 more probes after the first one (the range is at most 112 steps), instead of a hundred.
5. **Safety margin.** The measured maximum is not used as is: the pipeline subtracts 1024 tokens (one step) and never goes below 8192. The margin is kept even for a model that fits at its full trained context: it guards against memory fluctuation, not just overflow. The nightly run can have a little less memory free than the probe saw (the CUDA driver and other programs fluctuate), and a context that only just fits would then spill onto the CPU. The margin is applied only to a context that was measured; a context you pinned yourself is used exactly.
6. **Pick and cache.** The first acceptable model wins, with its largest fitting context less the margin. The result is saved in `<vault>/cache/llm_fit.json`, keyed by the Ollama address, the settings, every installed model's name and digest, and the machine: the Ollama version, the server settings that change memory use (`OLLAMA_FLASH_ATTENTION`, `OLLAMA_KV_CACHE_TYPE`, `OLLAMA_NUM_PARALLEL`) and the list of GPUs with their memory (`scan.machine_key`). Install, remove or re-pull a model, upgrade Ollama, change a server setting with `rpipe model tune` or swap a GPU and the key changes, so the next run probes again. A cache file written before the machine was part of the key misses once: the first nightly run after this change probes again (about 1-3 minutes) and saves the new key. Reading the machine takes one version request and a few quick commands and never fails a run: anything unreadable is left out of the key. A cached choice is also re-checked when it is loaded: its context must still be no more than the model's trained context, and (when the context was chosen automatically) at least 8192, otherwise it is ignored with a note (`the saved context of N tokens for X is outside what it supports ... probing again`) and probed again, so a damaged or hand-edited cache file cannot make a run send an impossible window. The 8192 floor is not applied to a context you pinned yourself (`rpipe model set-context` refuses values below 8192, so only hand-editing `config.yaml` can produce one): that value is your choice, so it is cached like any other instead of being rejected and re-probed every night. If nothing is acceptable you get an error listing every model tried and why; that outcome is cached under the same key too, so a small machine does not spend minutes re-probing every night: the same error is raised at once until you install a model, change a setting, or run `rpipe model fit` (which always probes again). The error says to pin **both** settings, `rpipe model set NAME` and `rpipe model set-context N`: a pinned model with an `auto` context must still fit on the GPU at 8192 tokens, so pinning only the model does not help a machine where nothing fits.

Worked example with five installed models, given generic names here (the numbers are illustrative). Sorted by parameter count the candidates are `dense-27b` (27.3B), `coder-16b` (15.7B), `coder-14b` and `general-14b` (14.77B each), then `longctx-14b` (13.76B). `dense-27b` is tried first and does not fit at 16384 tokens next to the other programs using the card, so it is skipped. `coder-16b` fits at 16384, so the search runs up to its trained limit of 20480, finds that 20480 fits, subtracts the 1024 margin and the pipeline uses that model with a 19456 context. The other three are never probed.

This example also shows the limit of the heuristic: parameter count is only a stand-in for quality, and here an older coder model (`coder-16b`) wins on size over the newer 14B models that may summarise papers better and could hold a larger context. If you disagree with the pick, pin the model you prefer with `rpipe model set NAME` (the context stays `auto`), or compare them with the gold-set check in `evaluation.md`.

If you pinned only one setting, the other is still searched: a pinned model with `auto` context is probed alone (any fitting context of 8192 or more is accepted); an `auto` model with a pinned context picks the largest model that fits at exactly that context.

### Why it's built this way

- **"Fits fully in GPU memory" is the test.** When a model does not fit, Ollama silently keeps part of it on the CPU, and generation becomes many times slower. A model that fits is the useful boundary, so that is what we measure, the same way the owner's `o` script does.
- **The context is sent with each request, not baked into a model.** The `o` script can rebuild a model with a new default context and delete the old one. That edits and removes files that belong to the user, so this code never does it; it only sends `num_ctx` with each request (as `llm.py` already does).
- **Best model = largest that fits.** Ollama has no quality ranking, so parameter count is a stand-in: bigger models are usually better, but not always (a newer 14B can beat an older 27B). The real comparison is the gold-set check in `evaluation.md`; if it disagrees with the automatic choice, pin your model.
- **Rejected: a fixed list of preferred models.** It would go stale and would not know your hardware.
- **The normal pass never spills; only reruns do.** The automatic choice still demands a context that fits 100% in GPU memory, so every normal paper runs at full speed. The few papers whose core text is cut at that context are read again after the normal pass at a bigger context, with part of the model in RAM, within free RAM and the morning deadline. That rerun stage is the only place `rpipe` deliberately lets the model spill; see "Reading cut papers whole: reruns" in `running.md`.

### What probing costs

Probing loads and unloads real models, which takes seconds to a minute each. The first run (and the first run after you install or change a model) can take a few minutes; short progress lines go to stderr so it is not silent. `rpipe model fit` and `rpipe model check` open a stage around the probe, and each log line from the probe becomes a note under it (the CLI hands `ollama_fit` the stage's `note` method as its `log` callback, so a line is printed once, never twice):

```
model fit…
  trying qwen3:14b at 8192 tokens
  trying qwen3:14b at 16384 tokens
model fit done (1m48s)
```

(`rpipe run`, `summarize` and `eval run` print the same probe lines as plain indented lines before their own stage starts.) A model download (`rpipe model setup`) is a stage with a total of 100 (percent): on a terminal a bar moves with each whole percent; in a log or pipe you get `model pull…`, a line about every 10% (`  m:1 43%`), the final `100%` and `model pull done (...)`. On a terminal every probe note is also kept as a permanent line, so the history of `trying ...` / `skipping ...` lines stays on screen as it did before. After that the cached answer is used and no probe happens.

Two things can make a probe see less memory than the nightly run will have, and the pipeline guards against both:

- **The embedding model.** `rpipe run` ranks papers with a sentence-transformers model. It runs on the CPU (see "Why it's built this way" in `triage.md`), so it takes no GPU memory. When it ran on the GPU it held roughly 0.3 to 1 GB (the CUDA context plus a cache of freed blocks) for the rest of the run, and the summarizing model spilled about 8% to RAM. As a safety net `run` still finishes ranking, drops the embedder, runs Python's garbage collector and, only if PyTorch is already imported and a GPU is present, calls `torch.cuda.empty_cache()` (it never imports PyTorch just for this), and only then resolves the model. The 1024-token margin above covers what is left. `rpipe model fit` has no embedder to release.
- **Models you already have loaded.** Before probing, the pipeline reads `/api/ps`. A model that was already loaded is never unloaded by a probe (you may be using it). If any other model was loaded when the probe began, the probe prints `probed while X was loaded; re-run rpipe model fit when the GPU is idle` and the result is **not** cached (neither a choice nor a failure), because a busy card makes everything look smaller. The next run probes again, so run `rpipe model fit` when the GPU is idle to get a cached answer.

One broken model never blocks the rest: if Ollama cannot describe one installed model (`/api/show` errors or returns nonsense) that model is skipped with a note and the others are still considered. With a pinned context, models whose trained context is shorter than the pin are skipped without being loaded.

### How to pin instead

`rpipe model set NAME` and `rpipe model set-context N` choose the model and context yourself; an explicit value always wins and is never overridden. Pin one and the other is still `auto`: a pinned model has only its context searched; a pinned context picks the largest model that fits at that context. Pin both and nothing is probed (the context you pinned is used exactly, with no safety margin). `rpipe model set auto` (any capitalisation, stored as `auto`) and `rpipe model set-context auto` hand the choice back. Invalid values (for example `rpipe model set-context big` or `100`) are refused and the file is left unchanged.

### `rpipe model fit`

`rpipe model fit` re-probes right now, ignoring the cache (including a cached "nothing fits"), prints each model it tries on stderr, then `Using model X with a context of N tokens`, and saves the result in `<vault>/cache/llm_fit.json` (unless the card was busy, see above). It exits 1 with a one-line reason (for example an `ollama pull` hint when nothing usable is installed; an unreadable Ollama answer or an unwritable cache also give one line, never a traceback). If both settings are pinned there is nothing to probe: it says `pinned: X at N tokens` and runs the same check as `rpipe model check`, which verifies X is installed. Use it after installing a model, or after freeing GPU memory. `rpipe model show` prints the configured values and, when either is `auto`, the last cached choice, but only if that cache still matches the current settings and installed models; otherwise it says `not probed yet for these settings and installed models` and that it is probed on next use.

### Troubleshooting

- **"no installed model fits fully in GPU memory".** The error lists each model tried and why, and is remembered (so it is raised at once each night) until the installed models or settings change or you run `rpipe model fit`. Close other programs using the GPU and run `rpipe model fit`, install a smaller model (`ollama pull qwen3:8b`), or pin **both** a model and a context with `rpipe model set NAME` and `rpipe model set-context N`.
- **"no usable model is installed".** Only embedding models (or none) are installed. Run `ollama pull NAME`, e.g. `ollama pull qwen3:14b`.
- **The first run is slow.** That is the probe (see "What probing costs"); later runs use the cache.
- **I want to pin a model.** `rpipe model set NAME`; undo with `rpipe model set auto`.

### Where the code is

- `src/research_pipeline/ollama_fit.py`: `list_models`, `loaded_models`, `unload` (used by the rerun stage), `fits`, `max_context`, `choose`, `resolve`, `cached_fit`, and `ProbeSession` (the deadline and the once-only unload warning).
- `src/research_pipeline/cli.py`: `_release_gpu_memory` (called by `run` before the model is resolved), `_progress` (stderr probe lines outside a stage) and `_pull_progress` (turns download percentages into progress steps).
- `tests/test_ollama_fit.py`: a small fake Ollama with a deterministic memory boundary (no real Ollama needed); `tests/test_e2e.py` ("auto model and context") runs `rpipe run` and `rpipe model fit` against it.

## Second user: onboarding

Besides summaries, the model has one more job: `rpipe profile suggest` (and the first question of `rpipe init`) asks it to propose topics and seed-paper titles from a description of your research. It goes through the same `build_llm` resolution and `check()` as `run` and `summarize`, including `auto` model choice. If Ollama is down or the model is missing, onboarding prints `Local model unavailable; add topics by hand with `rpipe profile add-topic`` and changes nothing. Its prompt, `ONBOARDING_SYSTEM`, is separate from the summary prompts and not versioned by `PROMPT_VERSION`. Details: `profile.md`.

## How to run / check it

Install Ollama (https://ollama.com), then:

```
ollama pull qwen3:14b
rpipe model check       # "Ollama is running at ... and model qwen3:14b is installed."
rpipe model show        # model, server, context window (and the last auto choice)
rpipe model fit         # re-probe now: best model and largest context that fit
rpipe model set qwen3:8b   # pin a smaller model for less memory (rpipe model set auto undoes it)
rpipe model set-url http://gpu-box:11434   # a different Ollama server (must start with http:// or https://)
rpipe model set-context 16384   # pin the context in tokens, at least 8192, or `auto`; invalid values are refused and the file is left alone
rpipe model set-mode split      # read papers in parts (auto, the default, picks per machine; whole reads in one go)
rpipe model set-diagrams off    # no diagram per paper (auto, the default, skips it only on a CPU-only model; on always asks for one: the model may still answer no diagram, and abstract-only papers never get one)
```

**Check that prefix caching works.** The summary and the critique start with identical text (see `summaries.md`), so Ollama should read the paper once. After every reply the client keeps Ollama's own timing numbers in `client.last_stats` (`prompt_eval_count`: the prompt's length in tokens, which this Ollama counts in full even when part of it came from the cache; `prompt_eval_duration` and `eval_duration` in nanoseconds, where the cache shows up; `eval_count`: tokens written; `None` when Ollama sends none). To see it on one paper:

```
uv run python - <<'PY'
from research_pipeline.llm import OllamaClient
from research_pipeline.prompts import build_summary_prompt, build_critique_prompt
from research_pipeline.summary_models import Summary, Critique
from research_pipeline.models import Section
secs = [Section(title="1 Introduction", text="We study X. " * 2000)]  # use a real paper's sections for a real test
c = OllamaClient("http://localhost:11434", "YOUR_MODEL", 32768)
c.generate(*build_summary_prompt("T", secs, "full_text"), Summary)
print("summary read", c.last_stats["prompt_eval_duration"] / 1e9, "s")
c.generate(*build_critique_prompt("T", secs, "full_text"), Critique)
print("critique read", c.last_stats["prompt_eval_duration"] / 1e9, "s")
PY
```

Working caching shows the second time far smaller than the first (only the task text is read again). Measured 2026-11-22 on the owner machine (27B model, 76,000-character paper, about 24,800 prompt tokens): with separate system prompts both calls spent about 15.5 s reading the prompt; with the shared layout the summary still took 15.7 s and the critique 0.5 s, so the critique call went from 42 s to 16 s overall. If both times are about equal, the server is not reusing its reading (another model was loaded in between, Ollama was restarted, or the options differ).

`rpipe model check` (which resolves `auto` first, so with `auto` settings it may probe the GPU and be slow the first time) compares names with the tag spelled out, so `llama3.2` matches the `llama3.2:latest` that Ollama lists. It uses a 10-second timeout (generating has a much longer one), so a hung server fails fast. It exits 1 with a fix hint if the server is down or the model is missing. Tests use a fake client, so `uv run pytest -q` needs no Ollama.

## Where the code is

- `src/research_pipeline/llm.py`: `LLM` protocol (with `name` and `local`), `OllamaClient`, `LlmError`, `LlmTimeout`, `LlmLimitReached`, `PrivacyError`, `require_local`, `NO_MODEL`, `CALL_TIMEOUT`, `call_timeout` (the per-call time limit).
- `src/research_pipeline/registry.py`: `make_ollama` (builds the client with the measured time limit).
- `src/research_pipeline/models.py`: `Config.llm_model`, `llm_url`, `llm_num_ctx`.
- `src/research_pipeline/profile_edit.py`: `set_llm_model`, `set_llm_num_ctx`.
- `src/research_pipeline/cli.py`: the `model` commands, `build_llm`.
- `src/research_pipeline/onboarding.py`: `propose` (the onboarding model call).
- `tests/test_llm.py`, and `FakeLLM` in `tests/conftest.py`.

## The diagrams setting

**What it does.** Each paper can get a small diagram of its approach, which is one more model call per paper. `diagrams` in `config.yaml` says whether to make it: `on`, `off` or `auto` (the default). Set it with `rpipe model set-diagrams auto|on|off` (any capitals; anything else is refused with the three choices listed and the config left unchanged). `rpipe model show` prints `diagrams: auto (change with `rpipe model set-diagrams`)`. `off` also hides diagrams already drawn: the app's paper screen leaves out "How it works" while the setting is `off` (Phase 9.3). Nothing is deleted, so `on` or `auto` shows them again.

**How it works.** In the same `reading mode` stage that picks whole or split, `resolve_diagrams` (`parts.py`) turns the setting into yes or no and a one-line reason that is shown as a progress note:

1. `on` or `off`: used as set, on any backend. The note says `set with `rpipe model set-diagrams on``.
2. `auto` with a backend other than Ollama (Claude Code): on. Cloud models are fast enough for one more call.
3. `auto` with Ollama: the GPU share already read for the summarize mode is reused. Unknown share (the model was not loaded because the summarize mode is fixed, or the read failed): on. Share 0.0, the model on the CPU only: off, with the note `diagrams off: the model runs on the CPU only (GPU share 0.0)`. Anything else: on.

Worked example: a CPU-only laptop with the default settings. `rpipe run` loads the model, reads share 0.0, notes `split: the model runs on the CPU only (GPU share 0.0)` and `diagrams off: the model runs on the CPU only (GPU share 0.0)`, and papers get no diagram. `rpipe model set-diagrams on` forces them back, at the cost of one more slow call per paper.

**Why it is built this way.** A CPU reads and writes tokens many times slower than a GPU, so for a pipeline that already struggles to finish overnight on a CPU, an optional picture is the first thing to drop (see `hardware.md`). Rejected: always on (it can push a CPU-only night past the morning deadline) and always off (it wastes capable machines). The value is stored as a quoted string, because an unquoted `off` is a boolean to YAML; a hand-edited `diagrams: off` is read as `off` all the same.

Phase 9.3 (2026-11-26): the owner switched diagrams off after the first nightly run with them. A diagram drawn from the model's reading of the paper is a few labels in boxes that repeat "What they did"; it shows nothing new. Diagrams worth having would come from the paper itself (its own figures and tables), which is a separate future phase. Until then the setting is `off` on the owner's machine and stored diagrams stay hidden.

**How to check it.** `rpipe model show` prints the setting; the `reading mode` stage shows the reason during a run. `tests/test_parts.py` checks `resolve_diagrams` for each case, `tests/test_profile_edit.py` and `tests/test_cli_model.py` check the command and the `show` line.

**Where the code.** `src/research_pipeline/parts.py` (`resolve_diagrams`), `src/research_pipeline/cli.py` (`_read_plan`, `_summarize_plan`, `rpipe model set-diagrams`), `src/research_pipeline/profile_edit.py` (`set_diagrams`), `src/research_pipeline/models.py` (`Config.diagrams`).
