# The local model (Ollama)

## What it does

From Phase 2 the pipeline asks a language model to read papers and write summaries. The model runs on your own computer, served by **Ollama** (a program that downloads open models and answers requests on `http://localhost:11434`). `src/research_pipeline/llm.py` is the small client that talks to it. Its one job is `generate(system, user, schema)`: send instructions and text, get back an object that has already been checked against a schema. If anything goes wrong it raises `LlmError` with a hint on how to fix it.

You manage it with three commands: `rpipe model show`, `rpipe model set NAME`, `rpipe model check`.

## How it works

One call, step by step:

1. The client builds a request to `POST {llm_url}/api/chat` with the system text, the user text and these settings: `stream: false` (one complete answer, not a trickle), `think: false` (no hidden reasoning), `options.num_ctx` (the context window), `options.temperature: 0`, and `format` (the JSON schema of the Pydantic class you want back).
2. Ollama answers with `message.content`, a JSON string.
3. The client parses it with Pydantic. If it is not valid, it asks **once more**, appending the validation error to the user text so the model can correct itself. A second failure raises `LlmError`.

Worked example: asking for `class Answer(BaseModel): text: str` sends `format = {"properties": {"text": {...}}, "required": ["text"], ...}`. The model can only produce JSON of that shape, and `Answer(text="...")` comes back.

Failures become `LlmError`: server not running (hint: `ollama serve`), model not installed (HTTP 404, hint: `ollama pull <model>`), timeout, other HTTP errors, and invalid output after the retry.

## Why it's built this way

- **Context window of 32K (`llm_num_ctx` = 32768).** The context window is how much text (in tokens, roughly word pieces) the model can read at once, prompt and answer together. Ollama's default is much smaller and silently cuts off the rest, which would make a summary of a long paper quietly miss its second half. 32768 fits most papers. The setting must be at least 8192: the prompt budget is `(num_ctx - 4000) * 3` characters (see `summaries.md`), and at a smaller window almost nothing is left for the paper, so smaller values are rejected.
- **JSON schema instead of parsing free text.** Asking for "a summary" in prose and then scraping it with patterns breaks whenever the model rephrases. With a schema the server constrains the output to the shape we validate, so code can rely on fields existing. Rejected: free-text parsing.
- **Thinking off and temperature 0.** Qwen3 models can write a long hidden "thinking" section first; it costs time and can leak into the answer. Temperature 0 makes the model pick its most likely word each time, so the same paper gives the same summary, which matters when we measure accuracy.
- **Local, not a cloud API.** Papers and your profile stay on your machine, there is no per-call cost and no key to manage. The price is needing a decent computer. Rejected: a cloud API.
- **One retry, then fail.** A second try fixes most formatting slips; a third rarely does and wastes minutes.

## How to run / check it

Install Ollama (https://ollama.com), then:

```
ollama pull qwen3:14b
rpipe model check       # "Ollama is running at ... and model qwen3:14b is installed."
rpipe model show        # model, server, context window
rpipe model set qwen3:8b   # smaller model for less memory
```

`rpipe model check` exits 1 with a fix hint if the server is down or the model is missing. Tests use a fake client, so `uv run pytest -q` needs no Ollama.

## Where the code is

- `src/research_pipeline/llm.py`: `LLM` protocol, `OllamaClient`, `LlmError`.
- `src/research_pipeline/models.py`: `Config.llm_model`, `llm_url`, `llm_num_ctx`.
- `src/research_pipeline/profile_edit.py`: `set_llm_model`.
- `src/research_pipeline/cli.py`: the `model` commands, `build_llm`.
- `tests/test_llm.py`, and `FakeLLM` in `tests/conftest.py`.
