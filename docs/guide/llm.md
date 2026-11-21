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

Failures become `LlmError`: server not running (hint: `ollama serve`), model not installed (HTTP 404, hint: `ollama pull <model>`), timeout, other HTTP errors, and invalid output after the retry.

## Why it's built this way

- **Context window: `auto`, or a number you pin (`llm_num_ctx`).** The context window is how much text (in tokens, roughly word pieces) the model can read at once, prompt and answer together. Ollama's default is much smaller and silently cuts off the rest, which would make a summary of a long paper quietly miss its second half, so we always send an explicit window (32768 fits most papers; `auto` finds the largest your card holds). A pinned number must be at least 8192: the prompt budget is `(num_ctx - 4000) * 3` characters (see `summaries.md`), and at a smaller window almost nothing is left for the paper, so smaller values are rejected.
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
4. **Search.** A model must first fit at 16384 tokens, otherwise it is skipped and the next candidate is tried. A model that passes gets a binary search between 8192 and `min(trained context, 131072)` in steps of 1024: test the middle, go up if it fits, down if not. About 6 to 8 probes instead of a hundred.
5. **Pick and cache.** The first acceptable model wins, with its largest fitting context. The result is saved in `<vault>/cache/llm_fit.json`, keyed by the Ollama address, the settings and every installed model's name and digest. Install, remove or re-pull a model and the key changes, so the next run probes again. If nothing is acceptable you get an error listing every model tried and why, and pointing at `rpipe model set`.

Worked example with models installed on the owner's machine (the numbers are illustrative): `q3.8-27b-34k` (27.3B) is tried first and does not fit at 16384 tokens next to the other programs using the card, so it is skipped. `q2.5c-14b-32k` (14.8B) fits at 16384, so the search runs up to its trained limit of 32768, finds that 32768 also fits, and the pipeline uses that model with a 32768 context.

If you pinned only one setting, the other is still searched: a pinned model with `auto` context is probed alone (any fitting context of 8192 or more is accepted); an `auto` model with a pinned context picks the largest model that fits at exactly that context.

### Why it's built this way

- **"Fits fully in GPU memory" is the test.** When a model does not fit, Ollama silently keeps part of it on the CPU, and generation becomes many times slower. A model that fits is the useful boundary, so that is what we measure, the same way the owner's `o` script does.
- **The context is sent with each request, not baked into a model.** The `o` script can rebuild a model with a new default context and delete the old one. That edits and removes files that belong to the user, so this code never does it; it only sends `num_ctx` with each request (as `llm.py` already does).
- **Best model = largest that fits.** Ollama has no quality ranking, so parameter count is a stand-in: bigger models are usually better, but not always (a newer 14B can beat an older 27B). The real comparison is the gold-set check in `evaluation.md`; if it disagrees with the automatic choice, pin your model.
- **Rejected: a fixed list of preferred models.** It would go stale and would not know your hardware.

### What probing costs

Probing loads and unloads real models, which takes seconds to a minute each. The first run (and the first run after you install or change a model) can take a few minutes. After that the cached answer is used and no probe happens. Another program using the GPU (a game, another model) makes models look smaller than they are; the result is cached until the installed models change, so re-probe with `rpipe model fit` when the card is free.

One broken model never blocks the rest: if Ollama cannot describe one installed model (`/api/show` errors or returns nonsense) that model is skipped with a note and the others are still considered. With a pinned context, models whose trained context is shorter than the pin are skipped without being loaded.

### How to pin instead

`rpipe model set NAME` and `rpipe model set-context N` choose the model and context yourself; an explicit value always wins and is never overridden. Pin one and the other is still `auto`: a pinned model has only its context searched; a pinned context picks the largest model that fits at that context. Pin both and nothing is probed. `rpipe model set auto` and `rpipe model set-context auto` hand the choice back. Invalid values (for example `rpipe model set-context big` or `100`) are refused and the file is left unchanged.

### `rpipe model fit`

`rpipe model fit` re-probes right now, ignoring the cache, prints each model it tries, then `Using model X with a context of N tokens`, and saves the result in `<vault>/cache/llm_fit.json`. It exits 1 with the reason (for example an `ollama pull` hint when nothing usable is installed). Use it after installing a model, or after freeing GPU memory. `rpipe model show` prints the configured values and, when either is `auto`, the last cached choice (or `not probed yet; runs on first use, or rpipe model fit`).

### Troubleshooting

- **"no installed model fits fully in GPU memory".** The error lists each model tried and why. Close other programs using the GPU and run `rpipe model fit`, install a smaller model (`ollama pull qwen3:8b`), or pin a model and context with `rpipe model set` and `rpipe model set-context`.
- **"no usable model is installed".** Only embedding models (or none) are installed. Run `ollama pull NAME`, e.g. `ollama pull qwen3:14b`.
- **The first run is slow.** That is the probe (see "What probing costs"); later runs use the cache.
- **I want to pin a model.** `rpipe model set NAME`; undo with `rpipe model set auto`.

### Where the code is

- `src/research_pipeline/ollama_fit.py`: `list_models`, `fits`, `max_context`, `choose`, `resolve`, `last_choice`.
- `tests/test_ollama_fit.py`: a small fake Ollama with a deterministic memory boundary (no real Ollama needed); `tests/test_e2e.py` ("auto model and context") runs `rpipe run` and `rpipe model fit` against it.

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

`rpipe model check` (which resolves `auto` first) compares names with the tag spelled out, so `llama3.2` matches the `llama3.2:latest` that Ollama lists. It uses a 10-second timeout (generating has a much longer one), so a hung server fails fast. It exits 1 with a fix hint if the server is down or the model is missing. Tests use a fake client, so `uv run pytest -q` needs no Ollama.

## Where the code is

- `src/research_pipeline/llm.py`: `LLM` protocol, `OllamaClient`, `LlmError`.
- `src/research_pipeline/models.py`: `Config.llm_model`, `llm_url`, `llm_num_ctx`.
- `src/research_pipeline/profile_edit.py`: `set_llm_model`, `set_llm_num_ctx`.
- `src/research_pipeline/cli.py`: the `model` commands, `build_llm`.
- `tests/test_llm.py`, and `FakeLLM` in `tests/conftest.py`.
