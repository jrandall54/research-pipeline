# Automatic Model and Context Selection: Implementation Plan

> **Phase 2.1** · Status: merged · Design: the master plan (archived, see ../ROADMAP.md) · Roadmap: [ROADMAP.md](../ROADMAP.md)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** With no setup, the pipeline asks Ollama which models are installed, picks the largest suitable one that fits fully in GPU memory, and finds the largest context window that still fits, with no user interaction. Users can still pin a model or a context with `rpipe model set` and `rpipe model set-context`.

**Architecture:** A new module `ollama_fit.py` does discovery (`/api/tags`, `/api/show`), fit probing (load the model at a given context, then read `/api/ps` to see whether it is fully in GPU memory), a binary search for the largest fitting context, and a vault-cached choice. `Config.llm_model` and `Config.llm_num_ctx` accept `auto` (the new default). The CLI resolves `auto` through the module before building the client. Nothing is ever created, rebuilt or deleted in Ollama; the context is sent with each request, as Phase 2 already does.

**Tech Stack:** Python 3.12, httpx/respx, pydantic v2, typer, pytest.

**Spec:** the owner's request in this session. The owner's own `o` script (`~/.local/bin/o`) has the proven algorithm: preload with an empty prompt, compare `size_vram` to `size` in `/api/ps`, binary-search the context in 1024-token steps. This plan ports the algorithm into the program, with no dependency on `o` and without its rebuild/delete step. Also binding: `CLAUDE.md` (docs in the same commit; users never hand-edit files; tests offline) and the Phase 2 plan's Global Constraints that still apply (Ollama call shape, 8192 context floor, per-paper failure isolation, Ollama down never fails `rpipe run`).

## Global Constraints

- The CLI is `rpipe`. Every message, help text and doc example uses `rpipe`. The program must not call or require the `o` script.
- No user interaction: no prompts during `rpipe run`, `rpipe summarize`, `rpipe eval run` or `rpipe model fit`.
- Never create, rebuild, copy or delete an Ollama model. Probing loads models and must unload each one it loaded (`keep_alive: 0`) when done, including after an error.
- Defaults: `llm_model: auto`, `llm_num_ctx: auto`. An explicit value always wins and is never overridden. Existing vaults with explicit values keep working unchanged.
- Context bounds: floor 8192 (the `Config` floor), search step 1024, ceiling = `min(model's trained context length, 131072)`.
- A model is a candidate only if `/api/show` lists capability `completion` and not `embedding`. Choice order: parameter count, largest first. A candidate is acceptable only if it fits fully in GPU memory at a context of at least 16384; otherwise try the next one. If none is acceptable, fail with an `LlmError` that says which models were tried and why, and names `rpipe model set` as the fix.
- The probe result is cached in `<vault>/cache/llm_fit.json`, keyed by the Ollama URL plus each installed model's name and digest. A changed key means re-probe. `rpipe model fit` forces a re-probe. The cache write is atomic.
- Ollama down or no models installed never fails `rpipe run`: the existing "summaries skipped" path applies.
- Probing timeouts: each probe request at most 300 s. A failed or timed-out probe counts as "does not fit".
- Tests run offline (respx). Test output pristine. `uv run pytest -q` passes after every task.
- Every task updates its docs in the same commit (page shape per `CLAUDE.md`); new terms go in `docs/guide/glossary.md`.
- Commit trailer, exactly:
  ```
  Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01STEoRkeRYCpoD8UnCvt6XB
  ```

## Review Focus

1. **Ollama is up but nothing is installed, or only embedding models.** A clear `LlmError` saying to install a model (`ollama pull NAME`); `rpipe run` still writes the digest.
2. **Another program already holds the GPU** (probe says a small model does not fit). The next candidate or a smaller context is chosen; no crash; the error lists what was tried.
3. **A probe load fails or times out partway.** The model is still unloaded; the result is "does not fit", never an exception out of the search.
4. **Cache goes stale** (a model was removed, replaced or re-pulled). The key changes, so it is re-probed; a cached choice whose model is no longer installed is never used.
5. **The user pinned only one of the two settings.** `llm_model: some-name` with `llm_num_ctx: auto` probes the context for that model only; `llm_model: auto` with a pinned context picks the model that fits at that context.

## File Structure

```
src/research_pipeline/ollama_fit.py   # discovery, fit probe, context search, choose, cache
src/research_pipeline/models.py       # Config: llm_model / llm_num_ctx accept "auto"
src/research_pipeline/llm.py          # OllamaClient unchanged in behaviour
src/research_pipeline/profile_edit.py # setters accept "auto"
src/research_pipeline/cli.py          # resolve auto in build_llm; `rpipe model fit`; show
src/research_pipeline/defaults/config.yaml
tests/test_ollama_fit.py, tests/test_cli_profile.py / test_llm.py additions, tests/test_e2e.py (auto path)
docs/guide/llm.md, vault-and-config.md, running.md, glossary.md, README.md
```

---

### Task 1: Discovery, fit probe, context search, choice and cache

**Files:**
- Create: `src/research_pipeline/ollama_fit.py`, `tests/test_ollama_fit.py`
- Modify: `docs/guide/llm.md` (new section "Choosing the model and context automatically"), `docs/guide/glossary.md`

**Interfaces:**
- Produces:
  - `class ModelInfo(BaseModel)`: `name: str`, `digest: str`, `params: int`, `max_context: int | None`, `capabilities: list[str]`.
  - `class Fit(BaseModel)`: `model: str`, `num_ctx: int`.
  - `def list_models(url: str) -> list[ModelInfo]`: `GET /api/tags` for names and digests, then `POST /api/show` per model for `model_info["general.parameter_count"]`, the `*.context_length` entry and `capabilities`. Raises `LlmError` (from `llm.py`) when Ollama is unreachable.
  - `def fits(url: str, model: str, num_ctx: int, timeout: float = 300.0) -> bool`: `POST /api/generate` with `{"model", "prompt": "", "keep_alive": "5m", "options": {"num_ctx": num_ctx}}`; any error, `error` field or timeout returns False; then `GET /api/ps` and return True only when that model's `size_vram >= size`. Always finishes by unloading the model (`POST /api/generate` with `keep_alive: 0`), even on failure.
  - `def max_context(url: str, model: str, ceiling: int, floor: int = 8192, step: int = 1024) -> int | None`: None if it does not fit at `floor`; otherwise the largest multiple of `step` (at least `floor`, at most `ceiling`) that fits, by binary search as in `o`'s `find_max_ctx`.
  - `def choose(url: str, models: list[ModelInfo], pinned_model: str | None, pinned_ctx: int | None, min_useful: int = 16384, log: Callable[[str], None] = lambda s: None) -> Fit`: applies the Global Constraints (candidates, order, pinning cases, `LlmError` listing what was tried). With `pinned_ctx` set, "fits" means `fits(..., pinned_ctx)`. With `pinned_model` set, only that model is tried (an explicit model with no fitting context raises `LlmError`).
  - `def resolve(url: str, vault: Path, model: str, num_ctx: int | str, force: bool = False, log=...) -> Fit`: `model`/`num_ctx` are the config values (`"auto"` or explicit). If both are explicit, returns them without any request. Otherwise computes the cache key from `list_models`, returns the cached `Fit` when the key matches and `force` is False and the cached model is still installed, else runs `choose` and writes `<vault>/cache/llm_fit.json` atomically (follow `seeds._write_cache`).

- [ ] **Step 1: Write failing tests** in `tests/test_ollama_fit.py` using respx and a small fake Ollama state (a dict of models with sizes, a free-GPU-memory number; `/api/generate` records the requested `num_ctx` and the model's `size_vram` in `/api/ps` is `size` when `model_size + kv(num_ctx) <= free`, else less than `size`, so the fit boundary is deterministic):
  - `test_list_models_reads_params_context_capabilities_digest`.
  - `test_fits_true_when_fully_in_vram_and_unloads_after`: the last request is the `keep_alive: 0` unload, also when the probe errored.
  - `test_fits_false_on_error_field_timeout_and_partial_offload`.
  - `test_max_context_finds_boundary`: with a boundary at 22528 and ceiling 40960, returns 22528; returns None when 8192 does not fit; never exceeds ceiling; the call count is logarithmic (assert it is less than 12 probes).
  - `test_choose_picks_largest_model_that_fits_at_16k`: three models; the biggest does not fit at 16K, so the second is chosen with its maximum context.
  - `test_choose_skips_embedding_only_models` and `test_choose_no_models_raises_llmerror_naming_ollama_pull`.
  - `test_choose_pinned_model_only_tries_it` and `test_choose_pinned_ctx_picks_model_that_fits_at_that_ctx`.
  - `test_choose_all_fail_error_lists_tried_models_and_rpipe_model_set`.
  - `test_resolve_explicit_values_make_no_requests`.
  - `test_resolve_uses_cache_and_reprobes_on_key_change`: second call makes no `/api/generate` call; after changing a digest it probes again; a cached model that is no longer installed is not used; `force=True` re-probes.
  - `test_resolve_cache_write_is_atomic` (no leftover temp files) and `test_resolve_corrupt_cache_is_ignored_and_rewritten`.
- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_ollama_fit.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement** `ollama_fit.py` as specified. Port the algorithm from `~/.local/bin/o` (`preload`, `mem_state`, `fully_on_gpu`, `find_max_ctx`) in Python; read that script for the exact `/api/generate` and `/api/ps` usage. Do not port the rebuild/delete logic.
- [ ] **Step 4: Docs.** `llm.md` section covers: what it does, the step-by-step (discovery → probe → search → pick → cache) with a worked example using the owner's installed models, why "fits fully in GPU memory" is the test (CPU spill makes generation many times slower), why the context is sent per request rather than rebuilding models (Ollama's model files are the user's; rebuild/delete is destructive), the "best model = largest that fits" heuristic and its limits (Ollama has no quality ranking; the gold-set eval is the real comparison), what probing costs (loads and unloads models; minutes on first run; cached afterwards), and how to pin instead. Glossary: VRAM, GPU offload, KV cache, probe.
- [ ] **Step 5: Run all tests** — `uv run pytest -q` → all pass.
- [ ] **Step 6: Commit** — `git commit -m "feat: probe Ollama for the best model and largest fitting context"` (with the trailer).

---

### Task 2: `auto` settings, wiring and `rpipe model fit`

**Files:**
- Modify: `src/research_pipeline/models.py`, `src/research_pipeline/profile_edit.py`, `src/research_pipeline/cli.py`, `src/research_pipeline/defaults/config.yaml`, `tests/test_llm.py`, `tests/test_cli_profile.py`, `tests/test_e2e.py`, `docs/guide/llm.md`, `docs/guide/vault-and-config.md`, `docs/guide/running.md`, `docs/guide/README.md`

**Interfaces:**
- Consumes: `resolve`, `Fit`, `list_models` (Task 1); the existing `build_llm`, `_ready_llm`, `Pipeline.make_llm`, `run_summarize(conn, llm, fetch, num_ctx)`, `run_eval(..., num_ctx, ...)`.
- Produces:
  - `Config.llm_model: str = "auto"` (non-empty) and `Config.llm_num_ctx: int | Literal["auto"] = "auto"`; an int still needs `ge=8192` (validator), and an invalid value is rejected with a clear message naming `auto`.
  - `profile_edit.set_llm_model` accepts `auto`; new `profile_edit.set_llm_num_ctx` accepts an int or `auto`.
  - `build_llm(config: Config, vault: Path) -> OllamaClient`: resolves through `ollama_fit.resolve` and builds the client from the resolved `Fit`. The resolved context is exposed as `client.num_ctx`, and every call site that passed `config.llm_num_ctx` to `run_summarize`/`run_eval` passes the client's `num_ctx` instead. Resolution failure raises `LlmError`, which the existing "summaries skipped" and "model unavailable" paths already handle.
  - CLI: `rpipe model fit` (re-probes now, prints `Using model X with a context of N tokens` and what was tried), `rpipe model set auto`, `rpipe model set-context auto`; `rpipe model show` prints the configured values and, when auto, the last cached choice (or "not probed yet; runs on first use, or `rpipe model fit`").

- [ ] **Step 1: Write failing tests:**
  - `Config()` defaults are `auto`/`auto`; `Config(llm_num_ctx=100)` and `Config(llm_num_ctx="big")` fail validation; `Config(llm_num_ctx=22000)` passes; an old vault's config.yaml with explicit values loads unchanged.
  - `rpipe model set auto` and `rpipe model set-context auto` write `auto`, keep comments, leave the file unchanged on invalid input (follow the existing set-model tests).
  - `rpipe model fit` with respx-faked Ollama prints the chosen model and context and writes the cache; with no models installed exits 1 with the `ollama pull` hint; `rpipe model show` before and after a fit.
  - e2e: `rpipe run` in a vault with defaults and a faked Ollama (two models) summarizes using the chosen model, and the `/api/chat` request carries the resolved `num_ctx` and `model`; a second `rpipe run` makes no probe requests (cache). With only embedding models installed, `rpipe run` exits 0, writes the digest and the health line names the cause.
  - Pinned model + `auto` context probes only that model (`test_cli`/e2e level).
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** as specified; keep `Pipeline.make_llm` a factory so tests still inject a `FakeLLM` (the fake carries `num_ctx` too; update `FakeLLM` in `tests/conftest.py` if needed). Update `defaults/config.yaml` comments and values so `rpipe init` writes `auto`.
- [ ] **Step 4: Docs.** Update `llm.md` (settings table, `rpipe model fit`, troubleshooting: "no model fits", "probe is slow", "I want to pin a model"), `vault-and-config.md` (new defaults and `cache/llm_fit.json`), `running.md` (the command with illustrative output labelled as such, and a note that the first run after installing a model takes a few extra minutes), `README.md`. Mention that the owner's `o` script is an optional convenience for managing models and is not required.
- [ ] **Step 5: Run all tests** — `uv run pytest -q` → all pass.
- [ ] **Step 6: Commit** — `git commit -m "feat: auto-select the Ollama model and context, add rpipe model fit"` (with the trailer).

---

## Self-Review

- **Spec coverage:** pick best installed model automatically (Task 1 `choose`), maximize context (Task 1 `max_context`), no user interaction (Global Constraints; no prompts anywhere), built into the program and independent of `o` (Task 1 port; Task 2 docs), pinning still possible and settable by command (Task 2). Review Focus 1-5 each map to a named test.
- **Type consistency:** `Fit(model, num_ctx)` from Task 1 is what Task 2's `build_llm` consumes; `resolve` takes the config values (`str`, `int | "auto"`) exactly as Task 2's `Config` produces them.
- **Proportion:** two tasks; the first is the algorithm and its tests, the second wires it and changes the settings.
