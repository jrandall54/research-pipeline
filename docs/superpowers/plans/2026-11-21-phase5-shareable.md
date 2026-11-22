# Phase 5 — Shareable Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Anyone can install `rpipe`, run a hardware-aware walkthrough, and get a working nightly digest on a local Ollama model, their Claude Code subscription, or in abstracts-only mode.

**Architecture:** A backend is anything implementing the `LLM` protocol plus `name`/`local`/`check()`. A small registry maps names to built-in backends/sources (hard-coded) plus third-party ones (Python entry points). A detection module describes the machine; a recommender picks a tier; the walkthrough (`rpipe model setup`, also first in `rpipe init`) saves the choice through validated `profile_edit` setters and ends with a real check.

**Tech Stack:** Python 3.12, typer, pydantic v2, httpx/respx, ruamel.yaml, subprocess, `importlib.metadata.entry_points`.

**Spec:** `docs/superpowers/specs/2026-11-21-phase5-shareable-design.md` (read its "Builder-check resolutions" section: it overrides the body where they conflict). Research: `docs/superpowers/specs/2026-11-21-cli-backends-research.md`.

## Global Constraints

- Every task updates the relevant `docs/guide/` page(s) in the same commit (page shape: What it does → How it works → Why → How to run/check → Where the code is). New terms go in `docs/guide/glossary.md`.
- Every user setting has an `rpipe` command that validates input; no documented workflow edits YAML.
- Tests are offline: no real `claude`, `nvidia-smi`, Ollama or network. `uv run pytest -q` passes after every task.
- A vault whose `config.yaml` lacks the new keys behaves exactly as today (Ollama).
- The Claude Code child process: all tools off, no user settings/hooks/MCP/CLAUDE.md, no session persistence, run in a fresh empty temp dir, prompt on stdin, never `--bare`, `ANTHROPIC_API_KEY` removed from its environment. rpipe never reads or stores credentials.
- Shelf (`my_work/`) content never reaches a backend whose `local` is false.
- `rpipe` never installs Ollama; it pulls a model only after a yes/no.
- Work on branch `phase-5-shareable` (created from `main` before Task 1); the owner merges.
- Use `rpipe` in all docs and messages. Commit after each task with the attribution trailer in the session's instructions.

## Review Focus

1. `ANTHROPIC_API_KEY` set in the user's shell → the child must not silently switch to API billing; it is stripped (test in Task 2).
2. The nightly systemd unit has no `~/.local/bin` on PATH, or the recorded `claude` path was moved → a clear error naming `rpipe model setup`, the digest is still written (tests in Tasks 2 and 4).
3. `llm_backend` or an enabled source names a plug-in that has since been uninstalled or fails to import → the run reports it in the health line and still writes the digest (test in Task 3).
4. Claude prints something before its JSON (an update notice), or reports `is_error` with exit code 0 → parsed or classified, never a crash (test in Task 2).
5. `nvidia-smi` exists but fails (driver mismatch, non-zero exit), or there are several GPUs → no crash; the largest GPU's memory is used (test in Task 5).

## Parallelism and file ownership

- Task 1 first (everyone builds on `LLM.name/local` and `LlmLimitReached`).
- Tasks 2 and 5 are independent of each other and of Task 3; they may run in parallel after Task 1.
- Task 3 owns `registry.py`, `models.py`, `collect.py` call site, `defaults/config.yaml`. Task 4 depends on 2 and 3. Task 6 depends on 4 and 5. Task 7 can start after Task 1 but its README must describe Task 6's commands, so run it last. Task 8 is the coordinator's.
- `cli.py` is touched by Tasks 3, 4, 6: never run those in parallel.

## File map

| File | Responsibility | Task |
|---|---|---|
| `src/research_pipeline/llm.py` | `LLM` protocol (+`name`,`local`), `LlmLimitReached`, `PrivacyError`, `require_local`, `OllamaClient` (+`name`,`local`), `pull_model` | 1, 6 |
| `src/research_pipeline/summarize.py` | limit stop; backend in provenance | 1 |
| `src/research_pipeline/db.py` | `save_summary(..., backend=)` | 1 |
| `src/research_pipeline/digest.py` | footer shows backend | 1 |
| `src/research_pipeline/backends/__init__.py`, `backends/claude_code.py` | Claude Code adapter + detection helpers | 2 |
| `src/research_pipeline/registry.py` | built-in tables, entry-point loading, `build_sources`, `build_backend` | 3, 4 |
| `src/research_pipeline/models.py` | `Config.llm_backend`, `llm_cli_path`, `sources` | 3 |
| `src/research_pipeline/profile_edit.py` | `enable_source`, `disable_source`, `set_llm_backend`, backend-aware `set_llm_model` | 3, 4 |
| `src/research_pipeline/machine.py` | `MachineInfo`, detection, `recommend` | 5 |
| `src/research_pipeline/defaults/ollama_models.yaml` | VRAM band → model tag table | 5 |
| `src/research_pipeline/cli.py` | `plugins`, `sources`, `model set-backend/setup`, walkthrough, init order, schedule check | 3, 4, 6 |
| `LICENSE`, `README.md`, `.github/workflows/tests.yml`, `pyproject.toml` | packaging | 7 |

---

### Task 1: Backend basics — names, locality, usage-limit stop, provenance

**Files:**
- Modify: `src/research_pipeline/llm.py`, `src/research_pipeline/summarize.py`, `src/research_pipeline/db.py` (`save_summary`), `src/research_pipeline/digest.py` (`_provenance_line`)
- Test: `tests/test_llm.py`, `tests/test_summarize.py`, `tests/test_digest.py`
- Docs: `docs/guide/llm.md` (Ollama is "the `ollama` backend"; usage-limit stop), `docs/guide/summaries.md` (limit stop vs timeout), `docs/guide/data-model.md` (provenance `backend`), `docs/guide/shelf.md` (privacy guard paragraph), `docs/guide/glossary.md` (backend, usage limit)

**Interfaces:**
- Produces:
  - `class LlmLimitReached(LlmError)` — a subscription usage limit was hit.
  - `class PrivacyError(LlmError)`.
  - `LLM` protocol gains `name: str` and `local: bool`.
  - `OllamaClient.name = "ollama"`, `OllamaClient.local = True` (class attributes).
  - `require_local(llm: LLM, what: str) -> None` — raises `PrivacyError(f"{what} never leaves this machine, but the {name} backend is not local; nothing was sent")` unless `getattr(llm, "local", False) is True` (missing attribute counts as not local).
  - `save_summary(..., backend: str | None = None)` stores `provenance["backend"]`.
  - Health-line error text for the stop: `"summarize: summaries stopped: usage limit reached; remaining papers wait for the next run"`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_llm.py
def test_ollama_client_is_named_and_local():
    c = OllamaClient("http://x", "m", 8192)
    assert (c.name, c.local) == ("ollama", True)

def test_require_local_blocks_non_local_and_unknown():
    class Cloud: name, local = "claude-code", False
    class Unknown: name = "x"
    require_local(OllamaClient("http://x", "m", 8192), "your shelf")  # no raise
    for llm in (Cloud(), Unknown()):
        with pytest.raises(PrivacyError, match="your shelf never leaves this machine"):
            require_local(llm, "your shelf")

# tests/test_summarize.py  (use the file's existing fake-LLM / vault helpers)
def test_usage_limit_stops_without_burning_a_retry(conn_with_two_triaged_papers):
    llm = FakeLLM(raises=LlmLimitReached("You've hit your weekly limit"))
    counts, errors = run_summarize(conn, llm, fetch_none, 8192)
    assert llm.calls == 1                       # second paper never sent
    assert all(p["retry_count"] == 0 and p["error"] is None for p in papers(conn))
    assert all(p["status"] == "triaged" for p in papers(conn))
    assert errors[-1] == "summarize: summaries stopped: usage limit reached; remaining papers wait for the next run"
    assert counts["summary_failed"] == 0

def test_timeout_still_records_failure_then_stops(...):  # pins today's behaviour
    ...  # LlmTimeout → first paper retry_count == 1, loop stops

def test_provenance_records_backend(...):
    # llm with name="claude-code", model_name="sonnet" → get_provenance(conn, pid)["backend"] == "claude-code"
```

```python
# tests/test_digest.py
def test_footer_shows_backend_before_model():
    assert _provenance_line({"backend": "claude-code", "model": "sonnet", "prompt_version": "p3"})[0] \
        == "_claude-code · sonnet · prompt p3_"

def test_footer_without_backend_unchanged():
    assert _provenance_line({"model": "qwen3:14b"})[0] == "_qwen3:14b_"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_llm.py tests/test_summarize.py tests/test_digest.py -q` — expected: the new tests FAIL (ImportError / assertion).
- [ ] **Step 3: Implement** in the files listed. In `run_summarize`'s `except`, check `LlmLimitReached` **before** `record_failure` and `counts["summary_failed"] += 1`: append the stop message and `break`. Pass `backend=getattr(llm, "name", None)` to `save_summary`. Do not wire `require_local` into any production call site (spec resolution 1).
- [ ] **Step 4: Privacy audit.** List every `.generate(` call site (`grep -rn "generate(" src/`): today `summarize._generate_checked` (paper text) and `onboarding.propose` (typed description). Write that table into `docs/guide/shelf.md` under "What never leaves your machine", stating no shelf-derived text reaches a model and that future code must call `require_local`.
- [ ] **Step 5: Run** `uv run pytest -q` — all pass.
- [ ] **Step 6: Commit** `feat: backend names, locality guard and usage-limit stop` (code + docs together).

---

### Task 2: Claude Code adapter

**Files:**
- Create: `src/research_pipeline/backends/__init__.py` (empty docstring), `src/research_pipeline/backends/claude_code.py`
- Test: `tests/test_claude_code.py`
- Docs: create `docs/guide/backends.md` (tiers overview; the Claude Code section: how a call works step by step with the argv, why each safety flag, the API-key stripping, usage limits, the June 2026 billing-policy risk from the research file); `docs/guide/glossary.md` (headless mode, subscription CLI, prompt injection if absent)

**Interfaces:**
- Consumes: `LlmError`, `LlmTimeout`, `LlmLimitReached` (Task 1).
- Produces (module `research_pipeline.backends.claude_code`):
  - Constants: `CONTEXT_BUDGET = 150_000`, `CALL_TIMEOUT = 600.0`, `STATUS_TIMEOUT = 15.0`, `DEFAULT_MODEL = "sonnet"`, `ALIASES = ("sonnet", "haiku", "opus")`.
  - `Runner = Callable[[list[str], str, float, Path, dict[str, str]], subprocess.CompletedProcess[str]]` — (argv, stdin text, timeout s, cwd, env); raises `subprocess.TimeoutExpired` / `FileNotFoundError` like `subprocess.run`.
  - `def subprocess_runner(argv, stdin, timeout, cwd, env) -> CompletedProcess[str]` — `subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)`.
  - `def resolve_model(name: str) -> str` — `auto`→`"sonnet"`; an alias, or a name starting `claude-`, returned as is; else `ValueError("... give sonnet, haiku, opus or a full claude-... model name")`.
  - `def find_claude(recorded: str | None) -> str | None` — the recorded path if it is an executable file, else `shutil.which("claude")`, else `~/.local/bin/claude` if executable, else `None`.
  - `def logged_in(cli_path: str, runner: Runner = subprocess_runner) -> bool` — `claude auth status` exit code 0.
  - `class ClaudeCodeClient` with class attrs `name = "claude-code"`, `local = False`; `__init__(self, model: str, cli_path: str, *, num_ctx: int = CONTEXT_BUDGET, timeout: float = CALL_TIMEOUT, runner: Runner = subprocess_runner)`; `model_name` = `resolve_model(model)`; `argv(self, system: str, schema: type[BaseModel]) -> list[str]`; `generate(system, user, schema)`; `check() -> str`.

Exact argv (verify every flag against the installed `claude --help` first; if a spelling differs, use the real one and update the pinned test and `backends.md`):

```python
[cli_path, "-p",
 "--output-format", "json",
 "--json-schema", json.dumps(schema.model_json_schema()),
 "--system-prompt", system,
 "--tools", "",
 "--setting-sources", "",
 "--strict-mcp-config",
 "--no-session-persistence",
 "--permission-mode", "dontAsk",
 "--model", self.model_name]
```

- [ ] **Step 1: Write failing tests** with a `FakeRunner` that records calls and returns canned `CompletedProcess` objects:

```python
def test_argv_pins_every_safety_flag():
    argv = ClaudeCodeClient("auto", "/opt/claude").argv("SYS", Out)
    for flag in ("-p", "--no-session-persistence", "--strict-mcp-config"):
        assert flag in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert "--bare" not in argv

def test_prompt_goes_on_stdin_in_empty_temp_dir_without_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    run = FakeRunner(ok('{"structured_output": {"ok": true}, "is_error": false}'))
    ClaudeCodeClient("haiku", "/opt/claude", runner=run).generate("S", "U" * 200_000, Out)
    argv, stdin, timeout, cwd, env = run.calls[0]
    assert stdin == "U" * 200_000 and "U" * 1000 not in " ".join(argv)
    assert "ANTHROPIC_API_KEY" not in env
    assert run.cwd_was_empty and not cwd.exists()      # created empty, removed afterwards
    assert timeout == CALL_TIMEOUT

def test_returns_validated_structured_output(): ...          # → Out(ok=True)
def test_banner_line_before_json_is_tolerated(): ...         # stdout "Update available\n{...}" → parsed (last JSON line)
def test_invalid_output_retried_once_then_llmerror(): ...    # 2 calls, then LlmError("...invalid output twice...")
@pytest.mark.parametrize("text", ["You've hit your session limit", "weekly limit reached", "out of credits"])
def test_limit_text_raises_limit_reached(text): ...          # is_error true, exit 0 AND exit 1 → LlmLimitReached
def test_not_logged_in_says_how_to_log_in(): ...             # → LlmError matching "run `claude` once and log in"
def test_timeout_raises_llmtimeout(): ...                    # runner raises TimeoutExpired → LlmTimeout
def test_missing_binary_names_model_setup(): ...             # FileNotFoundError → LlmError matching "rpipe model setup"
def test_old_cli_rejecting_tools_flag_says_update(): ...     # exit 1, stderr "unknown option '--tools'" → LlmError matching "update Claude Code"
def test_resolve_model(): ...                                # auto→sonnet; "claude-sonnet-5-5" kept; "gpt-4" ValueError
def test_find_claude_prefers_recorded_then_path_then_local_bin(tmp_path, monkeypatch): ...
def test_check_requires_login_then_one_tiny_call(): ...      # auth status exit 1 → LlmError; exit 0 + ok envelope → "Claude Code is logged in at /opt/claude and model sonnet answered."
```

- [ ] **Step 2: Run** `uv run pytest tests/test_claude_code.py -q` — FAIL (module missing).
- [ ] **Step 3: Implement** `claude_code.py`. Classification order on a finished call: parse envelope (whole stdout, else last non-empty line that parses as JSON); if `is_error` or exit≠0, take the message from `result`/stderr and match case-insensitively: `limit|credit` → `LlmLimitReached`; `log ?in|logged|authenticat` → `LlmError(... "run `claude` once and log in" ...)`; `unknown option|unrecognized` → `LlmError(... "update Claude Code" ...)`; else `LlmError(f"Claude Code failed: {msg[:200]}")`. Then validate `structured_output` with `schema.model_validate`; on failure retry once with the same correction suffix as `OllamaClient.generate`. Use `tempfile.TemporaryDirectory()` as cwd. Env = `os.environ` copy minus `ANTHROPIC_API_KEY`.
- [ ] **Step 4: Run** `uv run pytest -q` — all pass.
- [ ] **Step 5: Commit** `feat: Claude Code backend adapter` with `backends.md`.

---

### Task 3: Registry, config keys, sources by name

**Files:**
- Create: `src/research_pipeline/registry.py`
- Modify: `src/research_pipeline/models.py` (`Config`), `src/research_pipeline/defaults/config.yaml`, `src/research_pipeline/profile_edit.py`, `src/research_pipeline/cli.py` (`build_pipeline`, new `plugins` command and `sources` group)
- Test: `tests/test_registry.py`, `tests/test_models.py`, `tests/test_profile_edit.py`, `tests/test_e2e.py` (build_pipeline), `tests/test_cli_sources.py`
- Docs: create `docs/guide/plugins.md` (what a plug-in is, the two groups, the hard-coded built-ins and why — stale editable installs; a worked third-party source package: `pyproject.toml` `[project.entry-points."research_pipeline.sources"] mysource = "mypkg:make_source"` and a 15-line `make_source(config) -> Source`); update `docs/guide/sources.md` (sources by name, `rpipe sources ...`; seed lookup always uses arXiv), `docs/guide/data-model.md` / `vault-and-config.md` (new keys), `glossary.md` (entry point, plug-in)

**Interfaces:**
- Produces:
  - `Config.llm_backend: str = Field("ollama", pattern=r"^[a-z0-9][a-z0-9_-]*$")`, `Config.llm_cli_path: str | None = None`, `Config.sources: list[str] = ["arxiv", "hf_daily"]` (validator: non-empty, no duplicates, each matches the same pattern).
  - `registry.SOURCE_GROUP = "research_pipeline.sources"`, `registry.BACKEND_GROUP = "research_pipeline.backends"`.
  - `@dataclass(frozen=True) class Plugin: name: str; factory: Callable[..., Any] | None; origin: str; error: str | None = None` — `origin` is `"built-in"` or the distribution name; `error` set when the entry point failed to load (then `factory is None`).
  - `registry.plugins(group: str) -> dict[str, Plugin]` — built-ins, then entry points of that group; a plug-in whose name equals a built-in is skipped and reported as a `Plugin` with `error="name taken by a built-in"` under key `"<name> (<origin>)"`; any exception from `ep.load()` becomes `error`.
  - Source factory signature: `(config: Config) -> Source`. Built-ins: `arxiv` → `ArxivSource(config.arxiv_categories, max_results=config.arxiv_max_results)`, `hf_daily` → `HfDailySource()`.
  - `registry.build_sources(config: Config) -> tuple[list[Source], list[str]]` — enabled sources in config order; unknown/broken/raising ones become error strings `"source <name>: <reason>; see `rpipe sources list`"` and are skipped.
  - `Pipeline` gains `startup_errors: list[str] = field(default_factory=list)`; `build_pipeline` puts `build_sources` errors there; `_run_locked` appends them to `errors` before collect. `seed_fetcher` stays `make_arxiv(config).fetch_by_ids` regardless of enabled sources.
  - `profile_edit.enable_source(vault: Path, name: str, known: set[str]) -> bool` (False if already on; `ValueError` listing `known` if unknown) and `profile_edit.disable_source(vault: Path, name: str) -> bool` (`ValueError("at least one source must stay enabled")` for the last one).
  - CLI: `rpipe plugins` (per group: name, origin, `ok` or the error); `rpipe sources list` (each registered source, `on`/`off`, origin, error); `rpipe sources enable NAME`; `rpipe sources disable NAME`.

- [ ] **Step 1: Write failing tests**

```python
def test_config_without_new_keys_loads_with_defaults(tmp_path):  # old vault
    cfg = Config(vault_path=tmp_path)
    assert (cfg.llm_backend, cfg.llm_cli_path, cfg.sources) == ("ollama", None, ["arxiv", "hf_daily"])

def test_sources_must_be_nonempty_and_unique(): ...  # [] and ["arxiv","arxiv"] raise

def test_plugins_merges_entry_points_and_reports_broken(monkeypatch):
    good, bad, clash = fake_ep("mine", load=lambda: f), fake_ep("broken", load=raise_import), fake_ep("arxiv", load=lambda: f)
    monkeypatch.setattr(registry, "_entry_points", lambda group: [good, bad, clash])
    p = registry.plugins(registry.SOURCE_GROUP)
    assert p["arxiv"].origin == "built-in" and p["mine"].factory is f
    assert p["broken"].factory is None and "ImportError" in p["broken"].error
    assert p["arxiv (fake-dist)"].error == "name taken by a built-in"

def test_build_sources_skips_unknown_and_broken_with_messages(vault_config):
    cfg = vault_config.model_copy(update={"sources": ["hf_daily", "gone"]})
    sources, errors = registry.build_sources(cfg)
    assert [s.name for s in sources] == ["hf_daily"]
    assert errors == ["source gone: not installed; see `rpipe sources list`"]

def test_run_with_uninstalled_plugin_source_still_writes_digest(vault, monkeypatch): ...  # e2e: health line contains "source gone"
def test_disable_last_source_refused(vault): ...
def test_enable_unknown_source_lists_known(vault): ...
def test_cli_sources_list_shows_on_off(vault, runner): ...
```

- [ ] **Step 2: Run** the new tests — FAIL.
- [ ] **Step 3: Implement.** `_entry_points(group)` wraps `importlib.metadata.entry_points(group=group)` so tests can replace it. Add the three keys to `defaults/config.yaml` with plain-language comments naming `rpipe sources ...` and `rpipe model setup`. Existing tests that build `Pipeline(...)` positionally must still work (new field last, defaulted).
- [ ] **Step 4: Run** `uv run pytest -q` — all pass.
- [ ] **Step 5: Commit** `feat: plug-in registry and sources by name`.

---

### Task 4: Backend selection — `ollama`, `claude-code`, `none`

**Files:**
- Modify: `src/research_pipeline/registry.py` (backends), `src/research_pipeline/cli.py` (`build_llm`, `Pipeline.make_llm`, `_ready_llm`, `_run_locked`, `health_line`, `summarize`/`eval run`/`profile suggest` and init describe step, `model` commands), `src/research_pipeline/profile_edit.py`
- Test: `tests/test_registry.py`, `tests/test_cli_model.py` (new), `tests/test_e2e.py`, `tests/test_onboarding.py`
- Docs: `docs/guide/backends.md` (choosing a backend; `none`; which `rpipe model` commands apply to which backend — a table), `docs/guide/llm.md`, `docs/guide/running.md` (health line "summaries off"), `docs/guide/profile.md` (notice before sending the description to a non-local backend)

**Interfaces:**
- Consumes: `ClaudeCodeClient`, `find_claude`, `resolve_model`, `logged_in` (Task 2); `registry.plugins` (Task 3).
- Produces:
  - Backend factory signature: `(config: Config, vault: Path, log: Callable[[str], None]) -> LLM | None`. Built-ins: `ollama` (today's `build_llm` body), `claude-code` (`ClaudeCodeClient(config.llm_model, path)` where `path = find_claude(config.llm_cli_path)`; `None` path → `LlmError("Claude Code not found (looked at the recorded path, PATH and ~/.local/bin); run `rpipe model setup`")`), `none` (returns `None`).
  - `registry.build_backend(config, vault, log) -> LLM | None` — unknown/broken name → `LlmError(f"backend {name}: not installed; run `rpipe model setup`")`.
  - `cli.build_llm(config, vault, log=...) -> LLM | None` delegates to `registry.build_backend` (tests keep monkeypatching `cli.build_llm`). `Pipeline.make_llm: Callable[[], LLM | None]`; `_ready_llm` returns `None` without checking when the factory gives `None`.
  - `NO_MODEL = "No summary model is set up (abstracts-only mode). Run `rpipe model setup` to choose one."` — printed (exit 1) by `rpipe summarize`, `rpipe eval run`, `rpipe profile suggest`; the init describe step prints it and moves on.
  - In `_run_locked`, `llm is None` → `counts["summaries_off"] = 1`, no error; `health_line` adds ` · summaries off (abstracts-only mode)` when that count is set.
  - Before `onboarding.propose`, if `getattr(llm, "local", True) is False`: echo `f"Your description will be sent to {llm.name} (model {llm.model_name}) to suggest topics."`.
  - `profile_edit.set_llm_backend(vault: Path, name: str, known: set[str], cli_path: str | None = None) -> None` — validates `name in known`; sets `llm_backend`, `llm_model: "auto"`, and `llm_cli_path` (only when given; else left as is).
  - `profile_edit.set_llm_model(vault, name)` becomes backend-aware: reads the config's `llm_backend`; `claude-code` → `resolve_model` validation (store the user's text, e.g. `haiku`); `none` → `ValueError(NO_MODEL)`; `ollama` unchanged.
  - CLI: `rpipe model set-backend NAME [--force]` — without `--force` checks first (`ollama`: `OllamaClient(...).check()`-style reachability; `claude-code`: `find_claude` + `logged_in`), refuses with the fix if not ready; records `llm_cli_path` = `find_claude(...)` for `claude-code`. `rpipe model show` first line `backend: <name>`. `rpipe model fit|set-url|set-context` on a non-`ollama` backend: `"`rpipe model fit` only applies to the ollama backend (current: claude-code). Nothing was changed."`, exit 1. `rpipe model check` under `none` prints `NO_MODEL`, exit 1.

- [ ] **Step 1: Write failing tests**

```python
def test_none_backend_run_writes_abstract_digest_without_errors(vault, monkeypatch):
    set_backend(vault, "none")
    result = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert result.exit_code == 0
    assert "summaries off (abstracts-only mode)" in digest_text(vault)
    assert "errors: none" in digest_text(vault)   # use the health line's real "no errors" wording

def test_claude_code_missing_binary_run_still_writes_digest(vault, monkeypatch):
    # llm_backend claude-code, llm_cli_path "/gone/claude", which() → None, no ~/.local/bin/claude
    # → digest written; health line has "summaries skipped, model unavailable: Claude Code not found ... rpipe model setup"

def test_set_backend_resets_model_to_auto_and_keeps_ollama_keys(vault): ...
def test_set_backend_unknown_name_lists_known(vault): ...
def test_set_backend_claude_refuses_when_logged_out_unless_force(vault, monkeypatch): ...
def test_model_set_validates_per_backend(vault): ...      # claude-code: "haiku" ok, "qwen3:8b" ValueError; none: NO_MODEL
def test_ollama_only_commands_refuse_on_claude_code(vault): ...  # fit, set-url, set-context → exit 1, config unchanged (bytes equal)
def test_summarize_and_suggest_refuse_under_none(vault): ...
def test_suggest_announces_non_local_backend(vault, monkeypatch): ...  # fake llm local=False name="claude-code" → notice line printed before proposals
def test_build_backend_unknown_plugin_raises_with_setup_hint(): ...
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** Keep `build_llm`'s signature; `model fit`'s pinned branch and `model check` keep calling `build_llm(...).check()`.
- [ ] **Step 4: Run** `uv run pytest -q` — all pass.
- [ ] **Step 5: Commit** `feat: choose the summary backend (ollama, claude-code, none)`.

---

### Task 5: Machine detection and tier recommendation

**Files:**
- Create: `src/research_pipeline/machine.py`, `src/research_pipeline/defaults/ollama_models.yaml`
- Test: `tests/test_machine.py`
- Docs: `docs/guide/setup.md` (create; this task writes the "What the walkthrough looks at" and "How it recommends" sections, with the decision as a numbered list and the model table)

**Interfaces:**
- Consumes: `find_claude`, `logged_in` (Task 2).
- Produces:
  - Constants: `MIN_GPU_VRAM_GB = 6.0`, `APPLE_GPU_FRACTION = 0.66` (share of unified memory treated as GPU memory; document why), `PROBE_TIMEOUT = 5.0`.
  - `CmdRunner = Callable[[list[str], float], tuple[int, str]]` (argv, timeout → exit code, stdout; returns `(127, "")` when the binary is missing, `(124, "")` on timeout; never raises).
  - `@dataclass class Gpu: name: str; vram_gb: float; kind: Literal["nvidia", "amd", "apple"]`.
  - `@dataclass class MachineInfo: os: str; ram_gb: float | None; gpu: Gpu | None; ollama_installed: bool; ollama_running: bool; ollama_models: list[str]; claude_path: str | None; claude_logged_in: bool`.
  - `@dataclass(frozen=True) class ModelChoice: min_vram_gb: float; tag: str; download_gb: float`; `load_model_table() -> list[ModelChoice]` (sorted by `min_vram_gb`).
  - `detect_gpu(run: CmdRunner, system: str, arch: str) -> Gpu | None` — NVIDIA `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits` (MiB; largest GPU wins); AMD `rocm-smi --showmeminfo vram --json`; Darwin+arm64 `sysctl -n hw.memsize` × `APPLE_GPU_FRACTION`. Any non-zero exit or unparsable output → try the next, then `None`.
  - `detect_machine(config: Config, run: CmdRunner = system_cmd, which=shutil.which, tags: Callable[[str], list[str] | None] = ollama_tags) -> MachineInfo` — `ollama_tags(url)` returns pulled model names or `None` if the server does not answer within `PROBE_TIMEOUT`.
  - `OLLAMA_INSTALL: dict[str, str]` — Linux `curl -fsSL https://ollama.com/install.sh | sh`; Darwin `brew install ollama (or download it from https://ollama.com/download)`; Windows `download it from https://ollama.com/download` (builder confirms against ollama.com).
  - `@dataclass class Recommendation: backend: Literal["ollama", "claude-code", "none"]; pull: ModelChoice | None; install_ollama: bool; alternatives: list[str]; reasons: list[str]`.
  - `recommend(info: MachineInfo, table: list[ModelChoice]) -> Recommendation`:
    1. GPU ≥ `MIN_GPU_VRAM_GB` → `ollama`; `install_ollama = not info.ollama_installed`; `pull` = largest table entry with `min_vram_gb <= vram` when no models are pulled (else `None`); alternatives `["claude-code"]` if Claude is logged in, plus `"none"`.
    2. Else Claude logged in → `claude-code`; alternatives `["none"]` (+ `"ollama"` if installed).
    3. Else → `none`; reasons say what would unlock more (a GPU with ≥6 GB plus Ollama, or a Claude Code login) and that a CPU-friendly pipeline is planned.

- [ ] **Step 1: Write failing tests**

```python
def test_nvidia_largest_gpu_wins():
    run = cmds({"nvidia-smi": (0, "RTX 3060, 12288\nRTX 5070 Ti, 16303\n")})
    assert detect_gpu(run, "Linux", "x86_64") == Gpu("RTX 5070 Ti", pytest.approx(15.92, 0.01), "nvidia")

def test_failing_nvidia_smi_falls_through_to_none():
    assert detect_gpu(cmds({"nvidia-smi": (9, "Failed to initialize NVML: Driver/library version mismatch")}), "Linux", "x86_64") is None

def test_apple_silicon_uses_fraction_of_unified_memory(): ...   # 16 GiB → vram_gb ≈ 16*0.66 (GiB→GB as documented)
def test_amd_rocm_json_parsed(): ...
def test_recommend_gpu_with_ollama_no_models_offers_pull():
    rec = recommend(info(gpu=Gpu("x", 8.0, "nvidia"), ollama_installed=True, ollama_models=[]), TABLE)
    assert rec.backend == "ollama" and rec.pull.tag == TABLE_ENTRY_FOR_6GB.tag and not rec.install_ollama
def test_recommend_gpu_without_ollama_says_install(): ...
def test_recommend_small_gpu_with_claude_picks_claude(): ...      # 4 GB + logged in → claude-code
def test_recommend_nothing_gives_none_with_unlock_reasons(): ...
def test_model_table_loads_sorted_and_nonempty(): ...
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** Fill `ollama_models.yaml` with current tags verified on ollama.com/library (starting point: ≥6 GB `qwen3:8b`, ≥12 GB `qwen3:14b`, ≥24 GB `qwen3:32b`; record each download size from the library page and the date checked in a comment).
- [ ] **Step 4: Run** `uv run pytest -q` — all pass.
- [ ] **Step 5: Commit** `feat: machine detection and backend recommendation`.

---

### Task 6: The walkthrough — `rpipe model setup`, `rpipe init`, schedule check

**Files:**
- Modify: `src/research_pipeline/llm.py` (`pull_model`), `src/research_pipeline/cli.py` (`model setup`, `_wizard_backend`, `_wizard` order, non-TTY `init` next steps, `schedule install`)
- Test: `tests/test_setup_wizard.py` (new), `tests/test_init_wizard.py`, `tests/test_llm.py`, `tests/test_schedule.py`
- Docs: `docs/guide/setup.md` (walkthrough step by step plus a worked transcript per tier), `docs/guide/running.md` (init order), `docs/guide/scheduling.md` (backend check on install; why the absolute CLI path is recorded), `docs/guide/README.md` (add setup, backends, plugins to the reading order)

**Interfaces:**
- Consumes: `detect_machine`, `recommend`, `load_model_table`, `OLLAMA_INSTALL` (Task 5); `set_llm_backend`, `set_llm_model`, `build_llm` (Task 4); `ollama_fit.resolve`.
- Produces:
  - `pull_model(url: str, tag: str, progress: Callable[[str], None]) -> None` — streams `POST /api/pull` `{"model": tag}`; calls `progress` with a status line when the status text or whole-percent changes; raises `LlmError` on HTTP error or a streamed `{"error": ...}`.
  - `_wizard_backend(vault: Path) -> bool` — True when the final check passed.
  - `rpipe model setup` — runs `_wizard_backend`; exit 0 if ready, else exit 1 after saving the choice and printing the fix and `rpipe model setup`. Refuses without a terminal (`"rpipe model setup is interactive; use `rpipe model set-backend NAME` in scripts"`).
  - `rpipe schedule install|status|remove` on a non-Linux OS (`platform.system() != "Linux"`): print `"rpipe schedule uses systemd, which only exists on Linux. On <OS>, run `rpipe run` once a day from your own scheduler (see the README)."`, exit 1, nothing written. Test: `test_schedule_refuses_off_linux`.
  - `_wizard` order: **backend first**, then describe → topics → categories → seeds (plan-time correction: the describe step calls the model, so the backend must exist first).

Walkthrough flow (`_wizard_backend`):
1. Print what was found (one line each: OS, RAM, GPU name and memory or "no GPU found", Ollama installed/running/models, Claude Code found/logged in).
2. Print the recommendation and its reasons; numbered menu of `[recommended] + alternatives`, default 1.
3. `ollama`: if not installed, print `OLLAMA_INSTALL[os]`, then `typer.confirm("Installed it? Check again")`, looping until found or declined. If `rec.pull`, `typer.confirm(f"Pull {tag} now ({download_gb:g} GB download)?", default=True)` → `pull_model`. Save via `set_llm_backend(vault, "ollama", known)`.
4. `claude-code`: if not logged in, print "Run `claude` once in a terminal and log in, then come back", with the same re-check loop. Then explain: paper text goes to Anthropic; about 30 calls a night from your plan's quota; your shelf never leaves the machine; `haiku` uses less quota. Model prompt: `sonnet` default, `haiku`, `opus`. Save backend (with `cli_path`) then model.
5. `none`: save; print what it means.
6. Final check: `build_llm(config, vault, log=_progress)` → `.check()` (skipped for `none`, which counts as ready). Print `Ready: <check line>` or `Not ready yet: <error>` plus `Fix it, then run rpipe model setup`.

- [ ] **Step 1: Write failing tests** (monkeypatch `cli.detect_machine` and `cli.pull_model`, drive input with `runner.invoke(..., input="...")`, fake `build_llm`):

```python
def test_gpu_user_accepts_pull_and_ends_ready(vault, monkeypatch):
    # MachineInfo: 16 GB nvidia, ollama running, no models; input "1\ny\n"
    assert pulled == ["qwen3:14b"] and cfg(vault).llm_backend == "ollama"
    assert "Ready:" in out and result.exit_code == 0

def test_declining_pull_saves_choice_but_not_ready(vault, monkeypatch): ...   # check raises LlmError → exit 1, "rpipe model setup" in output
def test_no_ollama_prints_install_command_and_rechecks(vault, monkeypatch): ...  # second detect finds it
def test_claude_user_picks_haiku_and_cli_path_recorded(vault, monkeypatch): ...
def test_nothing_available_saves_none_and_is_ready(vault, monkeypatch): ...
def test_user_overrides_recommendation(vault, monkeypatch): ...             # recommended ollama, picks 2 = claude-code
def test_model_setup_refuses_without_terminal(vault): ...
def test_init_runs_backend_step_before_describe(vault, monkeypatch): ...    # call order recorded
def test_pull_model_streams_progress_and_raises_on_error(respx_mock): ...
def test_schedule_install_warns_when_backend_not_ready(vault, monkeypatch): ...  # installs anyway, prints warning + fix
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** Existing `tests/test_init_wizard.py` cases must keep passing: give them a default `detect_machine` stub (in `conftest.py`) returning "nothing found" and input that accepts the default, and adjust their scripted input for the new first step.
- [ ] **Step 4: Run** `uv run pytest -q` — all pass.
- [ ] **Step 5: Manual smoke (owner's machine, optional for the implementer):** `rpipe model setup` in a scratch vault (`RESEARCH_VAULT=$(mktemp -d) rpipe init`), choose Claude Code, check it ends "Ready". Record the output in the task report.
- [ ] **Step 6: Commit** `feat: hardware-aware setup walkthrough`.

---

### Task 7: Packaging — licence, README, CI

**Files:**
- Create: `LICENSE`, `.github/workflows/tests.yml`
- Modify: `README.md`, `pyproject.toml`, `docs/guide/README.md`, `docs/guide/running.md` (install section matches README)

**Interfaces:** none (consumes the commands from Tasks 3–6 for the README).

- [ ] **Step 1: `LICENSE`** — standard MIT text, line `Copyright (c) 2026 jrandall54`.
- [ ] **Step 2: `pyproject.toml`** — `license = "MIT"` and `license-files = ["LICENSE"]` (verify hatchling accepts PEP 639 fields with `uv build`; expected: a wheel whose metadata has `License-Expression: MIT`).
- [ ] **Step 3: `README.md`** — a stranger's quickstart, in this order:
  - one-paragraph what-it-is;
  - what you need: Python 3.12+, `uv`, and one of the three tiers, as a 3-row table (tier · you need · what you get);
  - install: `uv tool install "research-pipeline[embed] @ git+<origin URL from git remote get-url origin>"`; `[embed,serve]` for the phone browser;
  - `rpipe init`, then `rpipe run`, then `rpipe schedule install` (Linux; macOS/Windows: run `rpipe run` from your own scheduler);
  - daily use (vote, save, review);
  - the guide link;
  - licence (MIT; Marker is GPL if ever added).

  Keep it under ~120 lines.
- [ ] **Step 4: CI** — `.github/workflows/tests.yml`: on push and pull_request; matrix `ubuntu-latest`, `macos-latest`; `astral-sh/setup-uv` (pin a released major), `uv python install 3.12`, `uv sync` with whatever extras the offline suite imports (check: `grep -l "datasette\|sentence_transformers" tests/`), `uv run pytest -q`.
- [ ] **Step 5: Verify** `uv run pytest -q` passes and `uv build` succeeds; check every command named in the README exists in `rpipe --help` output.
- [ ] **Step 6: Commit** `docs: MIT licence, stranger's quickstart README, CI`.

---

### Task 8 (coordinator, not a subagent): master plan and handoff

- [ ] Update the Claude Docs master plan:
  - rewrite the Phase 5 section to the shipped scope;
  - add "Optimised CPU pipeline" as a separate project needing its own brainstorm;
  - add the `agy` investigation side project, with the student-free-Gemini motivation;
  - mark Docker and API-key backends as not planned;
  - note that Phase 4 slices 2–4 remain.
- [ ] Rewrite `docs/superpowers/handoff.md` for the post-Phase-5 state, including owner actions (verify Claude Code adapter flags on a real run; reinstall the tool; push CI if wanted).
- [ ] Final whole-branch review, then hand the merge decision to the owner.
