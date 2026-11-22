# Phase 5 — Shareable: design

Date: 2026-11-21. Status: draft for owner approval.

## Intent

Anyone, on any machine, can install `rpipe`, answer a guided walkthrough and get a nightly digest that fits their interests **and their hardware**, without reading code or editing YAML. The AI club is one group of such users, not the design target.

The owner's setup (a GPU with Ollama and local models) is the ideal and stays the default path. Phase 5 widens the doorway around it:

1. **A hardware-aware walkthrough** that detects what the machine has and recommends a backend tier.
2. **Pluggable backends**: the model behind summaries can be local Ollama or a subscription AI CLI the user is already logged into (Claude Code first).
3. **Packaging**: MIT licence, a README quickstart that works for a stranger, CI running the offline tests.

## Goals

- A new user runs `rpipe init` and finishes with a working setup: interests, a backend that works on their machine, and a passing check, or an exact list of what to fix.
- Three tiers, recommended in this order:
  1. **Local GPU + Ollama**: free and private (exists today).
  2. **Subscription CLI** (Claude Code in Phase 5): good summaries on weak or no GPU, using a subscription the user already pays for. No API keys.
  3. **Abstracts only**: no summaries. Ranking, voting, shelf and digest all still work (Phase 1 behaviour), reported as a deliberate mode, not an error.
- The backend choice can be redone later without redoing interests (`rpipe model setup`).
- Sources and backends are chosen by name, and third parties can add their own through Python entry points without forking.
- Safety for subscription CLIs lives in code, not config. Paper text is untrusted, and the CLI is an agent.

## Non-goals (Phase 5)

- **API-key backends** (Anthropic/OpenAI/Gemini APIs, OpenAI-compatible URLs). Way down the road, if ever (owner).
- **The optimised CPU pipeline** for machines with no GPU and no subscription: a redesign of summarising for small models. It is its own project with its own brainstorming session (owner). Until then such users get tier 3.
- **Codex and Antigravity (`agy`) adapters.** The interface must let them drop in. Codex waits until someone verifies it on a real install. `agy` gets its own investigation side project (see below).
- **Docker / Docker Compose.** Dropped: anyone who can run Ollama can run `uv tool install`, and GPU passthrough in Docker is its own setup problem (my call).
- **Installing Ollama.** `rpipe` never installs system software. It prints the official install command and re-checks afterwards (agreed).
- **Schedulers other than systemd.** `rpipe schedule` stays Linux-only. On macOS/Windows it says so plainly and points to the README (my call; launchd/Task Scheduler can come later).
- Plug-in registries for embedders, fetchers, rankers and outputs. Only sources and backends get one now (my call: only one implementation of each of the others exists, YAGNI).

## Anti-goals

- A subscription CLI ever running with tools enabled on paper text.
- `rpipe` reading, copying or storing anyone's OAuth tokens or credentials. It only runs the unmodified CLI binary under the user's own login. Anthropic's and Google's terms both forbid credential handling by third parties.
- Shelf (`my_work/`) content, or anything derived from it, reaching a non-local backend.
- A walkthrough that ends "done" when the nightly run would fail.

## Constraints

- Project rules (`CLAUDE.md`): docs in `docs/guide/` in the same commit as code; every user setting has an `rpipe` command; tests offline (`uv run pytest -q`); nothing personal in the repo.
- Existing behaviour for current users must not change: a vault with no `llm_backend` key behaves exactly as today (Ollama).
- Nightly runs have no terminal, stdout is a pipe, and the systemd user unit's PATH may lack `~/.local/bin`.
- Linux caps one argv string at 128 KiB, so prompts (up to ~30K tokens) must go on stdin.

## Approaches considered

- **A. One adapter per tool, in code (chosen by owner).** Each backend is a small class: generate, check, detect, recognise "usage limit hit". Each hard-wires its safety flags. Config stores only the backend name and model.
- **B. One generic "command template" backend.** Config holds a command line plus per-tool presets. Rejected: the safety flags would live in an editable file (one bad edit drops `--tools ""`). Each tool returns results differently (JSON field, file, envelope), so templates become a mini-language. It also fights the no-hand-editing rule.
- **C. Ollama-only Phase 5.** Rejected: a low-GPU user gets nothing new.

## Research basis: subscription CLIs (2026-11-21)

Full report: `docs/superpowers/specs/2026-11-21-cli-backends-research.md` (committed with this design).

| Tool | Status |
|---|---|
| Claude Code `claude -p` | **Verified on the owner's machine**: plan login, no TTY, all tools off, schema-valid JSON in 1.8 s. `--json-schema` result in `structured_output`; prompt on stdin; `--system-prompt` replaces the default; `claude auth status` exits 0/1; JSON `is_error` + limit text. Risk: Anthropic announced, then paused (June 2026), moving `-p` usage off plan limits. |
| Codex `codex exec` | Unverified (not installed). Has `--output-schema` and stdin. **Cannot switch tools off** (read-only sandbox only). Limit signalling undocumented. Deferred. |
| Antigravity `agy -p` | Has a schema flag; no tools-off switch; long prompts need `--input-format stream-json`. No-TTY hang reports up to v1.4. Consumer-plan FAQ says wrapping it in applications "is not supported". Deferred to side project. |
| Gemini CLI | Out: consumer access moved to `agy` in June 2026. |
| Copilot / Cursor / Kiro | No schema flag; API-key leaning. Deferred. |

## The design

### 1. Backends

A backend implements the existing `LLM` protocol (`model_name`, `num_ctx`, `generate(system, user, schema)`), plus:

- `local: bool`: does text stay on this machine?
- `check() -> str`: a status line, or `LlmError` with the fix.
- `name`: the registry name stored in config.

Detection (is it installed, is the user logged in) is a function per backend for the walkthrough, not a method a running client needs.

New exception `LlmLimitReached(LlmError)`: a subscription usage limit was hit. `run_summarize` treats it like `LlmTimeout` today: it stops calling the model for the rest of the run, and the health line says so (`summaries stopped: usage limit reached`). Papers stay in their prior status for the next night. No retries are burned.

Shipped backends:

- **`ollama`**: today's `OllamaClient`, `local = True`. `auto` model/context fitting (`ollama_fit.py`) stays Ollama-only.
- **`claude-code`**: `local = False`. It runs the `claude` binary as a subprocess:
  - The prompt goes on stdin. The schema goes in `--json-schema`; the system prompt in `--system-prompt`.
  - Output: `--output-format json`, parsed for `structured_output` and `is_error`, then validated with the Pydantic schema. One retry on invalid output, matching `OllamaClient`.
  - **Safety flags hard-wired:** all tools off (`--tools ""`); no user settings, hooks, MCP servers or CLAUDE.md loaded (`--setting-sources ""` or the current equivalent); no session persistence; a non-interactive permission mode. It runs in a fresh empty temp directory, deleted afterwards.
  - Never `--bare`: it ignores the subscription login.
  - The builder verifies every flag against `claude --help` at build time. A unit test pins the exact argv, so a refactor can't silently drop a safety flag.
  - It is a hard error, not a fallback, if the installed `claude` rejects the tools-off flag (e.g. an older version). `check()` reports "update Claude Code".
  - Limit detection: `is_error` true and the result text mentions a session/weekly/usage limit or credits → `LlmLimitReached`. A not-logged-in error → `LlmError` telling the user to run `claude` once to log in. A timeout → `LlmTimeout`.
  - `num_ctx` for budgeting (`fit_to_budget`) is a fixed per-backend constant (builder picks a safe value well under the model's window, e.g. 150,000 tokens). `llm_num_ctx` is ignored for this backend.
  - Model: `llm_model` takes `sonnet` / `haiku` / `opus` or a full model name. `auto` means `sonnet` (my call: tier 2 exists for good summaries; ~30 calls a night is modest). The walkthrough explains that `haiku` uses less quota.
  - Binary path: the walkthrough records the absolute path of `claude` in config (`llm_cli_path`), because the systemd unit's PATH may not include `~/.local/bin`. At run time: the recorded path, else `shutil.which`, else a clear error.
- **`none`**: abstracts-only mode. `make_llm` returns no model. The run skips summaries without an error, and the health line says `summaries off (abstracts-only mode)`. Commands that need a model (`rpipe summarize`, `rpipe eval run`, `rpipe profile suggest`) say "no summary model set up; run `rpipe model setup`".

### 2. Privacy guard

- A helper (e.g. `require_local(llm, what)`) raises before any shelf-derived content is sent to a backend with `local = False`. No current code path sends shelf content to a model, so today this is a tested guard for future features (overlap callouts in Phase 4's next slice). It is documented in `shelf.md` and the backends page.
- `rpipe profile suggest` and the init interview send the user's typed research description to the model. With a non-local backend, the prompt says which service will receive it before sending (my call: the user typed it on the spot, so a notice is enough, no block).

### 3. Config and commands

New config keys (all defaulted, so old vaults load unchanged):

- `llm_backend: ollama | claude-code | none` (default `ollama`).
- `llm_cli_path: str | null`: absolute path of the CLI binary, set by the walkthrough.
- `sources: [arxiv, hf_daily]`: enabled sources by registry name.

Commands (each validates input; `rpipe` is the only documented interface):

- `rpipe model setup`: the backend walkthrough on its own (re-runnable, also called by `rpipe init`).
- `rpipe model set-backend ollama|claude-code|none`: the non-interactive equivalent. It checks the backend is installed (and logged in) before saving, unless `--force`.
- `rpipe model set NAME`: now sets the model for the current backend (validated per backend).
- `rpipe model show` / `check`: report the backend too. `check` on `claude-code` does one tiny real call with a trivial schema, the same call the research verified, proving login, no-TTY JSON and tools-off all work.
- `rpipe model fit`, `set-url`, `set-context`: Ollama-only. On another backend they explain that and change nothing.
- `rpipe sources list|enable NAME|disable NAME`: list registered sources (built-in and plug-in) and toggle them. At least one source must stay enabled.
- `rpipe plugins`: list everything registered per group, with where it came from (built-in or which package).

### 4. Registry and entry points

- A small `registry.py` with two groups: `research_pipeline.sources` and `research_pipeline.backends`.
- Built-ins are registered in the project's own `pyproject.toml` entry points, so they go through the same path as third-party ones (my call: one code path, and it proves the mechanism works).
- A plug-in that fails to import is reported (`rpipe plugins`, and as a run error if it is enabled) and never crashes the run.
- `build_pipeline` constructs sources and the backend by name from config. Factories take the `Config` and return the object.
- Docs give a minimal worked example of a third-party source package.

### 5. Hardware detection and the walkthrough

**Detection** (a pure function returning a `MachineInfo` record; every probe is optional and times out fast):

- **GPU:** NVIDIA via `nvidia-smi --query-gpu=name,memory.total`; AMD via `rocm-smi`; Apple Silicon via `sysctl hw.memsize` + `uname -m` (unified memory, counted at a fraction the builder documents); otherwise "no GPU found".
- **Ollama:** is the binary on PATH, does the server answer at `llm_url`, which models are pulled.
- **Claude Code:** is the binary on PATH or in `~/.local/bin`; does `claude auth status` exit 0.
- Total RAM and OS, for messages.

**Recommendation rules:**

1. A usable GPU (VRAM at or above a threshold; builder sets it, around 6–8 GB) **and** Ollama present → recommend `ollama`.
   - No suitable model pulled → recommend one from a shipped table (`defaults/ollama_models.yaml`: VRAM band → model tag + download size; builder fills it with current tags verified to exist). Offer `ollama pull` with a yes/no, show progress, then run the existing fit.
   - GPU but no Ollama → print the official install command for the OS, offer to wait and re-check, or move on.
2. Otherwise, Claude Code installed and logged in → recommend `claude-code`. Say plainly:
   - paper text goes to Anthropic;
   - runs use the subscription's quota (~30 calls a night);
   - the shelf never leaves the machine.
3. Otherwise → recommend `none` (abstracts only). Say what would unlock more: a GPU with Ollama, or a Claude Code login. The CPU pipeline is planned.

The user can always pick a different tier than the one recommended. If both a GPU and Claude Code are present, local is recommended (owner's ideal: free and private) and Claude Code is offered as the alternative.

**Ending:** the walkthrough runs `rpipe model check` for the chosen backend. It ends "ready" only if the check passes. Otherwise it saves the choice and prints the exact fix, then how to re-run (`rpipe model setup`).

**`rpipe init` order:** vault → interests (existing interview) → backend step → check → next steps (schedule, first run). Non-TTY `init` keeps today's behaviour and prints `rpipe model setup` as a next step.

**`rpipe schedule install`:** already refuses without the `embed` extra. It now also runs the backend check and warns if the backend isn't ready. For `claude-code`, it confirms the recorded absolute CLI path exists.

### 6. Provenance

Each summary's provenance records `backend` alongside `model`, `prompt_version`, `num_ctx`. The digest footer shows it. Old summaries without it still display (the footer already tolerates missing fields).

### 7. Packaging

- `LICENSE`: MIT (owner). Copyright line: `Copyright (c) 2026 jrandall54` (owner).
- `README.md` rewritten as a stranger's quickstart:
  - what it is, what you need (Python 3.12+, `uv`, and one of: GPU + Ollama / Claude Code login / neither);
  - install (`uv tool install` from the git URL with the `embed` extra; `serve` optional);
  - `rpipe init`, the first run, `rpipe schedule install`;
  - the three tiers in a short table;
  - where the guide is; licence note, including that Marker is GPL if it is ever added.
- CI: a GitHub Actions workflow running `uv run pytest -q` on Linux and macOS (my call: macOS catches path/subprocess assumptions; tests are offline, so it's cheap). Pushing it is the owner's call.
- Optional extras unchanged (`embed`, `serve`).

### 8. Docs (same commit as each code change)

- New `docs/guide/backends.md`: tiers, how each backend works, the Claude Code safety flags and why, usage limits, privacy guard, how to add a backend.
- New `docs/guide/setup.md`: the walkthrough step by step, with a worked example transcript for each tier.
- New `docs/guide/plugins.md`: the registry, entry points, a worked third-party source example.
- Update `llm.md` (Ollama becomes one backend), `running.md`, `scheduling.md`, `data-model.md` (provenance, config keys), `summaries.md` (limit stop), `shelf.md` (privacy guard), `glossary.md` (backend, entry point, headless mode, subscription CLI, usage limit), `README.md`.
- Master plan doc (Claude Docs): rewrite the Phase 5 section to this scope; add the CPU pipeline as a separate project and the `agy` investigation as a side project; mark Docker and API keys as not planned.

### 9. Testing

- All offline. The `claude-code` adapter takes an injectable subprocess runner. Tests use canned JSON envelopes: success, invalid output then valid, `is_error` with limit text, not logged in, timeout, non-zero exit, old binary rejecting the tools flag.
- The argv test pins the safety flags.
- Hardware detection tests feed canned `nvidia-smi` / `rocm-smi` / `sysctl` output and missing binaries.
- Walkthrough tests drive prompts with scripted input for each tier, including declining the pull and a failing final check.
- Registry tests use a fake entry point (and a broken one).
- Existing-vault compatibility test: a config without the new keys loads and behaves as Ollama.

## Builder-check resolutions

A fresh reviewer read this design as its builder. Their questions, answered here, override anything above that conflicts.

1. **Privacy guard scope.**
   - Audit (a plan task): list every call site of `LLM.generate` and what text each sends: summaries, critiques, eval, profile suggest, the init interview.
   - Today none sends shelf content. Topics, keywords and seed titles are things the user typed, or public paper titles, so they get the same notice as the description, not a block.
   - `require_local` is wired at one choke point: a wrapper used by any future caller that sends shelf-derived text. For now it is called by no production code and covered by tests. The audit result goes in `backends.md`. (My call.)
2. **Seeds when `arxiv` is disabled.** Seed lookup (`rpipe profile add-seed`, `seed_fetcher`) always uses the arXiv API directly, independent of which sources are enabled for nightly collection. It's a lookup tool, not a collection source. The `Source` protocol does not grow `fetch_by_ids`. (My call.)
3. **Limit and timeout semantics.**
   - `LlmLimitReached`: stop the loop **without** calling `record_failure` for that paper. No retry is used, and the paper stays in its status for the next night.
   - `LlmTimeout` keeps today's behaviour: record the failure, then stop.
   - The `claude-code` subprocess gets its own timeout constant, sized for a cold start plus a long generation; the builder picks it (about 600 s, like Ollama's). (My call.)
4. **Built-ins without entry-point metadata.** Built-in sources and backends live in a hard-coded table in `registry.py`. Entry points are read only for third-party plug-ins, merged on top; a plug-in can't shadow a built-in name. This replaces "built-ins registered through entry points" in section 4: a stale editable install must not break every command. Tests use a fake entry point for the plug-in path. (My call.)
5. **Switching backend.**
   - `set-backend` resets `llm_model` to `auto` (which means `sonnet` on `claude-code` and today's fit on `ollama`) and leaves the Ollama-only keys (`llm_url`, `llm_num_ctx`) untouched but unused.
   - Switching back to `ollama` gives `auto`, today's default. The walkthrough then offers to pin a model as usual.
   - `model set` under `none` refuses with "no summary model set up; run `rpipe model setup`".
   - No per-backend model memory: simpler config, and the walkthrough re-asks anyway. (My call.)
6. **`none` and paper status.** No status-machine change. The digest already lists `triaged` papers without a summary, showing the abstract; that's what happens today when the model is unavailable. Under `none` the summarize stage is skipped, papers stay `triaged`, and the digest shows abstracts. Summaries made earlier stay and keep showing. `Pipeline.make_llm` may return `None` (typed `LLM | None`). Only the run's summarize stage and the model-needing commands check for it.
7. **Retry stacking.** Accepted as is. The client retries invalid JSON once, and the summariser retries bad citations once. With `--json-schema` enforcement, invalid JSON should be rare, so the realistic worst case is 2 calls per pass (summary, critique): about 4 calls per paper, rarely more. The usage-limit stop bounds the damage. (My call.)
8. **Master plan doc.** The coordinating session (not a subagent) updates the Claude Docs master plan as the plan's last task. It has write access, and the edit isn't committed to the repo.
9. **Licence name.** `jrandall54` (owner).

## Side projects recorded (not Phase 5)

- **Optimised CPU pipeline**: own brainstorming session. Ties in with the Phase 6 tier-comparison "script baseline".
- **Antigravity (`agy`) investigation**: an agent-driven deep dive into making `agy` a safe, reliable backend. The motivation: Gemini Pro is free for students with a valid school email, so `agy` would make tier 2 free for them. Open problems:
  - no-TTY hangs;
  - long prompts via `--input-format stream-json`;
  - no tools-off switch (what `--mode plan` / `--sandbox` actually block);
  - the consumer-plan wording on wrapping.

  It must meet the same anti-goals as `claude-code` before shipping.

## Left to the builder

- Exact Claude Code flag spellings (verified against the installed `--help`), the per-backend context budget constant, the VRAM threshold, the Apple Silicon memory fraction, and the model table contents.
- Module layout (e.g. `backends/` package vs flat modules) and how `Pipeline.make_llm` represents `none`.
- Wording of walkthrough prompts, within the guide's plain-language style.
