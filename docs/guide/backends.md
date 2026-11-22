# Model backends

## What it does

The pipeline needs a language model to write summaries. A *backend* is the thing that runs that model. There are tiers:

1. **Ollama** (default): a model on your own computer. Free, private, slower. See `llm.md`.
2. **Subscription CLI**: a command-line tool you already pay for and are logged in to. Today that is Claude Code (below).
3. **No model**: abstracts-only mode; the digest is built without summaries.

Every backend has two labels the rest of the code checks: `name` (e.g. `claude-code`) and `local` (true only if prompts never leave your machine). Text that must stay private, such as your own writing in `my_work/`, is only ever given to a backend whose `local` is true (`require_local` in `llm.py` enforces it).

## Choosing a backend

The backend is stored in `config.yaml` as `llm_backend` (default `ollama`; a vault without the key behaves exactly as before). Change it with a command, never by editing a file:

```
rpipe model set-backend ollama        # a model on this computer
rpipe model set-backend claude-code   # your Claude Code login
rpipe model set-backend none          # no model: abstracts-only digests
```

Without `--force` the command first checks the backend is ready: `ollama` must answer at the configured server address; `claude-code` must be found (`find_claude`) and logged in. If not, it refuses and says how to fix it, and the config is left untouched. `--force` switches anyway (for example while Ollama is stopped). Switching resets `llm_model` to `auto`, because model names differ per backend, but keeps the Ollama settings (`llm_url`, `llm_num_ctx`) so switching back is painless. For `claude-code` the command also records where it found `claude` (`llm_cli_path`); the nightly timer often has no `~/.local/bin` on its PATH, so the run tries that recorded path first, then PATH, then `~/.local/bin/claude`.

Worked example: you run `rpipe model set-backend claude-code` while logged out. It prints "Claude Code is not logged in: run `claude` once and log in, then try again", exits 1, and `rpipe model show` still says `backend: ollama`.

### Which `rpipe model` commands apply to which backend

| Command | ollama | claude-code | none |
|---|---|---|---|
| `set-backend NAME [--force]` | yes | yes | yes |
| `show` | yes (first line `backend: ollama`) | yes (model and `claude` path) | yes (just says so) |
| `set NAME` | any Ollama model, or `auto` | `sonnet`, `haiku`, `opus`, a full `claude-...` name, or `auto` | refused |
| `check` | server up and model installed | logged in, one tiny test call | refused |
| `fit`, `set-url`, `set-context` | yes | refused: "only applies to the ollama backend (current: claude-code). Nothing was changed." | refused (same wording) |

### The `none` backend (abstracts-only mode)

With `none` there is no summary model at all. `rpipe run` still collects, ranks and writes the digest; each paper shows its abstract instead of a summary, and the health line ends with `summaries off (abstracts-only mode)`. That is a choice, not a failure, so the errors field stays `none`. Commands that cannot work without a model (`rpipe summarize`, `rpipe eval run`, `rpipe profile suggest`, `rpipe model check`) print "No summary model is set up (abstracts-only mode). Run `rpipe model setup` to choose one." and exit 1; the guided `rpipe init` description step prints the same line and moves on.

### Privacy notice

Before `rpipe profile suggest` (or the init description step) sends your research description to a backend whose `local` is false, it prints "Your description will be sent to claude-code (model sonnet) to suggest topics." Your own writing in `my_work/` is never sent to such a backend at all.

### If the backend cannot start during a night

A backend that is missing, was uninstalled, or whose `claude` binary has moved does not stop the run. The health line records `summaries skipped, model unavailable: Claude Code not found (looked at the recorded path, PATH and ~/.local/bin); run `rpipe model setup``, and the digest is still written. The same goes for an unknown or broken plug-in backend (`backend NAME: not installed; run `rpipe model setup``). `ClaudeCodeClient.check()` likewise names `rpipe model setup` when the binary is missing or moved, and says "not logged in" only when the login is the problem.

## Claude Code backend

### How a call works

For each summary the pipeline runs Claude Code in headless mode (`claude -p`: print one answer and exit, no interactive screen):

1. Build the command line:
   ```
   claude -p --output-format json --json-schema <schema> --system-prompt <instructions>
          --tools "" --setting-sources "" --strict-mcp-config --no-session-persistence
          --permission-mode dontAsk --model sonnet
   ```
2. Create a fresh empty temporary folder and run the command inside it.
3. Send the (possibly huge) paper text on **stdin**, not in the command line, because command lines have a length limit.
4. Read the JSON that comes back. The answer is in `structured_output`; it is checked against the same Pydantic class used for Ollama. If it does not fit, the call is repeated once with a correction note; a second failure is an error.
5. Delete the temporary folder.

Worked example: a failed night where the login expired. `claude` prints `{"is_error": true, "result": "Not logged in"}`. The adapter matches "logged", raises an error saying "run `claude` once and log in", the run records it in the health line and still writes the digest.

### Why each flag

| Flag | Reason |
|---|---|
| `-p` | headless: no terminal needed, so it works from a timer |
| `--output-format json` | one machine-readable result with `is_error` |
| `--json-schema` | makes Claude return the exact JSON shape we validate |
| `--system-prompt` | replaces Claude Code's default coding prompt with ours |
| `--tools ""` | no tools at all: it cannot read files, run commands or browse |
| `--setting-sources ""` | ignores your user/project settings, hooks and CLAUDE.md files |
| `--strict-mcp-config` | loads no MCP (plug-in tool) servers |
| `--no-session-persistence` | does not save the conversation to disk |
| `--permission-mode dontAsk` | never stops to ask a question nobody can answer |
| `--model` | `sonnet` by default; `haiku`, `opus` or a full `claude-...` name allowed |

We never use `--bare`: it would skip the login and fall back to API-key billing.

These flags exist because paper text is untrusted. A paper could contain a *prompt injection*; with tools off, an injected instruction has nothing to act on.

### API key stripping

If `ANTHROPIC_API_KEY` is set in your shell, Claude Code would silently bill that API account instead of using your subscription. The adapter removes it from the child process's environment (your own shell is untouched). rpipe never reads or stores any credential; the login belongs to Claude Code.

### Usage limits

If the reply is an error (`is_error`, or a non-zero exit) mentioning a "limit" or "credit", the adapter raises `LlmLimitReached`. A run stops calling the model on it without counting a failure; remaining papers go in abstracts-only. Other failures: not logged in, a Claude Code too old to know a flag ("update Claude Code"), a missing binary (the error names `rpipe model setup`; the nightly timer often lacks `~/.local/bin` on its PATH), and a timeout after 600 s.

Anything printed before the JSON (an update notice) is ignored: the adapter takes the whole output if it parses, otherwise the last line that does.

### Risk: billing policy

Using `claude -p` on a Pro/Max plan is within Anthropic's consumer terms when you run your own login. In June 2026 Anthropic announced, then paused, moving `claude -p` and Agent SDK usage off subscription limits onto separate credits. It may return; if so the adapter reports it as a limit error and stops cleanly. Details: `docs/superpowers/specs/2026-11-21-cli-backends-research.md`.

## Why it's built this way

- Subprocess, not an SDK: the CLI already handles login, so rpipe never touches tokens.
- A `Runner` function is injected so tests use a fake and never start a real `claude`.
- Rejected: `--bare` (API-key billing), prompt on the command line (length limit), reusing the working directory (Claude could see project files).

## How to run / check it

`ClaudeCodeClient(...).check()` confirms login (`claude auth status`, exit 0) and does one tiny call. Check it from the command line with `rpipe model check`. Tests: `uv run pytest tests/test_claude_code.py tests/test_cli_model.py tests/test_registry.py -q` (offline).

## Where the code is

`src/research_pipeline/backends/claude_code.py` (`ClaudeCodeClient`, `find_claude`, `logged_in`, `resolve_model`); shared errors, `NO_MODEL` and `require_local` in `src/research_pipeline/llm.py`; backend names and `build_backend` in `src/research_pipeline/registry.py`; the `rpipe model` commands in `src/research_pipeline/cli.py` and their config edits (`set_llm_backend`, backend-aware `set_llm_model`) in `src/research_pipeline/profile_edit.py`.
