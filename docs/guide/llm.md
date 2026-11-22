# The local model (Ollama)

## What it does

From Phase 2 the pipeline asks a language model to read papers and write summaries. The model runs on your own computer, served by **Ollama** (a program that downloads open models and answers requests on `http://localhost:11434`). `src/research_pipeline/llm.py` is the small client that talks to it. Its one job is `generate(system, user, schema)`: send instructions and text, get back an object that has already been checked against a schema. If anything goes wrong it raises `LlmError` with a hint on how to fix it.

You manage it with six commands: `rpipe model show`, `rpipe model set NAME`, `rpipe model set-url URL`, `rpipe model set-context N`, `rpipe model check` and `rpipe model fit`. By default the model and the context window are both `auto`: the pipeline picks them for you (see "Choosing the model and context automatically" below). You never have to edit a file to change any of this. (The owner's `o` script is an optional convenience for managing models by hand; the pipeline does not need it and never calls it.)

## How it works

One call, step by step:

1. The client builds a request to `POST {llm_url}/api/chat` with the system text, the user text and these settings: `stream: false` (one complete answer, not a trickle), `think: false` (no hidden reasoning), `options.num_ctx` (the context window), `options.temperature: 0`, and `format` (the JSON schema of the Pydantic class you want back).
2. Ollama answers with `message.content`, a JSON string.
3. The client parses it with Pydantic. If it is not valid, it asks **once more**, appending the validation error to the user text so the model can correct itself. A second failure raises `LlmError`.

Worked example: asking for `class Answer(BaseModel): text: str` sends `format = {"properties": {"text": {...}}, "required": ["text"], ...}`. The model can only produce JSON of that shape, and `Answer(text="...")` comes back.

Failures become `LlmError`: server not running (hint: `ollama serve`), model not installed (HTTP 404, hint: `ollama pull <model>`), timeout (the subclass `LlmTimeout`; after the first one a `rpipe run` stops calling the model for the rest of that run, see `running.md`), other HTTP errors, and invalid output after the retry.

## Why it's built this way

- **Context window: `auto`, or a number you pin (`llm_num_ctx`).** The context window is how much text (in tokens, roughly word pieces) the model can read at once, prompt and answer together. Ollama's default is much smaller and silently cuts off the rest, which would make a summary of a long paper quietly miss its second half, so we always send an explicit window (a pinned 32768 fits most papers; `auto` finds the largest your card holds). A pinned number must be at least 8192: the prompt budget is `(num_ctx - 4000) * 3` characters (see `summaries.md`), and at a smaller window almost nothing is left for the paper, so smaller values are rejected.
- **JSON schema instead of parsing free text.** Asking for "a summary" in prose and then scraping it with patterns breaks whenever the model rephrases. With a schema the server constrains the output to the shape we validate, so code can rely on fields existing. Rejected: free-text parsing.
- **Thinking off and temperature 0.** Qwen3 models can write a long hidden "thinking" section first; it costs time and can leak into the answer. Temperature 0 makes the model pick its most likely word each time, so the same paper gives the same summary, which matters when we measure accuracy.
- **Local, not a cloud API.** Papers and your profile stay on your machine, there is no per-call cost and no key to manage. The price is needing a decent computer. Rejected: a cloud API.
- **One retry, then fail.** A second try fixes most formatting slips; a third rarely does and wastes minutes.

## Choosing the model and context automatically

### What it does

Instead of you naming a model and a context window, the pipeline can ask Ollama what is installed, pick the largest model that runs entirely on your graphics card, and find the largest context window (see the glossary) that still fits. It needs no input from you and never changes anything in Ollama. This is the code in `ollama_fit.py`. It is on by default: `llm_model` and `llm_num_ctx` both start as `auto`. `build_llm` in `cli.py` calls `ollama_fit.resolve` and builds the client from the answer; the resolved context is `client.num_ctx`, and the summarize and eval stages use that value. If nothing can be chosen (Ollama down, no model installed, nothing fits) `build_llm` raises `LlmError`, so `rpipe run` skips the summaries, still writes the digest and names the cause in the health line.

### How it works

1. **Discovery.** `GET /api/tags` lists the installed models and their digests (a fingerprint of the model file). For each one, `POST /api/show` gives the parameter count (its size, in "billions of parameters"), the longest context it was trained for and its capabilities. Only models that can `completion` and are not `embedding` models are candidates: an embedding model turns text into numbers and cannot write a summary.
2. **Order.** Candidates are sorted by parameter count, largest first.
3. **Probe.** For a candidate we load it at a chosen context with an empty prompt, then ask `GET /api/ps` how much of it sits in GPU memory (`size_vram`) compared with its total `size`. If `size_vram >= size` it fits fully. Whatever happens (success, error, timeout of at most 300 seconds) the model is then unloaded with `keep_alive: 0`. A failed or timed-out probe simply counts as "does not fit".
4. **Search.** A model must first fit at 16384 tokens (8192 if you pinned the model), otherwise it is skipped and the next candidate is tried. A model that passes gets a binary search between that already-proven size (not probed a second time) and `min(trained context, 131072)` in steps of 1024: test the middle, go up if it fits, down if not. At most about 7 more probes after the first one (the range is at most 112 steps), instead of a hundred.
5. **Safety margin.** The measured maximum is not used as is: the pipeline subtracts 1024 tokens (one step) and never goes below 8192. The nightly run can have a little less memory free than the probe saw (the CUDA driver and other programs fluctuate), and a context that only just fits would then spill onto the CPU. The margin is applied only to a context that was measured; a context you pinned yourself is used exactly.
6. **Pick and cache.** The first acceptable model wins, with its largest fitting context less the margin. The result is saved in `<vault>/cache/llm_fit.json`, keyed by the Ollama address, the settings and every installed model's name and digest. Install, remove or re-pull a model and the key changes, so the next run probes again. If nothing is acceptable you get an error listing every model tried and why; that outcome is cached under the same key too, so a small machine does not spend minutes re-probing every night: the same error is raised at once until you install a model, change a setting, or run `rpipe model fit` (which always probes again). The error says to pin **both** settings, `rpipe model set NAME` and `rpipe model set-context N`: a pinned model with an `auto` context must still fit on the GPU at 8192 tokens, so pinning only the model does not help a machine where nothing fits.

Worked example with models installed on the owner's machine (the numbers are illustrative). Sorted by parameter count the candidates are `q3.8-27b-34k` (27.3B), `ds2-16b-20k` (15.7B), `q2.5c-14b-32k` and `q3-14b-22k` (14.77B each), then `q3f-14b-143k` (13.76B). `q3.8-27b-34k` is tried first and does not fit at 16384 tokens next to the other programs using the card, so it is skipped. `ds2-16b-20k` fits at 16384, so the search runs up to its trained limit of 20480, finds that 20480 fits, subtracts the 1024 margin and the pipeline uses that model with a 19456 context. The other three are never probed.

This example also shows the limit of the heuristic: parameter count is only a stand-in for quality, and here an older coder model (`ds2-16b-20k`) wins on size over the newer 14B models that may summarise papers better and could hold a larger context. If you disagree with the pick, pin the model you prefer with `rpipe model set NAME` (the context stays `auto`), or compare them with the gold-set check in `evaluation.md`.

If you pinned only one setting, the other is still searched: a pinned model with `auto` context is probed alone (any fitting context of 8192 or more is accepted); an `auto` model with a pinned context picks the largest model that fits at exactly that context.

### Why it's built this way

- **"Fits fully in GPU memory" is the test.** When a model does not fit, Ollama silently keeps part of it on the CPU, and generation becomes many times slower. A model that fits is the useful boundary, so that is what we measure, the same way the owner's `o` script does.
- **The context is sent with each request, not baked into a model.** The `o` script can rebuild a model with a new default context and delete the old one. That edits and removes files that belong to the user, so this code never does it; it only sends `num_ctx` with each request (as `llm.py` already does).
- **Best model = largest that fits.** Ollama has no quality ranking, so parameter count is a stand-in: bigger models are usually better, but not always (a newer 14B can beat an older 27B). The real comparison is the gold-set check in `evaluation.md`; if it disagrees with the automatic choice, pin your model.
- **Rejected: a fixed list of preferred models.** It would go stale and would not know your hardware.

### What probing costs

Probing loads and unloads real models, which takes seconds to a minute each. The first run (and the first run after you install or change a model) can take a few minutes; short progress lines (`probing NAME at N tokens ...`) go to stderr so it is not silent (`rpipe model check`, `run`, `summarize`, `eval run` and `model fit` all show them). After that the cached answer is used and no probe happens.

Two things can make a probe see less memory than the nightly run will have, and the pipeline guards against both:

- **The embedding model.** `rpipe run` ranks papers with a sentence-transformers model that holds GPU memory (roughly 0.3 to 1 GB: the CUDA context plus a cache of freed blocks) while it is loaded. `run` finishes ranking, drops the embedder, runs Python's garbage collector and, only if PyTorch is already imported and a GPU is present, calls `torch.cuda.empty_cache()` (it never imports PyTorch just for this), and only then resolves the model. The 1024-token margin above covers what is left. `rpipe model fit` has no embedder to release.
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

- `src/research_pipeline/ollama_fit.py`: `list_models`, `loaded_models`, `fits`, `max_context`, `choose`, `resolve`, `cached_fit`.
- `src/research_pipeline/cli.py`: `_release_gpu_memory` (called by `run` before the model is resolved) and `_progress` (stderr progress lines).
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
```

`rpipe model check` (which resolves `auto` first, so with `auto` settings it may probe the GPU and be slow the first time) compares names with the tag spelled out, so `llama3.2` matches the `llama3.2:latest` that Ollama lists. It uses a 10-second timeout (generating has a much longer one), so a hung server fails fast. It exits 1 with a fix hint if the server is down or the model is missing. Tests use a fake client, so `uv run pytest -q` needs no Ollama.

## Where the code is

- `src/research_pipeline/llm.py`: `LLM` protocol, `OllamaClient`, `LlmError`.
- `src/research_pipeline/models.py`: `Config.llm_model`, `llm_url`, `llm_num_ctx`.
- `src/research_pipeline/profile_edit.py`: `set_llm_model`, `set_llm_num_ctx`.
- `src/research_pipeline/cli.py`: the `model` commands, `build_llm`.
- `src/research_pipeline/onboarding.py`: `propose` (the onboarding model call).
- `tests/test_llm.py`, and `FakeLLM` in `tests/conftest.py`.
