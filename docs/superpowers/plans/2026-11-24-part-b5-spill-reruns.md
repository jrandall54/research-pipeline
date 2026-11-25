# Part B.5: Proxy Detection, B.4 Fixes, and RAM-Spill Reruns Within a Night Deadline (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A paper whose body had to be cut to fit the GPU is read again whole the same night, with part of the model in RAM, as long as RAM and a morning deadline allow; plus the B.4 fixes parked in the handoff (proxy seen as the same server, `[N/A]` GPU memory, CPU fallback on small cards, the 600 s call limit on slow machines).

**Architecture:** The normal summarize pass is unchanged except that every summary records the context it *needed* (`needed_ctx`) and the share of the model on the GPU (`gpu_share`). A new stage, `spill.py`, runs after it in `rpipe run`: it finds stored summaries with `body_cut` whose `needed_ctx` is larger than the context they ran with, groups them into context buckets, measures how much memory one more token of context costs on this machine, and reruns each bucket (one model load per bucket, smallest first) while free RAM and the deadline (`night_deadline`, default `07:00`, set with `rpipe schedule set-deadline`) allow. Papers left over keep their cut summary and are found again on a later night, because the queue is recomputed from stored summaries (no new table).

**Tech Stack:** Python 3.12, typer, pydantic v2, httpx + respx (tests), pytest. `uv run pytest -q`, `uv run ruff check .`.

**Spec:** `docs/superpowers/specs/2026-11-22-part-b-hardware-adaptive-design.md`, section "5. Using the whole machine overnight", "Settled" items 5 and 10, plus the "B.4 built" section of `docs/superpowers/handoff.md` (proxy item and "Parked for B.5"). Read both.

## Global Constraints

- Tests run offline: Ollama calls through `respx`, commands through injected runners, clocks and RAM readers injected. No test talks to a real Ollama or runs `nvidia-smi`.
- Every waiting step reports through `progress.py` (`stage(reporter, ...)`, `handle.note`, `handle.step`).
- Every change updates `docs/guide/` in the same commit; new terms go in `docs/guide/glossary.md` in plain words. When behaviour changes, fix pages describing the old behaviour.
- Every user setting has an `rpipe` command with validation (`profile_edit` setter + CLI); no workflow requires hand-editing a file.
- No owner model names in the repo (`q3.8-27b-34k`, `q3f-14b-143k`). No owner machine details beyond what `hardware.md` already says.
- Never suggest `rpipe model set-url` to the service's own port when a proxy is in front of it (owner keeps the proxy).
- Normal papers always run as today; the deadline governs reruns only (spec, Settled 5). Never plan to use the last `RAM_SAFETY_GB` (4.0) of free RAM (never swap).
- Reruns only for the local Ollama backend (`llm.name == "ollama"`); a cloud backend gets no reruns (spec: "no spill").
- Commit per task; `git add` named files only (never `-A`: `.idea/` is untracked). Trailers: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01STEoRgM8cJycrqyDcfMV3x`.

## Decisions this plan makes (spec "left to the builder")

| Item | Decision |
|---|---|
| Deadline setting | `Config.night_deadline: str = "07:00"` (`HH:MM` 24-hour, or `off` = no reruns). Command `rpipe schedule set-deadline HH:MM|off`; shown by `rpipe schedule show`. |
| Which deadline applies | The next time the clock reads `HH:MM` after the rerun stage starts, **only if that is at most 12 h away** (`MAX_NIGHT_HOURS`). A 03:00 or 23:00 run gets 07:00; a manual run at 10:00 gets no reruns ("reruns wait for the night"). |
| Needed context | `needed_ctx = ceil(chars of core text, appendices excluded / CHARS_PER_TOKEN) + RESERVED_TOKENS`, rounded up to 1024. Set only when `body_cut`. Appendices are dropped before a paper is queued (spec, Settled 10). |
| Grouping | Rerun context = `needed_ctx` rounded up to a multiple of `RERUN_STEP = 8192`, capped at `min(trained context, CONTEXT_CEILING)`. One load per bucket, buckets smallest first, papers within a bucket by `needed_ctx`. |
| Candidate | Stored summary has `body_cut` and `needed_ctx`, provenance `num_ctx` < `needed_ctx`, and provenance `num_ctx` < the model's cap (a paper already rerun at the cap is not retried forever; B.6 map-reduce will handle it). Any digest date, newest digest first within a bucket's tie. |
| Memory estimate | Measured, not catalogued: `/api/ps` size at the normal context (`S_fit`, `V_fit` = size_vram) and at 8192 tokens (`S_8k`, one extra load). Bytes per token `k = (S_fit − S_8k) / (num_ctx − 8192)`. RAM part at context `c` = `S_fit + (c − num_ctx)·k − V_fit`. Allowed if ≤ `(MemAvailable − 4.0 GiB)`. After loading a bucket, `/api/ps` is read again; if the real RAM part exceeds the allowance, unload and stop reruns. |
| Time estimate | Seconds per token of paper read (paper tokens = `needed_ctx − RESERVED_TOKENS`), measured from the last finished rerun this night. Before any: from `rpipe model bench` (`seconds_per_paper / paper_tokens × SPILL_GUESS`, `SPILL_GUESS = 3.0`); no bench: the first rerun starts only if at least `FIRST_RERUN_MARGIN = 3600` s remain. A rerun starts only if `now + estimate ≤ deadline`. |
| Per-call timeout | Reruns: seconds left until the deadline (at least 60). Normal pass (lifts the 600 s limit for slow machines): `max(CALL_TIMEOUT, 1.5 × (num_ctx / read_tps + NUM_PREDICT / write_tps))` when a bench exists for the model, else `CALL_TIMEOUT`. |
| GPU share | `OllamaClient.gpu_share() -> float | None` (`size_vram / size` from `/api/ps`, 2 decimals). Stored in summary provenance as `gpu_share` for every Ollama summary. |
| Health line | ` · rerun N` (papers read whole on a rerun) and ` · rerun waiting N` (candidates left for a later night). |
| Rerun failure | Keep the old summary; no `retry_count` change; error line `rerun ID: ...`; an `LlmTimeout` stops the stage. |
| Proxy | Service port ≠ `llm_url` port, but `/api/tags` on both returns the same non-empty set of (name, digest) → one server: `host_matches = True`, new field `OllamaServer.proxied = True`. |
| CPU fallback | CPU placement is allowed for every machine on which no catalog entry gets a `gpu` placement (not only the `cpu`/`small` bands). |

## Review Focus

1. **Another model (or the user's game) holds GPU memory when reruns start, or the normal model was already unloaded.** Expected: `/api/ps` lists a model other than ours → reruns skipped with one note ("GPU busy"), counted as waiting. Test in Task 5 (`test_reruns_skip_when_other_model_loaded`).
2. **A manual daytime run, or a deadline already passed.** Expected: no reruns, a note, waiting count set. Test in Task 4 (`test_deadline_none_when_more_than_12h_away`) and Task 5 (`test_no_reruns_after_deadline`).
3. **Free RAM cannot be read (macOS, Windows) or is too small.** Expected: no rerun loads anything; one note; waiting count. Test in Task 5 (`test_reruns_skip_without_ram_reading`).
4. **The memory estimate is wrong** (real RAM part after loading is larger). Expected: unload, stop, note. Test in Task 5 (`test_reruns_stop_when_load_exceeds_ram`).
5. **A paper whose stored full text is gone, or old summaries without `needed_ctx` / provenance.** Expected: not candidates; nothing crashes. Test in Task 5 (`test_candidates_ignore_old_rows`).

---

### Task 1: A pass-through proxy counts as the same server

**Files:**
- Modify: `src/research_pipeline/scan.py` (`OllamaServer.proxied`, `ollama_tags`, `service_url` moved here, proxy check in `detect_ollama_server`)
- Modify: `src/research_pipeline/ollama_tune.py` (re-export or import `service_url` from scan; behaviour unchanged)
- Modify: `src/research_pipeline/cli.py` (`model scan` prints the proxy line instead of the mismatch note)
- Test: `tests/test_scan.py`
- Docs: `docs/guide/hardware.md` (plain-words section "When rpipe talks to Ollama through a proxy"), `docs/guide/glossary.md` ("pass-through proxy")

**Interfaces (Produces):**
```python
@dataclass
class OllamaServer:
    ...                      # existing fields unchanged
    proxied: bool = False    # True when the ports differ but both addresses answer as one server

def ollama_tags(url: str) -> list[tuple[str, str]] | None   # sorted (name, digest) from GET /api/tags; None on any failure
def service_url(server: OllamaServer, rpipe_url: str) -> str  # moved from ollama_tune unchanged, except it reads the service address whenever ports differ (use a private helper `_own_url(env)`)
def detect_ollama_server(url, run, version=ollama_version, tags=ollama_tags) -> OllamaServer
```

- [ ] **Step 1: Write the failing tests** in `tests/test_scan.py` (systemd runner fake as in existing tests, service `OLLAMA_HOST=127.0.0.1:11436`, config URL `http://localhost:11434`; respx mocks both `/api/tags`):
  - `test_proxy_with_same_models_is_same_server`: both return `[{"name": "m:latest", "digest": "abc"}]` → `host_matches is True`, `proxied is True`.
  - `test_proxy_with_different_models_is_mismatch`: digests differ → `host_matches is False`, `proxied is False`.
  - `test_proxy_unreadable_is_mismatch`: service address returns 500 → `host_matches is False`.
  - `test_proxy_both_empty_is_mismatch`: both `{"models": []}` → `host_matches is False` (nothing to compare).
  - `test_same_port_does_not_call_tags`: ports equal → `host_matches is True`, `proxied is False`, no `/api/tags` route called.
- [ ] **Step 2: Run** `uv run pytest tests/test_scan.py -q` → the new tests FAIL.
- [ ] **Step 3: Implement.** In `detect_ollama_server`, only when the ports differ: compare `tags(url)` with `tags(_own_url(env))`; equal and non-empty → `host_matches=True, proxied=True`. `machine_key` passes through unchanged (its key then uses the service's settings, which is correct). `rpipe model scan` prints `rpipe reaches this Ollama through a pass-through proxy on port P (same models): its settings apply.` in place of the mismatch note.
- [ ] **Step 4: Run** `uv run pytest -q` → all pass; `uv run ruff check .` clean.
- [ ] **Step 5: Commit** `feat: recognise a pass-through proxy in front of the Ollama service`.

### Task 2: B.4 parked fixes in scan and recommender

**Files:**
- Modify: `src/research_pipeline/scan.py` (`_nvidia_all`)
- Modify: `src/research_pipeline/recommender.py` (`place`, `rank`, `_placement_reason`)
- Test: `tests/test_scan.py`, `tests/test_recommender.py`
- Docs: `docs/guide/hardware.md` (CPU fallback rule; fix the worked example to 0.245 GiB; the placement reason wording)

**Interfaces (Produces):** `place(entry, info, allow_cpu: bool | None = None) -> tuple[Placement, int] | None` — `None` keeps today's band rule; `rank` passes `True` when no entry got a `gpu` placement.

- [ ] **Step 1: Failing tests.**
  - `test_nvidia_na_free_memory_is_unknown`: runner returns `"NVIDIA GeForce RTX 4060, 8188, [N/A]"` → one `Gpu` with `vram_gb ≈ 8.0`, `free_gb is None`. Also `"N/A"` without brackets.
  - `test_8gb_card_with_little_free_gets_cpu_models` (uses the **shipped** catalog via `catalog.load_shipped()` or the existing loader name): one 8 GiB GPU with `free_gb=5.0`, `ram_gb=32`, `ram_free_gb=24` → `rank` returns at least one candidate and all have placement `cpu` or `spill`; none `gpu`.
  - `test_mid_band_with_gpu_fits_gets_no_cpu_models`: a 16 GiB card, 15 GiB free → no `cpu` placements in `rank(..., top=None)`.
  - `test_placement_reason_names_usable_memory`: the GPU reason contains the usable figure (`usable_vram`, 1 decimal), not total VRAM.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `_nvidia_all`: treat a trailing `[N/A]`/`N/A` as an unknown number (keep it as `None`, keep parsing). `rank`: first pass with `allow_cpu=None`; if no `gpu` placement exists, place again with `allow_cpu=True`. `_placement_reason`: "fits fully in the X GB of GPU memory free for models".
- [ ] **Step 4: Run** `uv run pytest -q` → pass; ruff clean.
- [ ] **Step 5: Commit** `fix: read [N/A] GPU memory as unknown; CPU fallback when nothing fits the GPU`.

### Task 3: Summaries record the context they needed and the GPU share

**Files:**
- Modify: `src/research_pipeline/pruning.py` (`Fit.needed_ctx`)
- Modify: `src/research_pipeline/summary_models.py` (`PaperSummary.needed_ctx: int | None = None`)
- Modify: `src/research_pipeline/summarize.py` (`summarize_paper` passes it; `run_summarize` stores `gpu_share`)
- Modify: `src/research_pipeline/llm.py` (`OllamaClient.gpu_share`)
- Modify: `src/research_pipeline/db.py` (`save_summary(..., gpu_share: float | None = None)` into provenance; `get_summary` reads `needed_ctx`)
- Test: `tests/test_pruning.py`, `tests/test_summarize.py`, `tests/test_llm.py`, `tests/test_db.py`
- Docs: `docs/guide/summaries.md` (context budget: what `needed_ctx` is, worked example), `docs/guide/data-model.md` (new JSON fields)

**Interfaces (Produces):**
```python
# pruning.py
NEEDED_STEP = 1024
@dataclass(frozen=True)
class Fit: sections; dropped; body_cut; needed_ctx: int | None = None   # set only when body_cut
# llm.py
class OllamaClient:
    def gpu_share(self) -> float | None   # GET /api/ps, this model's size_vram/size rounded to 2 places; None if not listed or on error
# db.py
def save_summary(conn, paper_id, result, model, *, prompt_version=None, paper_version=None,
                 num_ctx=None, backend=None, gpu_share: float | None = None) -> None
```

- [ ] **Step 1: Failing tests.**
  - `test_needed_ctx_set_only_when_body_cut`: core text of 300,000 chars, one appendix of 90,000 chars, `num_ctx=32768` → `body_cut`, `needed_ctx` computed in the test as `-(-(-(-core_chars // 3) + 4000) // 1024) * 1024` where `core_chars` = `_size` of the core sections (titles included). A paper that fits → `needed_ctx is None`. Appendix-only overflow (fits once appendices drop) → `None`.
  - `test_summary_round_trips_needed_ctx` (db): save a `PaperSummary(needed_ctx=65536, ...)` and `get_summary` returns it; an old row without the key reads `None`.
  - `test_provenance_has_gpu_share` (db): `save_summary(..., gpu_share=0.93)` → `get_provenance(...)["gpu_share"] == 0.93`.
  - `test_gpu_share_from_ps` (llm, respx): `/api/ps` lists `{"name": "m:latest", "size": 1000, "size_vram": 930}` → `0.93`; other model only → `None`; HTTP 500 → `None`.
  - `test_run_summarize_stores_gpu_share`: a fake LLM with `gpu_share()` returning `1.0` → provenance has `1.0`; a fake LLM without the method → key present with `None`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `needed_ctx` from `_size(kept)` (core text, titles included, appendices and non-paper text excluded). `run_summarize` calls `getattr(llm, "gpu_share", None)` after `summarize_paper` succeeds, never letting it raise.
- [ ] **Step 4: Run** `uv run pytest -q` → pass.
- [ ] **Step 5: Commit** `feat: summaries record the context they needed and the share of the model on the GPU`.

### Task 4: Night deadline setting

**Files:**
- Modify: `src/research_pipeline/models.py` (`Config.night_deadline`)
- Modify: `src/research_pipeline/profile_edit.py` (`set_night_deadline`)
- Modify: `src/research_pipeline/schedule.py` (`deadline_at`)
- Modify: `src/research_pipeline/cli.py` (`schedule set-deadline`, `schedule show` prints it)
- Modify: `src/research_pipeline/defaults/config.yaml` (commented `night_deadline: "07:00"` line, if the template lists settings)
- Test: `tests/test_schedule.py`, `tests/test_profile_edit.py`, `tests/test_cli_schedule.py` (or the existing CLI test file for schedule)
- Docs: `docs/guide/scheduling.md` ("The morning deadline": what it governs — reruns only), `docs/guide/vault-and-config.md` (setting table)

**Interfaces (Produces):**
```python
# models.py
night_deadline: str = Field(default="07:00", pattern=r"^(off|([01]\d|2[0-3]):[0-5]\d)$")
# profile_edit.py
def set_night_deadline(vault: Path, value: str) -> None   # accepts "7:00", "07:00", "off" (any case); stores "07:00"/"off"; ValueError with an example otherwise
# schedule.py
MAX_NIGHT_HOURS = 12
def deadline_at(now: datetime, value: str) -> datetime | None   # next HH:MM after `now` (local, naive), None if "off" or more than 12 h away
```

- [ ] **Step 1: Failing tests.**
  - `test_deadline_same_night`: `now=03:00`, `"07:00"` → today 07:00. `test_deadline_after_midnight`: `now=23:00`, `"07:00"` → tomorrow 07:00. `test_deadline_none_when_more_than_12h_away`: `now=10:00` → `None`. `test_deadline_off` → `None`. `now` exactly 07:00 → tomorrow 07:00 → 24 h → `None`.
  - `test_set_night_deadline_normalises`: `"7:00"` → stored `"07:00"`; `"OFF"` → `"off"`; `"25:00"` and `"7pm"` raise `ValueError` mentioning `HH:MM`.
  - CLI: `rpipe schedule set-deadline 06:30` writes the config and prints `Reruns of cut papers must finish by 06:30.`; a bad value exits 1 with the error; `rpipe schedule show` includes `deadline 06:30`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** with the existing `_edit(_config_path(vault), Config, change)` pattern.
- [ ] **Step 4: Run** `uv run pytest -q` → pass.
- [ ] **Step 5: Commit** `feat: night deadline setting (rpipe schedule set-deadline)`.

### Task 5: Spill reruns stage

**Files:**
- Create: `src/research_pipeline/spill.py`
- Modify: `src/research_pipeline/cli.py` (`_run_locked`: after `run_summarize`, before the digest; `health_line` adds `rerun` / `rerun waiting`)
- Modify: `src/research_pipeline/db.py` (`rerun_candidates` query helper, if it reads better there than in `spill.py`)
- Test: `tests/test_spill.py`, `tests/test_cli_run.py` (or the existing run/health-line tests)
- Docs: `docs/guide/running.md` (new section "Reading cut papers whole: reruns", step by step with a worked example: 3 cut papers needing 61,440, 63,488 and 90,112 tokens at a fitted 57,344 → buckets 65,536 (two papers, one load) and 90,112; memory arithmetic; deadline arithmetic), `docs/guide/llm.md` (fit gate stays 100% GPU for the normal pass; reruns are the only spill), `docs/guide/glossary.md` (fix "Spill": reruns spill dense models too, for a few papers, because the slowdown is acceptable overnight; add "rerun", "bucket")

**Interfaces:**
- Consumes: `PaperSummary.needed_ctx`, provenance `num_ctx` (Task 3); `deadline_at`, `Config.night_deadline` (Task 4); `ollama_fit.list_models`, `ollama_fit.CONTEXT_CEILING`, `ollama_fit._unload` (or a public `unload`), `scan.ram_free_gb`, `bench.load_benches`, `summarize.summarize_paper`, `db.get_fulltext`, `db.save_summary`, `db.get_summary`, `db.get_provenance`.
- Produces:
```python
RERUN_STEP = 8192
SPILL_GUESS = 3.0
FIRST_RERUN_MARGIN = 3600.0
MIN_CALL_TIMEOUT = 60.0

@dataclass(frozen=True)
class Rerun:
    paper_id: str
    needed_ctx: int
    ran_ctx: int

def rerun_candidates(conn, model_cap: int) -> list[Rerun]          # see Decisions: Candidate
def buckets(reruns: list[Rerun], model_cap: int) -> list[tuple[int, list[Rerun]]]
def ram_part_bytes(ctx: int, num_ctx: int, s_fit: int, v_fit: int, s_8k: int) -> int
def run_reruns(conn, llm: OllamaClient, vault: Path, deadline: datetime | None, *,
               reporter: Reporter | None = None,
               now: Callable[[], datetime] = datetime.now,
               ram_free: Callable[[], float | None] = lambda: ram_free_gb(platform.system()),
               ) -> tuple[dict[str, int], list[str]]   # counts {"rerun": n, "rerun_waiting": m}
```

`run_reruns` order: candidates → (none: return zeros) → deadline None: all waiting + note → `/api/ps` must list only our model (else "GPU busy" note, all waiting) → read `S_fit`, `V_fit` (load at `llm.num_ctx` if not loaded) → load at 8192, read `S_8k` → for each bucket: check RAM allowance (Decisions: Memory estimate) and time (Decisions: Time estimate) for its first paper → load at the bucket context, verify RAM part from `/api/ps` → rerun papers one by one with `OllamaClient(llm.url, llm.model_name, ctx, timeout=max(MIN_CALL_TIMEOUT, seconds left))`, each re-checking time before it starts; save with `num_ctx=ctx`, `gpu_share=client.gpu_share()` → unload at the end. Progress: one `stage(reporter, "rerun", total=len(candidates))`, notes for each load/measure, one `step` per paper (`done` / `waiting` / `failed`).

- [ ] **Step 1: Failing tests** in `tests/test_spill.py` (in-memory vault DB fixtures as in `test_summarize.py`; respx for `/api/generate`, `/api/ps`, `/api/show`; fake clock; `ram_free` lambda; a fake summarize via monkeypatching `spill.summarize_paper` to return a `PaperSummary` with `body_cut=False`):
  - `test_candidates_and_buckets`: summaries with (needed, ran) = (61440, 57344), (66560, 57344), (90112, 57344), (40000 not cut), cap 131072 → 3 candidates; buckets `[(65536, [61440]), (73728, [66560]), (90112, [90112])]` (rule: `ceil(needed / 8192) * 8192`, capped at the model cap). A row with `ran_ctx == cap` is not a candidate.
  - `test_candidates_ignore_old_rows`: summary rows without `needed_ctx` or without provenance → none.
  - `test_ram_part_bytes`: `s_fit=14e9, v_fit=14e9, s_8k=12e9, num_ctx=57344` → at `65536` equals `(65536−57344)·(2e9/49152)` rounded to int.
  - `test_reruns_happy_path`: 2 candidates in one bucket, RAM ample, deadline in 3 h, bench present → both saved with provenance `num_ctx == 65536`, `body_cut False`; counts `{"rerun": 2, "rerun_waiting": 0}`; one load at 65536 (count `/api/generate` calls with that `num_ctx` = 1).
  - `test_no_reruns_after_deadline`: deadline `None` → no Ollama call, `rerun_waiting == 2`, one note.
  - `test_deadline_stops_midway`: fake clock advances 50 min per rerun, deadline 70 min away, no bench → first runs (margin 60 min met), second waits; counts `{1, 1}`.
  - `test_reruns_skip_without_ram_reading`: `ram_free=lambda: None` → nothing loaded, all waiting.
  - `test_reruns_stop_when_load_exceeds_ram`: `/api/ps` after the bucket load reports `size − size_vram` above the allowance → unload called (`keep_alive: 0`), papers waiting, note.
  - `test_reruns_skip_when_other_model_loaded`: `/api/ps` lists another model → all waiting, note "GPU busy".
  - `test_rerun_failure_keeps_old_summary`: summarize raises `LlmError` → old summary unchanged, `retry_count` unchanged, error line `rerun ID: ...`; an `LlmTimeout` stops the remaining reruns.
  - `test_cloud_backend_gets_no_reruns` (cli level): `llm.name == "claude-code"` → `run_reruns` not called.
  - `test_health_line_shows_reruns`: counts `{"rerun": 2, "rerun_waiting": 1}` → line contains ` · rerun 2 · rerun waiting 1`.
- [ ] **Step 2: Run** `uv run pytest tests/test_spill.py -q` → FAIL.
- [ ] **Step 3: Implement `spill.py`** and wire it into `_run_locked` (inside the summarize stage's `else`, only when `getattr(llm, "name", "") == "ollama"`; exceptions from `run_reruns` become an error line `rerun: ...`, never stop the digest). Add `rerun` to the stage names the CLI renders if `progress_render` keeps a list.
- [ ] **Step 4: Run** `uv run pytest -q` → pass; ruff clean.
- [ ] **Step 5: Commit** `feat: rerun cut papers whole with part of the model in RAM, within the night deadline`.

### Task 6: Call timeout scales with a measured slow machine

**Files:**
- Modify: `src/research_pipeline/registry.py` (`make_ollama`)
- Modify: `src/research_pipeline/llm.py` (`call_timeout` helper next to `CALL_TIMEOUT`)
- Modify: `src/research_pipeline/recommender.py` / `cli.py` only to remove or reword the "600 s limit" warning that `recommend` prints (it no longer applies when a bench exists)
- Test: `tests/test_llm.py`, `tests/test_registry.py`
- Docs: `docs/guide/llm.md` (call limit section: worked example — CPU 80 tok/s read, 18 tok/s write, 32,768 context → 1.5 × (410 + 228) ≈ 957 s), `docs/guide/hardware.md` (drop or reword the 600 s warning text)

**Interfaces (Produces):** `def call_timeout(num_ctx: int, bench: Bench | None) -> float` in `llm.py` (import `Bench` under `TYPE_CHECKING` to avoid a cycle) → `max(CALL_TIMEOUT, 1.5 * (num_ctx / bench.read_tps + NUM_PREDICT / bench.write_tps))`, `CALL_TIMEOUT` when `bench is None` or the bench's model differs.

- [ ] **Step 1: Failing tests.** `test_call_timeout_cpu_bench`: read 80, write 18, ctx 32768 → `≈ 1.5*(409.6+227.6)` (`pytest.approx`, rel 1e-3). `test_call_timeout_fast_gpu_keeps_600`: read 3000, write 40 → `600.0`. `test_call_timeout_no_bench` → `600.0`. `test_make_ollama_uses_bench` (registry, with `resolve` monkeypatched and a bench saved in the tmp vault for the fitted model) → `client.timeout` equals `call_timeout(...)`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; `make_ollama` reads `bench.load_benches(vault).get(fit.model)` (normalise `:latest` the way `recommender._norm` does).
- [ ] **Step 4: Run** `uv run pytest -q` → pass.
- [ ] **Step 5: Commit** `feat: per-call time limit grows with the measured speed of a slow machine`.

### Task 7: Handoff and master plan

**Files:** `docs/superpowers/handoff.md` (B.5 section: what was built, owner run-through steps, what was not run on the real machine), Claude Docs master plan status line (via the docs tools, by the coordinating session, not a subagent).

- [ ] **Step 1:** Write the B.5 section; list `rpipe schedule set-deadline`, the new health-line fields, and the first real-night check: `rpipe runs` shows `rerun N` and summaries' provenance `num_ctx` / `gpu_share` (`rpipe show ID`).
- [ ] **Step 2: Commit** `docs: handoff after B.5`.

## Parallelism and file ownership

Tasks 1, 2, 4 and 6 touch different files except `cli.py` and docs; run them one after another (cheap), or 1+4 in parallel. Task 3 before Task 5 (Task 5 consumes its fields). Task 4 before Task 5.
