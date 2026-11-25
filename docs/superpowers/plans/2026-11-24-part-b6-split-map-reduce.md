# Part B.6: Task Splitting and Map-Reduce Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the summarizer read a paper in parts — a fixed two-part split (front matter / body) on slow machines, and size-based map-reduce for papers that would otherwise be cut and can never be rerun whole — with the mode chosen per machine (`auto`), settable with an `rpipe` command, and measured with `rpipe eval`.

**Architecture:** A new module `parts.py` takes notes on groups of sections (one model call per group, claims cite real section titles) and combines the notes into the existing `Summary` and `Critique` with two more calls that share a prefix (cache-friendly). `summarize_paper` gains a `mode` (`whole` | `split`) and a `fallback_above` threshold that sends a cut paper to map-reduce. The setting `summarize_mode` (`auto` | `whole` | `split`) is resolved once per run from the backend, where the loaded model sits (GPU share) and the context size.

**Tech Stack:** Python 3, pydantic, typer, httpx/respx, pytest (offline: fake LLM).

**Spec:** `docs/superpowers/specs/2026-11-22-part-b-hardware-adaptive-design.md`, section 6, approach C (C3), settled rulings 3, 9 and 10.

## Global Constraints

- Docs in the same commit as code: `docs/guide/summaries.md` (main page for this part), `glossary.md`, plus the pages named per task. Page shape: What → How (worked example) → Why (decisions + rejected alternatives) → How to run/check → Where the code is.
- Every user setting has an `rpipe` command: `summarize_mode` gets `rpipe model set-mode auto|whole|split`.
- "Citation checking keeps working: every claim still cites real section titles." Notes and combined claims are checked against the titles of the sections actually read, with the existing one-retry rule.
- Pruning means non-paper text only, never core text (owner ruling). Appendices: kept when they fit, dropped first when space is short (ruling 10).
- Paper text is untrusted data: notes are model output derived from it, so they are treated as untrusted too (inside a tag block, defused).
- `PROMPT_VERSION` becomes `"2026-11-p7"` (ruling 9: a part that adds prompt text bumps it). The whole-paper prompt texts do not change.
- Every waiting step reports through `progress.py` (`on_phase` notes; the mode decision runs inside a stage).
- Tests run offline with a fake LLM; `uv run pytest -q` and `uv run ruff check .` clean at every commit.
- Keep the owner's model names out of the repo.

## Decisions (my calls; the spec leaves them to the builder)

1. **Two modes, one fallback.** `whole` (today) and `split` are the user-facing modes. Map-reduce is not a mode: it is what any mode does when a part does not fit the context. This keeps C3's order: GPU-fitting context for all, spill reruns for papers a rerun can fix, map-reduce only as last resort.
2. **When whole mode falls back to map-reduce.** A whole-mode paper that comes out `body_cut` is read in parts when its `needed_ctx` is above `fallback_above`. `run_summarize` passes the model's cap when reruns can happen (Ollama and a night deadline set) — so only papers no rerun could ever read whole are split — and `0` otherwise (no reruns: every cut paper goes to parts). Spill reruns pass `None` (never fall back; they exist to read whole). Eval passes `0`.
3. **Split groups.** *Front*: sections whose heading words (numbering stripped, as `pruning._words`) start with `abstract`, `introduction`, `conclusion`, `discussion`, `limitation`, `future work`, `summary`. *Body*: every other paper section, plus appendices if they fit. If either group is empty, the paper is chunked by size instead (headings did not say enough).
4. **Calls in split mode:** notes(front), notes(body …one per chunk if the body does not fit), summary-from-notes, critique-from-notes. Front notes focus on core claim and what is new; body notes on assumptions, baselines, results and missing ablations; size chunks get the general focus.
5. **Combining notes that do not fit.** If all notes together exceed the budget, notes are condensed in rounds (chunk the notes, one condense call per chunk, citations kept). After 3 rounds the notes are trimmed uniformly and the result is marked `body_cut`.
6. **`auto`.** Backend not `ollama` → `whole`. Ollama: load the model, read `gpu_share()`; unknown → `whole` (today's behaviour); share below 1.0 (any of the model in RAM, CPU-only = 0.0) → `split`; context below `SPLIT_BELOW_CTX = 26_624` (median paper ~22K tokens + the 4,000 reserve, rounded up to 1,024; measured 2026-11-22) → `split`; else `whole`. Task 6 measures and may change these shipped defaults.
7. **Recorded:** `PaperSummary.read_mode: Literal["whole", "split", "map_reduce"] = "whole"` and `parts: int = 1` (notes calls made). Old rows read as `whole`. `rpipe show ID` prints the mode when not whole; the health line adds `in parts N` (papers with `read_mode == "map_reduce"`).

**Not in this plan:** ruling 10's "my call, to check" (CPU tier always drops appendices) — left until Task 6 shows split mode's CPU cost; the deferred B.5 minors.

## Review Focus

1. A paper whose headings are all generic ("1", "2", "Section 3") — no front group — must still be summarized (chunked by size), not crash or produce an empty notes call. → Task 2 test `test_split_without_front_falls_back_to_chunks`.
2. One section longer than the whole budget (a 60K-char "Experiments") must be cut into pieces at paragraph boundaries, each piece citing the real title. → Task 2 test `test_oversized_section_is_split_at_paragraphs`.
3. Many chunks at a small context (8,192) — notes exceed the budget — must condense, not overflow. → Task 2 test `test_notes_condense_when_too_long`.
4. A notes call citing a section from another part (or an invented title) gets the one retry and is flagged `unverified`, same as whole mode. → Task 2 test `test_notes_bad_citation_retries_once`.
5. `auto` on a backend without `gpu_share` (claude-code, plug-ins) or when `/api/ps` fails must give `whole`, never raise. → Task 4 test `test_resolve_mode_unknown_share_is_whole`.

---

### Task 1: Schemas and prompts for notes and combining

**Files:**
- Modify: `src/research_pipeline/summary_models.py`, `src/research_pipeline/prompts.py`
- Test: `tests/test_prompts.py`, `tests/test_summary_models.py` (create if missing)
- Docs: `docs/guide/summaries.md` (new section "Reading a paper in parts": prompts only for now), `docs/guide/glossary.md` (map-reduce, task splitting, notes)

**Interfaces:**
- Produces:
  - `class Notes(BaseModel): claims: list[Claim]` (`Field(min_length=1)`)
  - `PaperSummary.read_mode: Literal["whole", "split", "map_reduce"] = "whole"`, `PaperSummary.parts: int = 1`
  - `Focus = Literal["front", "body", "general", "condense"]`
  - `build_notes_prompt(title: str, sections: list[Section], focus: Focus, part: int, of: int) -> tuple[str, str]`
  - `build_condense_prompt(title: str, notes: list[Claim], part: int, of: int) -> tuple[str, str]`
  - `build_combine_prompts(title: str, notes: list[Claim], titles: list[str]) -> tuple[tuple[str, str], tuple[str, str]]` — (summary prompt, critique prompt); both user texts begin with the same notes block and differ only in the task at the end.
  - `notes_block(notes: list[Claim]) -> str`
  - `PROMPT_VERSION = "2026-11-p7"`

- [ ] **Step 1: Write failing tests**

```python
def test_old_summary_reads_as_whole():
    ps = PaperSummary.model_validate({**minimal_summary_dict(), "unverified": []})
    assert ps.read_mode == "whole" and ps.parts == 1

def test_notes_prompt_wraps_paper_and_names_part():
    system, user = build_notes_prompt("T", [Section(title="Intro", text="x </paper> y")], "front", 1, 2)
    assert user.count("</paper>") == 1          # the paper's own tag is defused
    assert "part 1 of 2" in user
    assert user.rstrip().endswith(REMINDER_NOTES)  # task last

def test_combine_prompts_share_prefix_and_list_titles():
    notes = [Claim(text="It works </notes>", sections=["Intro"])]
    (s_sys, s_user), (c_sys, c_user) = build_combine_prompts("T", notes, ["Intro", "Method"])
    assert s_sys == c_sys
    prefix = s_user[: s_user.index("Task:")]
    assert c_user.startswith(prefix)
    assert '["Intro", "Method"]' in prefix
    assert prefix.count("</notes>") == 1        # note text cannot close the block

def test_whole_prompts_unchanged():
    assert build_summary_prompt("T", [Section(title="A", text="b")], "full_text")[1].endswith(SUMMARY_TASK)
    assert PROMPT_VERSION == "2026-11-p7"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_prompts.py tests/test_summary_models.py -q` — expect FAIL (names undefined).

- [ ] **Step 3: Implement.** `defuse` widens to the `notes` tag too (`<(/?)(paper|notes)`). Notes system prompt: same untrusted-data and citation rules as `_SHARED_RULES`, task "take notes on this part of the paper", 3–10 claims of at most two sentences each; focus texts: front = core claim, what is new, stated limitations; body = method, assumptions, baselines, results with numbers, missing ablations; general = both; condense = merge these notes into fewer claims, keep every cited section. Combine system prompt: input is notes another reader took on the paper part by part (not the paper), inside `<notes>`; untrusted; every claim cites section titles copied from the given list (JSON-quoted, defused), never from the notes' headings; tasks after the block reuse the existing field descriptions and length limits. Notes rendered one per line: `- {text} [sections: {json list}]`.

- [ ] **Step 4: Run** the tests — PASS; then `uv run pytest -q`.

- [ ] **Step 5: Commit** `feat: notes and combine prompts for reading a paper in parts (prompts p7)` with the docs.

### Task 2: `parts.py` — notes per part, condense, combine

**Files:**
- Create: `src/research_pipeline/parts.py`
- Modify: `src/research_pipeline/pruning.py` (add `partition`), `src/research_pipeline/summarize.py` (make `_generate_checked` generic over `Notes` too — widen the TypeVar)
- Test: `tests/test_parts.py`, `tests/test_pruning.py`
- Docs: `docs/guide/summaries.md` ("Reading a paper in parts": how it works, worked example with a 3-chunk paper, diagram front/body → notes → summary + critique)

**Interfaces:**
- Consumes: Task 1 prompts and `Notes`; `pruning.RESERVED_TOKENS`, `CHARS_PER_TOKEN`, `classify`, `_classes`, `_words`; `summarize._generate_checked`, `summarize.claims` (extend to `Notes`).
- Produces:
  - `pruning.partition(sections) -> tuple[list[Section], list[Section], list[Dropped]]` — (paper, appendix, non-paper dropped), using `_classes`.
  - `parts.FRONT_WORDS: tuple[str, ...]` (Decision 3)
  - `parts.chunk_to_budget(sections: list[Section], num_ctx: int) -> list[list[Section]]` — consecutive, greedy; a section over the budget is cut at `"\n\n"` (else hard cut) into pieces that keep its title.
  - `parts.read_in_parts(llm: LLM, title: str, groups: list[tuple[Focus, list[Section]]], num_ctx: int, *, read_mode: Literal["split", "map_reduce"], dropped: list[Dropped], on_phase: Callable[[str], None] | None = None) -> PaperSummary` — each group is chunked to budget, one notes call per chunk; `on_phase` gets `"notes 2/3"`, `"condense"`, `"summary"`, `"critique"`, `"retry"`.
  - `parts.split_groups(sections: list[Section], num_ctx: int) -> tuple[list[tuple[Focus, list[Section]]], list[Dropped]]` — Decision 3 plus ruling 10: appendices join the body only if body + appendices fit one budget; else they are dropped (reason `appendix`). Empty front or body → `[("general", paper + fitting appendices)]`.
  - `MAX_CONDENSE_ROUNDS = 3`

- [ ] **Step 1: Write failing tests** (fake LLM returns queued pydantic objects and records prompts)

```python
def test_split_groups_by_heading():
    groups, dropped = split_groups([S("Abstract"), S("1 Introduction"), S("2 Method"), S("3 Results"),
                                    S("4 Conclusion"), S("References")], 32768)
    assert [(f, [s.title for s in g]) for f, g in groups] == [
        ("front", ["Abstract", "1 Introduction", "4 Conclusion"]), ("body", ["2 Method", "3 Results"])]
    assert [d.title for d in dropped] == ["References"]

def test_appendix_dropped_when_body_too_long(): ...   # body fills the budget; "Appendix A" → dropped reason "appendix"
def test_split_without_front_falls_back_to_chunks(): ...  # headings "1","2","3" → [("general", ...)]
def test_oversized_section_is_split_at_paragraphs():
    chunks = chunk_to_budget([Section(title="5 Experiments", text=("p" * 9000 + "\n\n") * 8)], 16384)
    assert len(chunks) > 1 and all(s.title == "5 Experiments" for c in chunks for s in c)
    assert all(sum(len(s.title) + len(s.text) for s in c) <= (16384 - 4000) * 3 for c in chunks)

def test_read_in_parts_calls_and_result():
    llm = FakeLLM([notes("Intro"), notes("Method"), summary_citing("Intro"), critique_citing("Method")])
    ps = read_in_parts(llm, "T", [("front", [S("Intro")]), ("body", [S("Method")])], 32768,
                       read_mode="split", dropped=[])
    assert ps.read_mode == "split" and ps.parts == 2 and not ps.body_cut and ps.unverified == []
    assert llm.prompts[2][1].startswith(llm.prompts[3][1][: llm.prompts[3][1].index("Task:")])  # shared prefix

def test_notes_bad_citation_retries_once(): ...   # first notes cite "Nope" → second call has citation feedback; still bad → in unverified
def test_notes_condense_when_too_long(): ...      # num_ctx 8192, 12 chunks of long notes → ≥1 "condense" phase, final calls fit budget
def test_condense_gives_up_after_three_rounds(): ...  # notes that never shrink → body_cut True, exactly 3 condense rounds
```

- [ ] **Step 2: Run** `uv run pytest tests/test_parts.py tests/test_pruning.py -q` — FAIL.
- [ ] **Step 3: Implement** the interfaces above. Notes from all parts are concatenated in paper order; titles for citation checks = titles of the sections that were read (after dropping). Notes calls see only their chunk's titles; combine calls see all titles.
- [ ] **Step 4: Run** the tests — PASS; full suite.
- [ ] **Step 5: Commit** `feat: read a paper in parts (notes, condense, combine)` with the docs.

### Task 3: Modes in `summarize_paper` and `run_summarize`

**Files:**
- Modify: `src/research_pipeline/summarize.py`, `src/research_pipeline/spill.py` (pass `fallback_above=None`; rename `_model_cap` → `model_cap`, keep behaviour), `src/research_pipeline/cli.py` (health line `in parts N`; `rpipe show` prints `read in parts (split, 3 notes calls)` / `(map-reduce, …)` when not whole)
- Test: `tests/test_summarize.py`, `tests/test_spill.py`, `tests/test_cli_*.py` (health line, show)
- Docs: `docs/guide/summaries.md` (when each mode runs; the fallback rule with a worked example), `docs/guide/running.md` (health line `in parts`)

**Interfaces:**
- Consumes: Task 2 `read_in_parts`, `split_groups`, `chunk_to_budget`, `pruning.partition`.
- Produces:
  - `summarize_paper(llm, title, abstract, sections, num_ctx, on_phase=None, *, mode: Literal["whole", "split"] = "whole", fallback_above: int | None = None) -> PaperSummary`
  - `run_summarize(..., *, reporter=None, refetch=False, mode: Literal["whole", "split"] = "whole", fallback_above: int | None = None)`; counts gain `"in_parts"`.
  - `spill.model_cap(url: str, model: str) -> int`

- [ ] **Step 1: Write failing tests**

```python
def test_whole_mode_unchanged_when_it_fits(): ...      # 2 calls, read_mode "whole"
def test_split_mode_uses_parts(): ...                  # read_mode "split", ≥4 calls
def test_abstract_only_is_always_whole(): ...          # sections=None + mode "split" → 2 calls, basis "abstract"
def test_cut_paper_falls_back_above_threshold():
    ps = summarize_paper(fake, "T", "", long_sections, 8192, mode="whole", fallback_above=0)
    assert ps.read_mode == "map_reduce" and not ps.body_cut and ps.needed_ctx is None
def test_cut_paper_waits_for_rerun_below_threshold():
    ps = summarize_paper(fake, "T", "", long_sections, 8192, mode="whole", fallback_above=200_000)
    assert ps.read_mode == "whole" and ps.body_cut and ps.needed_ctx
def test_run_summarize_counts_in_parts_and_notes_progress(): ...  # note "reading part 1 of 2 of ID"
def test_rerun_never_falls_back(): ...                # spill passes fallback_above=None
def test_health_line_in_parts(): ...                  # counts {"in_parts": 2} → " · in parts 2"
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** Split mode on a paper with full text: `split_groups` → `read_in_parts(read_mode="split")`. Whole mode: today's path; if `fit.body_cut` and `fallback_above is not None` and `fit.needed_ctx > fallback_above` → `partition`, chunk the paper sections → `read_in_parts([("general", paper)], read_mode="map_reduce", dropped=<non-paper + appendices>)`. Progress labels in `run_summarize`: `notes i/n` → `reading part i of n of ID`, `condense` → `condensing notes for ID`, `summary`/`critique` as today.
- [ ] **Step 4: Run** — PASS; full suite.
- [ ] **Step 5: Commit** `feat: summarize in split mode, map-reduce for papers no rerun can read whole`.

### Task 4: The `summarize_mode` setting, `auto`, and wiring

**Files:**
- Modify: `src/research_pipeline/models.py` (`Config.summarize_mode: Literal["auto", "whole", "split"] = "auto"`), `src/research_pipeline/profile_edit.py` (`set_summarize_mode`), `src/research_pipeline/llm.py` (`OllamaClient.load() -> None`: `/api/generate` with empty prompt, `keep_alive`, the client's `num_ctx`, client timeout), `src/research_pipeline/parts.py` (`resolve_mode`), `src/research_pipeline/cli.py` (`rpipe model set-mode`, `rpipe model show` prints the setting and, for auto, the rule; `run` and `summarize` resolve the mode and compute `fallback_above`)
- Test: `tests/test_parts.py`, `tests/test_profile_edit.py`, `tests/test_cli_model.py`, `tests/test_cli_run.py`, `tests/test_llm.py` (respx for `load`)
- Docs: `docs/guide/summaries.md` (the setting, auto rule, worked examples: 16 GB GPU at 57K → whole; CPU-only → split; claude-code → whole), `docs/guide/llm.md` (`set-mode` in the command list), `docs/guide/hardware.md` (which tier gets which mode)

**Interfaces:**
- Consumes: Task 3 `run_summarize(mode=, fallback_above=)`, `spill.model_cap`, `LLM.gpu_share` (existing on `OllamaClient`).
- Produces:
  - `parts.SPLIT_BELOW_CTX = 26_624`
  - `parts.resolve_mode(setting: str, backend: str, gpu_share: float | None, num_ctx: int) -> tuple[Literal["whole", "split"], str]` — mode plus a one-line plain reason for the progress note (e.g. `"split: part of the model runs from RAM (GPU share 0.62)"`).
  - `profile_edit.set_summarize_mode(vault: Path, value: str) -> None` (case-insensitive; error lists the three values)
  - CLI: `rpipe model set-mode MODE`

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.parametrize("setting,backend,share,ctx,mode", [
    ("whole", "ollama", 0.0, 8192, "whole"), ("split", "claude-code", None, 200_000, "split"),
    ("auto", "claude-code", None, 200_000, "whole"), ("auto", "ollama", 1.0, 57_344, "whole"),
    ("auto", "ollama", 0.93, 57_344, "split"), ("auto", "ollama", 0.0, 32_768, "split"),
    ("auto", "ollama", 1.0, 16_384, "split"), ("auto", "ollama", 1.0, 26_624, "whole"),
])
def test_resolve_mode(setting, backend, share, ctx, mode): assert resolve_mode(setting, backend, share, ctx)[0] == mode
def test_resolve_mode_unknown_share_is_whole(): assert resolve_mode("auto", "ollama", None, 57_344)[0] == "whole"
def test_set_mode_command_validates(): ...   # "SPLIT" saved as "split"; "fast" → exit 1, message names auto, whole, split; config unchanged
def test_run_passes_mode_and_fallback(): ... # ollama + deadline → fallback_above == model_cap; deadline off → 0; claude-code → 0
def test_run_auto_load_failure_is_whole(): ...  # load() raises → whole, note says why, run continues
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** In `run` and `summarize`: after `_ready_llm`, inside a `stage(reporter, "reading mode")`, for `auto` + Ollama call `llm.load()` then `gpu_share()` (any exception → share None); note the reason line; pass mode and `fallback_above` (Decision 2) to `run_summarize`.
- [ ] **Step 4: Run** — PASS; full suite; `uv run ruff check .`.
- [ ] **Step 5: Commit** `feat: summarize_mode setting (auto/whole/split) with rpipe model set-mode`.

### Task 5: Measure modes with `rpipe eval`

**Files:**
- Modify: `src/research_pipeline/evaluate.py` (`run_eval(..., mode: Literal["whole", "split"] = "whole")`, passes `fallback_above=0`; `EvalSetup.read_mode: str = "whole"`; setup and compare lines show it), `src/research_pipeline/cli.py` (`rpipe eval run --mode auto|whole|split`, default: the config setting, resolved as in Task 4)
- Test: `tests/test_evaluate.py`, `tests/test_cli_eval.py`
- Docs: `docs/guide/evaluation.md` ("Comparing reading modes": run both, read the compare line, pass rule from ruling 2)

**Interfaces:**
- Consumes: Task 3 `summarize_paper(mode=, fallback_above=)`, Task 4 `resolve_mode`.
- Produces: `EvalSetup.read_mode`; CLI flag `--mode`.

- [ ] **Step 1: Failing tests:** `test_eval_records_mode` (setup line contains `mode split`), `test_old_eval_file_reads_as_whole`, `test_compare_shows_mode_change` (`whole → split`), `test_eval_mode_flag_overrides_config`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS; full suite.
- [ ] **Step 5: Commit** `feat: rpipe eval run --mode to compare whole and split reading`.

### Task 6: Measurement on the owner's machine (controller, not a subagent)

Not code unless the numbers say so. Runs against the real vault's gold set (10 papers, Claude judge). Never leave config changed: `--mode` is a flag, nothing is saved. Check no nightly run is due (03:00) and nothing else holds the GPU (`ollama ps`).

- [ ] **Step 1:** `rpipe eval run --judge --mode whole` then `rpipe eval run --judge --mode split` (about 10–20 min each). Record facts covered, contradictions, unverified, time per paper.
- [ ] **Step 2:** CPU reading of split vs whole on 2–3 gold papers in a throwaway vault (copy `config.yaml`, `profile.yaml`, `gold/gold.json`; set a CPU-fitting model via `llm_model`/`llm_num_ctx` there; `num_gpu 0` is not a setting, so use the model the recommender gives a CPU-only machine and accept partial GPU use as an approximation, noted as such). Time only; quality from Step 1.
- [ ] **Step 3: Decide the shipped `auto` rule from the numbers** (spec: "by measurement and the answer key"): if split passes ruling 2's bar against whole on GPU, keep Decision 6; if it fails on quality, keep `auto` → split only where the whole mode is predicted to exceed the night (no GPU share) and say so in the docs. Any change to `SPLIT_BELOW_CTX` or the rule goes in one commit with tests and docs.
- [ ] **Step 4:** Results table in `docs/superpowers/handoff.md` (B.6 section) and `docs/guide/summaries.md` ("Why": measured numbers); commit `docs: B.6 measurements and the auto rule they set`.

## Parallelism and file ownership

Tasks 1 → 2 → 3 → 4 → 5 are sequential (each consumes the previous one's interfaces). Task 6 needs everything merged into the working branch. Files are shared across tasks (`summarize.py`, `cli.py`, `summaries.md`), so do not run tasks in parallel.
