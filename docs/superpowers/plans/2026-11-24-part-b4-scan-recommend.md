# Part B.4: Machine Scan, Model Catalog, Recommender and Guided Settings (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `rpipe` looks at the machine, recommends models that suit it (installed or not) and the Ollama server settings they need, applies those settings with consent and undo, and measures speed, so every hardware tier from CPU only to a large GPU gets a working, explained setup.

**Architecture:** Four new modules with injected runners, plus thin CLI commands under `rpipe model`. `scan.py` reads the machine and the Ollama server. `catalog.py` loads a versioned YAML list of candidate models (shipped copy, or a newer copy fetched by `rpipe model catalog update`). `recommender.py` places each catalog entry on this machine (GPU, partly in RAM, or CPU), estimates its context and ranks them. `ollama_tune.py` advises and writes one systemd drop-in file. `bench.py` measures read and write speed. Measuring (`rpipe model fit`, `rpipe model bench`) stays the final word; the catalog is only a starting guess.

**Tech Stack:** Python 3.12, typer, pydantic v2, PyYAML, httpx + respx (tests), pytest. `uv run pytest -q`, `uv run ruff check .`.

**Spec:** `docs/superpowers/specs/2026-11-22-part-b-hardware-adaptive-design.md`, section "4. Machine scan, recommender, and guided settings", plus "Settled" items 6, 7, 8. Read both.

## Global Constraints

- Tests run offline: every command, HTTP call and clock goes through an injected runner or `respx`; no test runs `nvidia-smi`, `systemctl`, `sudo` or a real Ollama.
- Every waiting step reports through `progress.py` (`stage(...)` from the CLI's `_reporter()`).
- Every change updates `docs/guide/` in the same commit; new terms go in `docs/guide/glossary.md` in plain words. The new page is `docs/guide/hardware.md` (shape: What it does → How it works → Why it's built this way → How to run / check it → Where the code is); link it from `docs/guide/README.md`.
- No owner model names in the repo (the owner's own Ollama model tags must never appear). Public pull names (`qwen3:14b`, `hf.co/unsloth/...`) are fine.
- No documented workflow may require hand-editing a file. Every setting a command changes goes through `profile_edit` setters that already exist (`set_llm_model`, `set_llm_num_ctx` or equivalent; check `profile_edit.py` names before use).
- Nothing changes the system or downloads without a yes from the user after seeing what will happen (size vs free disk; exact command; how to undo). Interactive commands refuse to run without a terminal (`_is_tty()`), the way `rpipe model setup` does.
- Use `rpipe` in all messages and docs. Commit per task; `git add` named files only (never `-A`: the owner's `.idea/` is untracked).
- Commit trailers: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01STEoRgFdUaSzkzddpJSKWg`.

## Decisions this plan makes (spec "Left to the builder")

| Spec item | Decision |
|---|---|
| Command names | `rpipe model scan`, `rpipe model recommend`, `rpipe model tune [--undo]`, `rpipe model bench`, `rpipe model catalog show`, `rpipe model catalog update`. All under `model`, next to `fit`/`setup`. |
| Catalog schema | See Task 2. Stored in `src/research_pipeline/defaults/model_catalog.yaml`; a fetched copy in `VAULT/cache/model_catalog.yaml`; the higher `version` wins. |
| Expected time per paper | Shown only when measured (`rpipe model bench`); otherwise "not measured yet". No invented speed constants (deviation from spec, which asked for an estimate: we have one machine's data, too little to model others honestly). |
| CPU-only machines | Recommended an Ollama CPU model (overnight, slow) when no cloud login; with a Claude Code login, Claude Code first. Choosing a CPU or partly-in-RAM model pins both model and context, because the fit gate refuses models not fully on the GPU (B.5 will change that). |
| Tuning scope | Automatic only for the systemd **system** service on Linux. Other setups (user service, manual `ollama serve`, Docker, macOS/Windows app) get printed exact instructions and the undo. |
| Settings offered | `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0` (measured: 27B context 32,768 → 57,344), and `OLLAMA_NUM_PARALLEL=1` only when the current value is above 1 (each parallel slot holds its own context memory). |
| Opt-in sharing | Not built (spec: design only, later). One line in `hardware.md` says so. |

## Review Focus

1. **The Ollama service rpipe would tune is not the server rpipe talks to** (the owner's service sets `OLLAMA_HOST=127.0.0.1:11436` while rpipe uses `localhost:11434`). Expected: scan reports the mismatch; `tune` warns and defaults to "no". Test in Task 1 (`test_host_mismatch_flagged`) and Task 5 (`test_tune_warns_on_host_mismatch`).
2. **Settings already set by another drop-in** (the owner's `kv-cache.conf`). Expected: `tune` says "already set" and writes nothing; `--undo` removes only `rpipe.conf`, never other files. Test in Task 5.
3. **`systemctl show` output with quoted values** (`"OLLAMA_ORIGINS=*"`, values with spaces). Expected: parsed with `shlex`, no crash. Test in Task 1.
4. **A fetched catalog that is invalid, older, or unreachable.** Expected: shipped copy used; `catalog update` says why and saves nothing. Test in Task 2.
5. **Not enough free disk for a recommended download, or a CPU-only machine with little RAM.** Expected: the download is refused with sizes shown; a model that cannot fit in RAM is not recommended. Tests in Task 6.

---

### Task 1: Machine scan (`scan.py`, `rpipe model scan`)

**Files:**
- Create: `src/research_pipeline/scan.py`
- Modify: `src/research_pipeline/machine.py` (`MachineInfo` gains defaulted fields; `detect_machine` fills them)
- Modify: `src/research_pipeline/cli.py` (new `model scan` command; `_describe_machine` shows the new facts)
- Create: `tests/test_scan.py`; Modify: `tests/test_machine.py` only if an existing assertion breaks
- Docs: create `docs/guide/hardware.md` (sections for scan; later tasks add theirs), `docs/guide/README.md`, `docs/guide/glossary.md` (CPU vector instructions/AVX, systemd drop-in file, VRAM)

**Interfaces (Produces):**
```python
# scan.py
@dataclass
class CpuInfo:
    threads: int
    vector: list[str]          # subset of ["avx2", "avx512", "neon"], in that order

@dataclass
class OllamaServer:
    version: str | None        # GET {url}/api/version -> {"version": "0.34.0"}
    how: Literal["systemd", "systemd-user", "manual", "docker", "app", "unknown"]
    env: dict[str, str]        # OLLAMA_* variables the server was started with (systemd: `systemctl show`)
    drop_ins: list[str]        # absolute paths of drop-in files (systemd only)
    host_matches: bool | None  # service OLLAMA_HOST port == port of config.llm_url; None if unknown

def detect_cpu(run: CmdRunner, system: str) -> CpuInfo
def ram_free_gb(system: str) -> float | None            # Linux: MemAvailable from /proc/meminfo
def disk_free_gb(path: Path) -> float | None             # shutil.disk_usage on the nearest existing parent
def ollama_models_dir(server: OllamaServer) -> Path      # env OLLAMA_MODELS, else /usr/share/ollama/.ollama/models for "systemd", else ~/.ollama/models
def detect_ollama_server(url: str, run: CmdRunner, version: Callable[[str], str | None] = ollama_version) -> OllamaServer
def ollama_version(url: str) -> str | None
def parse_systemd_env(line: str) -> dict[str, str]       # "Environment=A=1 \"B=x y\"" -> {"A": "1", "B": "x y"}; only keys starting OLLAMA_
def all_gpus(run: CmdRunner, system: str, arch: str) -> list[Gpu]   # every NVIDIA/AMD card, not just the best
```
```python
# machine.py — MachineInfo gains (all defaulted so existing constructors keep working):
gpus: list[Gpu] = field(default_factory=list)
cpu: CpuInfo | None = None
ram_free_gb: float | None = None
disk_free_gb: float | None = None     # free space where Ollama stores models
ollama: OllamaServer | None = None
```
`detect_machine` keeps its current parameters and adds `server: Callable[[str, CmdRunner], OllamaServer] = detect_ollama_server`. `gpu` stays (the best card) for existing callers.

How detection works (the implementer picks the code):
- CPU: Linux reads `/proc/cpuinfo` flags (`avx2`, `avx512f` → "avx512"); threads from `os.cpu_count()`. macOS arm64 → `["neon"]`. Inject the cpuinfo text in tests via a `read_text` parameter defaulting to reading the file.
- Server `how`: `systemctl is-active ollama` == "active" → "systemd"; else `systemctl --user is-active ollama` == "active" → "systemd-user"; else `docker ps --filter ancestor=ollama/ollama -q` non-empty → "docker"; else macOS/Windows with the server answering → "app"; else server answering → "manual"; else "unknown". Every probe returns (127, "") when the tool is missing; that is not an error.
- systemd env: `systemctl show ollama --property=Environment` (add `--user` for systemd-user) and `--property=DropInPaths` (space-separated).
- `host_matches`: compare port in `env["OLLAMA_HOST"]` (default 11434 when absent) with the port of `config.llm_url`.

- [ ] **Step 1: Write the failing tests** in `tests/test_scan.py`:
  - `test_parse_systemd_env_handles_quotes`: input `'Environment=PATH=/bin OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 "OLLAMA_ORIGINS=*" "OLLAMA_X=a b"'` → `{"OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0", "OLLAMA_ORIGINS": "*", "OLLAMA_X": "a b"}` (PATH dropped).
  - `test_detect_cpu_flags`: cpuinfo text with `flags : fpu avx2 avx512f` → `CpuInfo(threads=..., vector=["avx2", "avx512"])`.
  - `test_systemd_server_detected`: fake runner answering `systemctl is-active ollama` → "active\n", `show ... Environment` → env line, `show ... DropInPaths` → `"DropInPaths=/etc/systemd/system/ollama.service.d/kv-cache.conf"`; version stub "0.34.0" → `how == "systemd"`, env parsed, `drop_ins == ["/etc/systemd/system/ollama.service.d/kv-cache.conf"]`.
  - `test_host_mismatch_flagged`: env `OLLAMA_HOST=127.0.0.1:11436`, config url `http://localhost:11434` → `host_matches is False`; no OLLAMA_HOST → `True`.
  - `test_missing_tools_mean_unknown`: runner returns (127, "") for all, version None → `how == "unknown"`, `env == {}`, no exception.
  - `test_models_dir_prefers_env`: env `OLLAMA_MODELS=/data/m` → `Path("/data/m")`; systemd without it → `/usr/share/ollama/.ollama/models`.
  - `test_all_gpus_lists_every_card`: nvidia-smi two lines → two `Gpu` objects.
  - `test_model_scan_command_prints_sections` (CliRunner, `monkeypatch` `cli.detect_machine` to return a filled `MachineInfo`): output contains "GPU", "memory", "CPU", "Ollama", "flash attention", "free disk".
- [ ] **Step 2: Run** `uv run pytest tests/test_scan.py -q` — expect failures (module missing).
- [ ] **Step 3: Implement** `scan.py`, the `MachineInfo` fields, and `rpipe model scan` (non-interactive; prints every fact in plain words, e.g. `flash attention: on (set in kv-cache.conf)`, `context memory type (KV cache): q8_0`, `parallel slots: 1`, and when `host_matches is False`: `note: the Ollama service listens on port 11436 but rpipe talks to localhost:11434; settings rpipe changes would go to the service, not the server rpipe uses`). Extend `_describe_machine` to show RAM free, CPU, free disk.
- [ ] **Step 4: Run** `uv run pytest -q` and `uv run ruff check .` — all pass.
- [ ] **Step 5: Docs** — `hardware.md` sections "What it does" (overview of the whole B.4 flow: scan → recommend → tune → bench) and "The scan"; README link; glossary entries.
- [ ] **Step 6: Commit** `feat: rpipe model scan reads CPU, RAM, disk, every GPU and the Ollama server settings`.

### Task 2: Model catalog (`catalog.py`, `rpipe model catalog show|update`)

**Files:**
- Create: `src/research_pipeline/catalog.py`, `tests/test_catalog.py`, `tests/fixtures/catalog_small.yaml`
- Modify: `src/research_pipeline/cli.py` (a `catalog_app` typer added under `model_app` as `catalog`)
- Do **not** write `defaults/model_catalog.yaml` (Task 3 owns it). If it does not exist yet when you run tests, the shipped-file test is skipped with `pytest.mark.skipif(not SHIPPED.exists(), ...)`.
- Docs: `hardware.md` "The catalog"; glossary (quantization, mixture of experts (MoE) and active parameters, trained context, KV cache)

**Interfaces (Produces):**
```python
Band = Literal["cpu", "small", "mid", "high", "very_large"]

class CatalogEntry(BaseModel):          # model_config = ConfigDict(extra="forbid")
    id: str                             # short unique id, e.g. "qwen3-14b-q4km"
    family: str                         # "Qwen3"
    pull: str                           # exact `ollama pull` name
    arch: Literal["dense", "moe"]
    params_b: float                     # total parameters, billions
    active_b: float | None = None       # MoE only: parameters used per token, billions
    quant: str                          # "Q4_K_M", "UD-IQ3_S", ...
    download_gb: float                  # GB as Ollama/HF reports (also used as weight memory)
    trained_ctx: int                    # tokens
    kv_mib_per_1k: float                # KV cache memory per 1,000 tokens at f16, MiB
    bands: list[Band]                   # non-empty
    notes: str = ""

class Catalog(BaseModel):
    version: int                        # bumped on every change
    updated: date
    entries: list[CatalogEntry]         # validator: ids unique

CATALOG_URL = "https://raw.githubusercontent.com/jrandall54/research-pipeline/main/src/research_pipeline/defaults/model_catalog.yaml"
FETCHED_NAME = "model_catalog.yaml"     # under VAULT/cache/

def parse_catalog(text: str) -> Catalog                 # raises ValueError with a plain message
def shipped_catalog() -> Catalog
def load_catalog(vault: Path) -> Catalog                # fetched copy if valid and version > shipped, else shipped
def fetch_catalog(url: str = CATALOG_URL, get=httpx.get) -> Catalog   # raises ValueError on HTTP error/invalid
def diff_catalogs(old: Catalog, new: Catalog) -> list[str]  # lines: "+ id (pull, X GB)", "- id", "~ id: field old -> new"
def save_fetched(vault: Path, text: str) -> None        # atomic write (reuse ollama_fit._write_cache pattern: temp + os.replace)
```

- [ ] **Step 1: Write the failing tests** (`tests/test_catalog.py`, fixture with 3 entries, version 1):
  - `test_parse_valid_fixture` (3 entries, first `arch == "dense"`).
  - `test_duplicate_ids_rejected`, `test_unknown_field_rejected`, `test_empty_bands_rejected` → `ValueError`.
  - `test_load_prefers_newer_fetched` (fetched version 999 → used); `test_load_ignores_older_or_invalid_fetched` (version 0, or garbage text → shipped).
  - `test_diff_lists_added_removed_changed`.
  - `test_catalog_update_shows_diff_and_saves_on_yes` (CliRunner, respx mock of `CATALOG_URL` returning a version-999 catalog, `_is_tty` patched True, input "y\n") → file in `vault/cache/model_catalog.yaml`; with input "n\n" → no file.
  - `test_catalog_update_unreachable_saves_nothing` (respx 404 → exit code 1, message names the URL, no file).
  - `test_catalog_update_older_says_up_to_date` (fetched version <= current → "already up to date", no prompt).
  - `test_shipped_catalog_valid` (skipif missing): parses; every band has at least one entry; no owner model name patterns (`re.search(r"^q\d", e.pull)` is false for all entries).
- [ ] **Step 2: Run** — expect failures.
- [ ] **Step 3: Implement** `catalog.py` and the two commands. `catalog show` prints one line per entry grouped by band: `pull — family, dense|MoE (A active of P B), quant, X GB, trained context N`. `catalog update` fetches (progress stage "catalog update"), diffs, asks `Save this catalog? [y/N]`, saves. Without a terminal it prints the diff and exits 1 with "run in a terminal to confirm".
- [ ] **Step 4: Run** full tests + ruff.
- [ ] **Step 5: Docs** as listed.
- [ ] **Step 6: Commit** `feat: versioned model catalog with rpipe model catalog show and update`.

### Task 3: Catalog data — research (runs in parallel with Tasks 1–2; touches only one file)

**Files:**
- Create: `src/research_pipeline/defaults/model_catalog.yaml` (schema of Task 2; `version: 1`, `updated: 2026-11-24`)
- Do not commit; the coordinator commits it with Task 2 or right after.

Research rules:
- 14–22 entries covering every band: `cpu` (small dense ≤ 8B and MoE with ≤ 4B active), `small` (<8 GB VRAM), `mid` (8–16 GB), `high` (16–24 GB), `very_large` (>24 GB). An entry may list several bands.
- Every `pull` must be a name `ollama pull` accepts **today**: an Ollama library tag (check `https://ollama.com/library/<name>/tags`) or `hf.co/<org>/<repo>:<quant>` for a GGUF repo (check the file exists via `https://huggingface.co/api/models/<org>/<repo>/tree/main`). `download_gb` from that page/API (bytes / 1e9, one decimal).
- `trained_ctx`, and the numbers for `kv_mib_per_1k`, from the model's `config.json` on Hugging Face: `kv_mib_per_1k = L_full × 2 × n_kv_heads × head_dim × 2 bytes × 1000 / 2^20`, where `L_full` counts only layers with full attention (skip sliding-window and linear-attention layers; Gemma 4: 25 of 30 sliding-window; Qwen3.6-35B-A3B: 30 of 40 linear). Put the inputs in a YAML comment above each entry, with the source URLs.
- Must include: the two models from the owner's bake-off, `hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-IQ3_S` (notes: "about 2x faster than a 27B dense model in a 10-paper test, but covered fewer key facts and produced unverified citations; partly in RAM on 16 GB") and `hf.co/unsloth/gemma-4-26B-A4B-it-GGUF:UD-IQ4_XS`; the current Qwen3 library tags `qwen3:8b`, `qwen3:14b`, `qwen3:32b`; a public ~27B dense model of the current Qwen generation in a quant that fits 16 GB (IQ3_S-class, ~12 GB). Prefer instruction-tuned, text-capable models with ≥ 32K trained context.
- No owner model names (the owner's own Ollama model tags).
- Validate before finishing: `uv run python -c "from research_pipeline.catalog import parse_catalog; import pathlib; print(len(parse_catalog(pathlib.Path('src/research_pipeline/defaults/model_catalog.yaml').read_text()).entries))"` (if `catalog.py` does not exist yet, validate the YAML with `yaml.safe_load` and the field list above).
- Report: the entry list, anything you could not verify (and left out), and the sources.

### Task 4: Speed benchmark (`bench.py`, `rpipe model bench`)

**Files:**
- Create: `src/research_pipeline/bench.py`, `tests/test_bench.py`
- Modify: `src/research_pipeline/cli.py`
- Docs: `hardware.md` "Measuring speed"; glossary (tokens per second, prompt reading vs writing, benchmark)

**Interfaces (Produces):**
```python
class Bench(BaseModel):
    model: str
    num_ctx: int
    read_tps: float          # prompt tokens read per second
    write_tps: float         # tokens written per second
    seconds_per_paper: float
    paper_tokens: int        # the paper size the estimate used
    measured_at: datetime

BENCH_NAME = "bench.json"            # VAULT/cache/bench.json: {"<model>": Bench-as-dict, ...}
BENCH_DEADLINE = 300.0               # seconds
BENCH_PROMPT_TOKENS = 3000           # roughly; built-in text, no download
BENCH_OUTPUT_TOKENS = 200            # num_predict for the measuring call
PAPER_OUTPUT_TOKENS = 2500           # summary + critique written per paper
DEFAULT_PAPER_TOKENS = 22000         # median of 40 recent papers (spec Evidence)

def bench_prompt(nonce: str) -> str                    # nonce first so Ollama's prefix cache cannot reuse an earlier run
def run_bench(url: str, model: str, num_ctx: int, paper_tokens: int, post=httpx.post, now=datetime.now) -> Bench
def estimate_seconds(read_tps: float, write_tps: float, paper_tokens: int) -> float
    # = paper_tokens / read_tps + PAPER_OUTPUT_TOKENS / write_tps  (the critique reuses the cached paper, B.3)
def median_paper_tokens(conn: sqlite3.Connection) -> int   # median len(text)/4 over stored full texts; DEFAULT_PAPER_TOKENS if fewer than 5
def load_benches(vault: Path) -> dict[str, Bench]      # {} on missing/corrupt file
def save_bench(vault: Path, bench: Bench) -> None      # atomic, replaces that model's entry
```
`run_bench` sends one `POST {url}/api/generate` with `stream: false`, `options: {num_ctx, num_predict: BENCH_OUTPUT_TOKENS, temperature: 0}`, `keep_alive: 0`, timeout `BENCH_DEADLINE`, and reads `prompt_eval_count`, `prompt_eval_duration`, `eval_count`, `eval_duration` (nanoseconds). Missing or zero fields → `LlmError("Ollama did not report timing ...")`. Check the table/column name for stored full texts in `db.py` before writing `median_paper_tokens`.

`rpipe model bench`: backend must be `ollama` (else: "the benchmark measures local models; the cloud backend's speed is the provider's"). Resolves model and context like `rpipe model fit` does (`ollama_fit.resolve`, deadline), states the expected duration first (`about 1-3 minutes on a GPU, up to 5 on a CPU`), runs inside a progress stage, saves, prints `reads N tokens/s, writes M tokens/s; about X s per paper (Y-token paper), about Z min for 15 papers`.

- [ ] **Step 1: Failing tests:** `test_estimate_seconds` (`22000/1000 + 2500/50 == 72.0`); `test_run_bench_reads_ollama_stats` (respx reply `prompt_eval_count=3000, prompt_eval_duration=3e9, eval_count=200, eval_duration=4e9` → read 1000, write 50); `test_run_bench_missing_stats_raises`; `test_prompt_has_nonce_first`; `test_median_paper_tokens_default_when_few`; `test_save_and_load_bench_roundtrip`; `test_load_benches_corrupt_file_is_empty`; `test_bench_command_refuses_cloud_backend`; `test_bench_command_saves` (CliRunner, `run_bench` and `resolve` monkeypatched).
- [ ] **Step 2–4:** run (fail), implement, run full tests + ruff.
- [ ] **Step 5: Docs.**
- [ ] **Step 6: Commit** `feat: rpipe model bench measures read and write speed and estimates time per paper`.

### Task 5: Guided Ollama settings (`ollama_tune.py`, `rpipe model tune [--undo]`)

**Files:**
- Create: `src/research_pipeline/ollama_tune.py`, `tests/test_ollama_tune.py`
- Modify: `src/research_pipeline/cli.py`
- Docs: `hardware.md` "Server settings"; glossary (flash attention, KV cache type / q8_0, parallel slots, sudo)

**Interfaces:**
- Consumes: `OllamaServer` (Task 1), `detect_ollama_server`.
- Produces:
```python
DROPIN = Path("/etc/systemd/system/ollama.service.d/rpipe.conf")

@dataclass(frozen=True)
class Setting:
    name: str            # "OLLAMA_FLASH_ATTENTION"
    value: str           # "1"
    current: str | None  # value now, None = not set
    what: str            # plain words: what it does
    why: str             # plain words: why rpipe wants it (with the measured number for KV cache)

def advise(server: OllamaServer) -> list[Setting]          # only settings whose current value differs
def render_dropin(settings: list[Setting]) -> str           # "[Service]\nEnvironment=\"NAME=value\"\n..." with a header comment naming rpipe and the undo command
def apply_commands(tmp: Path) -> list[list[str]]            # [["sudo","install","-m","644",tmp,DROPIN], ["sudo","systemctl","daemon-reload"], ["sudo","systemctl","restart","ollama"]]
def undo_commands() -> list[list[str]]                      # [["sudo","rm","-f",DROPIN], daemon-reload, restart]
def manual_instructions(server: OllamaServer, settings: list[Setting]) -> str   # per `how`: systemd-user, manual, docker, app, unknown
def wait_ready(url: str, seconds: float = 30.0, version=ollama_version, sleep=time.sleep) -> bool
```
Interactive commands run through an injected `run_tty(argv) -> int` (default: `subprocess.run(argv).returncode`, inheriting the terminal so `sudo` can ask for the password). rpipe never reads or stores the password.

`rpipe model tune` flow: scan server → `advise` → nothing to change: "Ollama already has the recommended settings" (name where each is set, from `drop_ins`) → for `how != "systemd"`: print `manual_instructions`, exit 0 → if `host_matches is False`: warn (Review Focus 1) and `Continue anyway? [y/N]` → show each setting (what, why, current → new), the file content, the exact commands, and the undo (`rpipe model tune --undo`) → `Apply? [y/N]` → write temp file, run commands (stop at the first non-zero exit and say which failed and that nothing else ran) → `wait_ready` → re-scan and confirm every value is live → if backend is ollama: run the fit again (`ollama_fit.resolve(..., force=True)`) and print `context before → after`. `--undo`: if `DROPIN` does not exist, say so and exit 0; else show commands, confirm, run, wait, confirm.

- [ ] **Step 1: Failing tests:** `test_advise_skips_settings_already_set` (env with FA=1, KV=q8_0, NUM_PARALLEL=1 → `[]`); `test_advise_parallel_only_when_above_one` (NUM_PARALLEL=4 → included; absent → not included); `test_render_dropin_quotes_values_and_names_undo`; `test_tune_already_set_writes_nothing` (Review Focus 2; `run_tty` fake records no calls); `test_tune_applies_with_consent` (input "y", fake `run_tty` returns 0 for all, server re-scan returns new env → output "live"; commands recorded equal `apply_commands`); `test_tune_declined_runs_nothing`; `test_tune_stops_at_failed_command` (second command returns 1 → third never runs, message names it); `test_tune_warns_on_host_mismatch` (default answer no → nothing runs); `test_tune_non_systemd_prints_instructions` (how "manual" → contains `OLLAMA_FLASH_ATTENTION=1 ollama serve`); `test_undo_removes_only_rpipe_conf` (commands reference `rpipe.conf` only; with `DROPIN` patched to a tmp path that does not exist → "nothing to undo"); `test_tune_refuses_without_terminal`.
- [ ] **Step 2–4:** run (fail), implement, run full tests + ruff.
- [ ] **Step 5: Docs** (include the owner's measured numbers: 27B context 32,768 → 57,344; 14B 141,312 → 223,232).
- [ ] **Step 6: Commit** `feat: rpipe model tune offers Ollama server settings with consent, verify and undo`.

### Task 6: Recommender (`recommender.py`, `rpipe model recommend`, setup wizard)

**Files:**
- Create: `src/research_pipeline/recommender.py`, `tests/test_recommender.py`
- Modify: `src/research_pipeline/machine.py` (remove `ModelChoice`, `load_model_table`; `recommend()` takes a `Catalog` and returns its pull as a `CatalogEntry`), `src/research_pipeline/cli.py` (`model recommend`; `_wizard_backend`/`_setup_ollama` use the recommender), `tests/test_machine.py`, `tests/test_setup_wizard.py` / `tests/test_init_wizard.py` where they use the old table
- Delete: `src/research_pipeline/defaults/ollama_models.yaml`
- Docs: `hardware.md` "Recommendations"; `setup.md` "How it recommends" (rewrite: catalog, bands, CPU tier now gets a model); glossary (band, spill / offload to RAM)

**Interfaces:**
- Consumes: `MachineInfo` (Task 1), `Catalog`/`CatalogEntry`/`Band` (Task 2), `Bench`/`load_benches` (Task 4), `advise` (Task 5).
- Produces:
```python
Placement = Literal["gpu", "spill", "cpu"]
GPU_OVERHEAD_GB = 1.0        # runtime buffers outside weights and KV cache
RAM_SAFETY_GB = 4.0          # never plan to use the last 4 GB of RAM
MIN_USEFUL_CTX = 16384       # same as ollama_fit.choose's min_useful
CPU_CTX = 32768              # context pinned for CPU and spill choices (median paper ~22K tokens)
CPU_MAX_ACTIVE_B = 15.0      # CPU tier: models above this many active billions are too slow for a night
TOP_N = 5

@dataclass
class Candidate:
    entry: CatalogEntry
    placement: Placement
    est_ctx: int              # estimated context (tokens), multiple of 1024, <= trained_ctx and ollama_fit.CONTEXT_CEILING
    installed: bool
    reasons: list[str]        # plain sentences
    bench: Bench | None       # measured on this machine, if any

def band_for(info: MachineInfo) -> Band
    # total VRAM = sum of gpus (else gpu, else 0): < 4 -> "cpu"; < 8 -> "small"; <= 16.5 -> "mid"; <= 24.5 -> "high"; else "very_large"
def kv_factor(env: dict[str, str]) -> float
    # flash attention on (OLLAMA_FLASH_ATTENTION in {"1","true"}) and KV type q8_0 -> 0.5, q4_0 -> 0.25; otherwise 1.0
def place(entry: CatalogEntry, info: MachineInfo) -> tuple[Placement, int] | None
def rank(info: MachineInfo, catalog: Catalog, benches: dict[str, Bench]) -> list[Candidate]   # at most TOP_N
def uncatalogued(info: MachineInfo, catalog: Catalog) -> list[str]   # installed generation models not in the catalog
```
`place` algorithm (state it in the docstring; this is the part the tests pin):
1. `vram` = total VRAM − `GPU_OVERHEAD_GB`; `kv_gb_per_tok` = `entry.kv_mib_per_1k × kv_factor / 1000 / 1024`.
2. GPU: if `vram > entry.download_gb`, `ctx = floor((vram − download_gb) / kv_gb_per_tok)` rounded down to 1024, capped by `trained_ctx` and `CONTEXT_CEILING`; if `ctx >= MIN_USEFUL_CTX` → `("gpu", ctx)`.
3. Spill (MoE only, GPU present): if `entry.arch == "moe"` and `download_gb + CPU_CTX × kv_gb_per_tok <= vram + (ram_free_gb or 0) − RAM_SAFETY_GB` → `("spill", CPU_CTX)`.
4. CPU (no GPU band, i.e. `band_for == "cpu"`): if `(entry.active_b or entry.params_b) <= CPU_MAX_ACTIVE_B` and `download_gb + CPU_CTX × kv_gb_per_tok <= (ram_total − RAM_SAFETY_GB)` → `("cpu", CPU_CTX)`.
5. Else `None`.

`rank` order: placement gpu, then spill, then cpu; within a placement, entries listing this machine's band first, then larger `params_b` (quality first, spec), then installed first. Reasons include: placement in words (`fits fully in 15.9 GB of GPU memory with about 57K tokens of context (estimate; rpipe model fit measures it)`, `mixture of experts: 3B of 35B parameters work per token, so part of it can sit in RAM at a modest slowdown`, `runs on the CPU: slow but fine overnight`), `installed` / `download X GB`, the catalog `notes`, and the measured time when `bench` exists (else `speed not measured yet: rpipe model bench`).

`rpipe model recommend`: scan (progress stage "scan"), print a 3-line machine summary, then server-setting advice (`advise`; if any: `Run rpipe model tune to apply them`), then the numbered candidates (name, placement, est. context, installed or download size, reasons), then `uncatalogued` (`installed, not in the catalog: NAME (rpipe model fit measures any installed model)`). If a Claude Code login exists, add a line: `cloud: Claude Code is logged in; rpipe model set-backend claude-code uses it`. Then, only on a terminal: `Use one? number, or Enter to keep your current model`. On a pick:
- not installed: show `download X GB; free disk where Ollama keeps models: Y GB` (from `info.disk_free_gb`); if `X + 2 > Y` refuse (`not enough free disk`), else confirm and pull through the existing `pull_model` + `_pull_progress`.
- set the model (pinned name) via `profile_edit`; placement `gpu` → set context `auto` and run the fit (`resolve(force=True)`), print the measured context; `spill`/`cpu` → set context to `CPU_CTX` and say why both are pinned.
- offer `Measure its speed now (about 1-5 minutes)? [y/N]` → Task 4's bench.
- print how to go back: `rpipe model set PREVIOUS` (and `rpipe model set-context PREVIOUS_CTX`).

Setup wizard: `recommend(info, catalog)` keeps its return shape (`Recommendation.pull` becomes `CatalogEntry | None`, chosen as the top `rank` candidate not installed when no generation model is installed). CPU-only: Claude Code logged in → "claude-code" (as today); else, if `rank` has a cpu candidate → "ollama" with that pull and reason "no GPU found; a small model runs on the CPU overnight (slow)"; else "none" as today. When the wizard pulls a cpu/spill candidate it also pins model and context, as `recommend` does.

- [ ] **Step 1: Failing tests** (`tests/test_recommender.py`, a hand-made `Catalog` in the test file: dense 12 GB 27B `kv_mib_per_1k=64`, dense 9 GB 14B, MoE 14 GB 35B active 3B, dense 5 GB 8B, dense 20 GB 32B):
  - `test_band_boundaries` (0 → cpu, 6 → small, 15.92 → mid, 24 → high, two 24 GB cards → very_large).
  - `test_kv_factor` (FA on + q8_0 → 0.5; q8_0 without FA → 1.0).
  - `test_place_gpu_context_estimate`: 15.92 GB card, FA+q8_0, 12 GB model, 64 MiB/1K → `("gpu", ctx)` with `ctx == floor((14.92−12)/(64*0.5/1000/1024)) // 1024 * 1024`; capped at `trained_ctx` when smaller.
  - `test_place_moe_spills_when_ram_allows` (16 GB card, 29 GB total / 20 GB free → "spill"); `test_dense_never_spills`.
  - `test_cpu_only_ranks_small_models_by_ram` (no GPU, 16 GB RAM → only entries ≤ CPU_MAX_ACTIVE_B whose size fits; 32B absent; MoE 3B-active present if it fits); `test_cpu_only_tiny_ram_gets_nothing` (4 GB RAM → `[]`) (Review Focus 5).
  - `test_rank_orders_gpu_before_spill_and_bigger_first`; `test_rank_marks_installed_and_attaches_bench`.
  - `test_uncatalogued_lists_other_installed_models` (embedding models excluded).
  - `test_recommend_command_lists_and_keeps_current_on_enter` (CliRunner; `detect_machine`, `load_catalog` patched; input "\n" → config unchanged).
  - `test_recommend_pick_installed_gpu_model_pins_and_fits` (input "1\nn\n"; `resolve` patched → `Fit`; config `llm_model` == pick, `llm_num_ctx == "auto"`).
  - `test_recommend_pick_cpu_model_pins_context` (`llm_num_ctx == 32768`).
  - `test_recommend_refuses_download_without_disk` (download 12 GB, free 10 GB → "not enough free disk", `pull_model` not called) (Review Focus 5).
  - `test_recommend_non_tty_prints_only`.
  - Wizard: `test_wizard_cpu_only_without_cloud_recommends_cpu_model` (replaces the old "none" expectation when RAM allows) and keep `none` when RAM is tiny.
- [ ] **Step 2–4:** run (fail), implement, run full tests + ruff. Delete `ollama_models.yaml` and fix every reference (`grep -rn "ollama_models\|load_model_table\|ModelChoice" src tests docs`).
- [ ] **Step 5: Docs** (`hardware.md`, `setup.md`, glossary; search `docs/guide` for "qwen3:8b" / "6 GB" statements about the old table and fix them).
- [ ] **Step 6: Commit** `feat: rpipe model recommend ranks catalog models for this machine, installed or not`.

### Task 7: Fit cache re-checks when the machine changes

**Files:**
- Modify: `src/research_pipeline/ollama_fit.py` (`_cache_key`, `resolve`, `cached_fit`), `src/research_pipeline/scan.py` (add `machine_key`)
- Test: `tests/test_ollama_fit.py`
- Docs: `llm.md` (the section that says what the fit cache is keyed on), `hardware.md` "When the machine changes"

**Interfaces:**
```python
# scan.py
def machine_key(url: str, run: CmdRunner = system_cmd) -> dict
    # {"ollama": version or None, "env": {k: v for the OLLAMA_FLASH_ATTENTION, OLLAMA_KV_CACHE_TYPE, OLLAMA_NUM_PARALLEL keys present},
    #  "gpus": sorted "name:vram_gb rounded to 0.1"}  — never raises
# ollama_fit.py
def _cache_key(url, model, num_ctx, models, machine: dict | None = None) -> str   # machine added under "machine"
def resolve(..., machine: Callable[[str], dict] = machine_key) -> Fit
def cached_fit(..., machine: Callable[[str], dict] = machine_key) -> Fit | None
```
- [ ] **Step 1: Failing tests:** `test_changed_server_settings_reprobe` (same models, machine dict differs in `env` → cached entry ignored, probes run); `test_same_machine_uses_cache`; `test_new_gpu_reprobe`; existing tests pass a constant `machine=lambda url: {}` where they build keys by hand.
- [ ] **Step 2–4:** run (fail), implement, run full tests + ruff.
- [ ] **Step 5: Docs** — say that after this change the first nightly run probes once again (about 1-3 minutes) because the key changed.
- [ ] **Step 6: Commit** `feat: the fit cache re-checks after an Ollama upgrade, a server setting change or a new GPU`.

### Task 8: Real-machine check, docs pass, handoff (coordinator)

- [ ] `uv run pytest -q` and `uv run ruff check .` — all pass; note the count.
- [ ] Reinstall the tool: `uv tool install --editable '.[embed,serve]' --force`.
- [ ] On the owner's machine (read-only for config): `rpipe model scan`, `rpipe model catalog show`, `rpipe model recommend < /dev/null` (prints only). Check: the host-port note appears (service on 11436, rpipe on 11434); flash attention/KV shown as already set; the 27B-class catalog entry estimates ~55-58K context. Run `rpipe model bench --vault COPY` against a throwaway copy of the vault (copy `config.yaml`, `profile.yaml`, `cache/`) so the owner's cache is untouched; record the numbers. Do **not** run `rpipe model tune` (it would restart the owner's Ollama).
- [ ] Read `docs/guide/hardware.md` top to bottom as a newcomer; fix gaps. Every new term is in the glossary.
- [ ] Update `docs/superpowers/handoff.md` (B.4 state, real-machine numbers, what's next: B.5) and commit.
