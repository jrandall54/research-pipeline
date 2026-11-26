# Part B.1 — Answer key (gold set) drafted and graded by the cloud model — Implementation Plan

> **Phase 6.1** · Status: merged · Design: [phase-06.1-06.6-hardware-adaptive-design.md](../specs/phase-06.1-06.6-hardware-adaptive-design.md) · Roadmap: [ROADMAP.md](../ROADMAP.md)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the cloud backend draft the key facts of gold-set papers for the owner to review, suggest which papers to use, and grade every eval run fact by fact, so every later Part B change is measured for quality without the owner grading by hand.

**Architecture:** A new `judge.py` holds the two cloud-model jobs (draft facts, grade a summary against facts) behind the existing `LLM` protocol, so tests use the existing `FakeLLM`. `gold.py` gains a length-spread suggestion helper; `evaluate.py` gains per-fact verdicts, the setup that produced the run, a self-graded flag and a comparison with the previous run. The CLI adds `rpipe gold draft`, `rpipe gold suggest` and `rpipe eval run --judge`; the judge client comes from a new swappable `Pipeline.make_judge`.

**Tech Stack:** Python 3, typer, pydantic v2, sqlite3, pytest (offline: `FakeLLM`, `AlwaysSummary`, `CliRunner`, monkeypatched `cli.build_pipeline`).

**Spec:** `docs/superpowers/specs/phase-06.1-06.6-hardware-adaptive-design.md` — part 1, and Settled items 1–2.

## Global Constraints

- Tests run offline: no real Claude, Ollama or arXiv (`uv run pytest -q`; `ruff check .` clean).
- Every waiting step reports through `progress.py` (`stage(reporter, name, total=...)`, `handle.step(...)`).
- Every change updates `docs/guide/evaluation.md` (and `glossary.md` for new terms) **in the same commit**; page shape: What it does → How it works → Why → How to run/check → Where the code is. Plain words; "gold set" is explained as "an answer key".
- CLI name in docs and messages: `rpipe`.
- The judge is the Claude Code backend (`backends/claude_code.py`), whatever `llm_backend` is configured for summaries. Judge model: `DEFAULT_MODEL` (`"sonnet"`) with `config.llm_effort`.
- Self-graded: the summarizer and the judge are the same backend name **and** model name → the eval records `self_graded = True` and the report says so; it is allowed.
- Pass bar vs the previous run (Settled item 2): passes when facts covered drops by **at most 1**, contradictions **do not rise**, unverified citations **do not rise** — counted over papers present in both runs.
- Human `rpipe eval grade` grades stay and win over the judge where both exist (the existing "Accurate" line keeps using human grades only).
- Owner model names never appear in code, tests or docs.

## Review Focus

1. A gold-draft paper with no arXiv HTML full text → refused with a message pointing to `rpipe gold add` (drafting from an abstract makes weak facts); nothing saved. (Task 3)
2. The judge answers with a verdict list that skips, repeats or adds fact numbers → only verdicts for the given numbers count; a fact with no verdict is reported as "not judged", never as covered. (Task 1)
3. The Claude usage limit (`LlmLimitReached`) or a judge error hits mid-eval → summaries are kept, judging stops (limit) or skips that paper (other error), and the report says "judged N of M". (Task 4)
4. Ctrl-C / end of input during the draft review → nothing is saved for that paper, earlier finished papers stay saved. (Task 3)
5. The previous eval used a different gold set or had no judge → compare only papers judged in both; with none, the report says "no earlier judged run to compare". (Task 4)

---

## Parallelism and file ownership

- Task 1 (`judge.py`, `tests/test_judge.py`) and Task 2 (`gold.py`, `tests/test_gold.py`, `cli.py` gold-suggest block) can run **in parallel**.
- Task 3 and Task 4 both edit `cli.py` and `docs/guide/evaluation.md`: run **after Task 1, one after the other** (3 then 4).
- Task 2 also edits `cli.py` and `evaluation.md`; if run in parallel with Task 1, merge before Task 3 starts.

---

### Task 1: `judge.py` — draft facts and grade a summary

**Files:**
- Create: `src/research_pipeline/judge.py`
- Test: `tests/test_judge.py`
- Docs: `docs/guide/evaluation.md` (new section "The judge"), `docs/guide/glossary.md` (judge, self-graded, answer key = gold set)

**Interfaces:**
- Consumes: `LLM` protocol and `LlmError`, `LlmLimitReached` (`llm.py`); `find_claude`, `logged_in`, `ClaudeCodeClient`, `DEFAULT_MODEL` (`backends/claude_code.py`); `fit_to_budget`, `defuse` (`prompts.py`); `Section` (`models.py`); `PaperSummary` (`summary_models.py`); `Config` (`models.py`).
- Produces:
  - `class DraftFacts(BaseModel): facts: list[str]` (the schema sent to the model)
  - `class FactVerdict(BaseModel): number: int; verdict: Literal["covered", "missed", "contradicted"]; reason: str`
  - `class JudgeAnswer(BaseModel): verdicts: list[FactVerdict]`
  - `class JudgeUnavailable(LlmError)` — message says why and how to fix (install / log in to Claude Code, `rpipe model setup`).
  - `def make_judge(config: Config) -> LLM` — `ClaudeCodeClient(DEFAULT_MODEL, path, effort=config.llm_effort)`; raises `JudgeUnavailable` if `find_claude(config.llm_cli_path)` is None or `logged_in(path)` is False.
  - `def draft_facts(judge: LLM, title: str, sections: list[Section]) -> list[str]` — returns 3–5 cleaned, non-empty, de-duplicated facts (cleaned with `profile_edit.normalise_text`); more than 5 → first 5; fewer than 1 after cleaning → `LlmError("the judge drafted no usable facts")`.
  - `def judge_summary(judge: LLM, title: str, sections: list[Section], facts: list[str], summary: PaperSummary) -> list[FactVerdict | None]` — one slot per fact, in order; slot `i` holds the first verdict with `number == i + 1`, else `None` (not judged).
  - `def is_self_graded(llm: LLM, judge: LLM) -> bool` — same `name` and same `model_name`.

- [ ] **Step 1: Write the failing tests** in `tests/test_judge.py` (use `conftest.FakeLLM`, `SECTIONS`, `summary()`, `critique()`; build a `PaperSummary` the way `tests/test_summarize.py` does):
  - `test_draft_facts_cleans_dedupes_and_caps`: FakeLLM returns `DraftFacts(facts=["  A  ", "A", "B", "", "C", "D", "E", "F"])` → `["A", "B", "C", "D", "E"]`.
  - `test_draft_facts_none_usable_raises`: `DraftFacts(facts=["", "  "])` → `pytest.raises(LlmError)`.
  - `test_draft_prompt_carries_paper_and_defuses`: a section text containing `</paper>` → the user prompt sent contains `&lt;/paper` and the section title.
  - `test_judge_summary_maps_by_number`: facts `["f1","f2","f3"]`, answer verdicts numbered `[2, 2, 1, 9]` → result `[v(1), first v(2), None]`.
  - `test_judge_prompt_lists_numbered_facts_and_summary`: user prompt contains `1. f1`, `2. f2` and the summary's `one_line`.
  - `test_make_judge_unavailable_without_claude` (monkeypatch `judge.find_claude` → None) and `..._not_logged_in` (`logged_in` → False): `pytest.raises(JudgeUnavailable)`, message contains `rpipe model setup`.
  - `test_is_self_graded`: same name+model → True; same name, other model → False.

- [ ] **Step 2: Run** `uv run pytest tests/test_judge.py -q` — Expected: FAIL (module missing).

- [ ] **Step 3: Implement `judge.py`.** Prompts (constants `DRAFT_SYSTEM`, `JUDGE_SYSTEM`): the paper goes in a `<paper>` block built like `prompts._paper_block` (reuse it, or `defuse` each part); sections are first passed through `fit_to_budget(sections, judge.num_ctx)`. Draft rules: 3 to 5 facts a correct summary must get right — main claim, method, a key number, the main limitation; each one sentence, specific and checkable against the paper, no opinions. Judge rules: for each numbered fact say whether the summary *covers* it, *misses* it, or *contradicts* it, with a one-line reason; judge only the summary text given, use the paper only to understand the fact. The summary is rendered as plain text (one_line, problem, approach, results, limitations, strengths, weaknesses) inside a `<summary>` block, defused.

- [ ] **Step 4: Run** `uv run pytest tests/test_judge.py -q` — Expected: PASS. Then `uv run pytest -q && ruff check .`.

- [ ] **Step 5: Docs** — `evaluation.md`: new "The judge" section (what it is, that it is Claude Code whatever backend summarizes, that paper text goes to Anthropic, that it may be self-graded, how unknown verdicts show). Glossary entries.

- [ ] **Step 6: Commit** `feat: judge drafts key facts and grades summaries fact by fact` (with the session trailers).

---

### Task 2: `rpipe gold suggest` — papers spread across lengths

**Files:**
- Modify: `src/research_pipeline/gold.py`, `src/research_pipeline/cli.py` (gold commands block, after `gold_list`)
- Test: `tests/test_gold.py`
- Docs: `docs/guide/evaluation.md` ("Choosing papers")

**Interfaces:**
- Consumes: `get_fulltext` and the `papers`/`summaries`/`fulltexts` tables (`db.py`); `is_back_matter`, `CHARS_PER_TOKEN` (`prompts.py`); `load_gold`, `has_gold`.
- Produces:
  - `@dataclass(frozen=True) class Suggestion: paper_id: str; title: str; tokens: int`
  - `def suggest_papers(conn: sqlite3.Connection, gold: list[GoldPaper], count: int = GOLD_TARGET) -> list[Suggestion]` — candidates: papers with a stored summary **and** a stored full text, not already in `gold`; `tokens` = characters of non-back-matter sections // `CHARS_PER_TOKEN`; sorted by tokens; picks `count` (or all if fewer) spread evenly across the sorted list (always including the shortest and the longest); result sorted by tokens.

- [ ] **Step 1: Failing tests** in `tests/test_gold.py` (use `init_vault` + `db` helpers to insert papers, `save_fulltext`, `save_summary`):
  - `test_suggest_spreads_lengths`: 9 candidates of sizes 1..9 (×3000 chars), count 3 → tokens of picks are the shortest, the middle, the longest.
  - `test_suggest_skips_gold_and_unsummarized`: a paper already in gold and one without a summary are never suggested.
  - `test_suggest_fewer_than_count_returns_all`.
  - `test_cli_gold_suggest_lists_and_hints`: output lists ids with `~N tokens` and ends with `rpipe gold draft ID ...`; empty vault → message "No summarized papers with full text yet — run `rpipe run` first, or add papers by hand with `rpipe gold add`." and exit 0.
- [ ] **Step 2: Run** `uv run pytest tests/test_gold.py -q` — FAIL.
- [ ] **Step 3: Implement** `suggest_papers` and `@gold_app.command("suggest")` with `--count` (int, ≥1, default `GOLD_TARGET`).
- [ ] **Step 4: Run** the tests, then the full suite and ruff — PASS.
- [ ] **Step 5: Docs** — "Choosing papers": why a spread of lengths (long papers are where truncation and pruning matter).
- [ ] **Step 6: Commit** `feat: rpipe gold suggest picks answer-key papers across lengths`.

---

### Task 3: `rpipe gold draft` — cloud-drafted facts, owner review

**Files:**
- Modify: `src/research_pipeline/cli.py` (`Pipeline` dataclass, `build_pipeline`, gold commands block)
- Test: `tests/test_gold_draft.py`
- Docs: `docs/guide/evaluation.md` ("Drafting key facts")

**Interfaces:**
- Consumes: Task 1 `make_judge`, `draft_facts`, `JudgeUnavailable`; `Pipeline.fulltext_fetcher`, `Pipeline.seed_fetcher`; `ensure_usable`, `FullTextUnavailable`; `add_gold`, `has_gold`, `load_gold`, `GoldPaper`; `_ask_facts`.
- Produces:
  - `Pipeline.make_judge: Callable[[], LLM]` — new last field, default a function raising `JudgeUnavailable("no judge configured")`; `build_pipeline` sets `make_judge=lambda: judge.make_judge(config)`.
  - `rpipe gold draft ID [ID ...]` (`--vault`).

Behaviour, per id, in order: skip with a message if already in gold; look up title via `seed_fetcher`; fetch full text (`FullTextUnavailable` → "no HTML full text for ID; add it by hand with `rpipe gold add ID`", go on to the next id); draft inside a progress stage `gold draft` (`total=len(ids)`, one `step` per paper); show the paper title, then each drafted fact numbered, asking `Keep, edit or drop? [k/e/d]` (edit asks for the new text, cleaned; empty edit = keep); then `_ask_facts()` for any extra facts; save with `add_gold` only if ≥1 fact remains, else "No facts kept, so nothing was saved for ID." Judge unavailable → one clear error, exit 1, before any fetching.

- [ ] **Step 1: Failing tests** (monkeypatch `cli.build_pipeline` to a `Pipeline` with stub fetchers and `make_judge=lambda: FakeLLM([DraftFacts(...)])`; drive prompts with `CliRunner.invoke(..., input=...)`):
  - `test_draft_keep_edit_drop_then_save`: drafted `["a","b","c"]`, input `k`, `e` + `"b2"`, `d`, blank → saved facts `["a", "b2"]`.
  - `test_draft_extra_facts_appended`: keep all, then type `"x"` + blank → facts end with `"x"`.
  - `test_draft_no_fulltext_skips_with_hint` (Review Focus 1): fetcher raises `FullTextUnavailable` → output contains `rpipe gold add`, gold empty, exit 0.
  - `test_draft_abort_saves_nothing_for_that_paper` (Review Focus 4): two ids; first fully reviewed, input ends during the second's review → first saved, second absent.
  - `test_draft_judge_unavailable_exits_1`: `make_judge` raises `JudgeUnavailable("log in")` → exit 1, message shown, fetcher never called.
  - `test_draft_skips_existing_gold`.
- [ ] **Step 2: Run** `uv run pytest tests/test_gold_draft.py -q` — FAIL.
- [ ] **Step 3: Implement** the `Pipeline` field, `build_pipeline` wiring, and `gold_draft` command.
- [ ] **Step 4: Run** the tests, full suite, ruff — PASS.
- [ ] **Step 5: Docs** — "Drafting key facts": worked example transcript (illustrative), that the paper text is sent to Claude, review keys, how to add by hand instead.
- [ ] **Step 6: Commit** `feat: rpipe gold draft drafts key facts with the cloud judge for review`.

---

### Task 4: `rpipe eval run --judge` — per-fact grading, setup record, comparison

**Files:**
- Modify: `src/research_pipeline/evaluate.py`, `src/research_pipeline/cli.py` (`eval_run`)
- Test: `tests/test_eval.py`
- Docs: `docs/guide/evaluation.md` ("Automatic grading", "Comparing runs")

**Interfaces:**
- Consumes: Task 1 `judge_summary`, `FactVerdict`, `is_self_graded`; Task 3 `Pipeline.make_judge`; `LlmLimitReached`; `PROMPT_VERSION`.
- Produces (all new fields optional/defaulted, so old eval files still load):
  - `EvalEntry.verdicts: list[FactVerdict | None] | None = None` (None = not judged); `EvalEntry.judge_error: str | None = None`
  - `class EvalSetup(BaseModel): backend: str; model: str; num_ctx: int; prompt_version: str; judge_model: str | None = None; self_graded: bool = False`
  - `EvalFile.setup: EvalSetup | None = None`
  - `run_eval(..., *, reporter=None, judge: LLM | None = None)` — after each summary, if `judge` and the paper is in gold: `judge_summary(...)`; `LlmLimitReached` → stop judging for the rest of the run (summaries continue), record `judge_error="usage limit reached"` on the rest; any other `LlmError` → `judge_error` on that entry, carry on. Writes `setup`.
  - `def judged_counts(data: EvalFile) -> dict[str, int]` — keys `papers_judged`, `covered`, `missed`, `contradicted`, `not_judged`, `unverified` (sum of `len(summary.unverified)` over judged papers).
  - `def compare(previous: EvalFile, current: EvalFile) -> str` — over papers judged in both; applies the pass bar from Global Constraints; returns lines ending `Change: PASS` or `Change: FAIL (<which rule>)`; no common papers → `"No earlier judged run to compare."`.
  - `report(vault, day)` gains, when the file has verdicts: `Judged: N of M papers`, `Facts covered: c of t (missed m, contradicted x, not judged n)`, `Self-graded` line when set, `Setup: backend/model, context N, prompts V`, and the `compare` result against the latest earlier eval file that has verdicts.
  - CLI: `rpipe eval run --judge` builds the judge via `pipeline.make_judge()` **before** summarizing (unavailable → exit 1 with its message, nothing run).

- [ ] **Step 1: Failing tests** in `tests/test_eval.py`:
  - `test_eval_judge_records_verdicts_and_setup`: `AlwaysSummary` summarizer + FakeLLM judge answering `JudgeAnswer(verdicts=[FactVerdict(number=1, verdict="covered", reason="r")])` → entry verdicts `[covered]`, `setup.model == "fake-llm"`, `setup.prompt_version == PROMPT_VERSION`.
  - `test_eval_judge_limit_stops_judging_keeps_summaries` (Review Focus 3): judge raises `LlmLimitReached` on paper 1 of 3 → 3 summaries, 0 verdicts, report shows `Judged: 0 of 3`.
  - `test_eval_judge_error_skips_one_paper`: `LlmError` on paper 1 of 2 → paper 2 judged.
  - `test_eval_self_graded_flag`: summarizer and judge objects with equal `name`/`model_name` → `setup.self_graded` and report line `Self-graded`.
  - `test_report_counts_not_judged_separately` (Review Focus 2): verdicts `[covered, None]` → `Facts covered: 1 of 2 (missed 0, contradicted 0, not judged 1)`.
  - `test_compare_pass_and_fail_rules`: covered −1 → PASS; covered −2 → FAIL (facts covered); contradicted +1 → FAIL; unverified +1 → FAIL.
  - `test_compare_no_common_papers` (Review Focus 5).
  - `test_old_eval_file_still_loads`: a JSON written without the new fields loads and reports.
  - `test_cli_eval_run_judge_unavailable_exits_before_summarizing`.
- [ ] **Step 2: Run** `uv run pytest tests/test_eval.py -q` — FAIL.
- [ ] **Step 3: Implement** in `evaluate.py` and `cli.eval_run` (`--judge` option; pass `judge=` to `run_eval`; the per-paper progress `detail` says `judging` while the judge runs).
- [ ] **Step 4: Run** tests, full suite, ruff — PASS.
- [ ] **Step 5: Docs** — "Automatic grading" (what covered/missed/contradicted/not judged mean, self-graded, usage-limit behaviour) and "Comparing runs" (the pass bar in plain words, why one run is enough at temperature 0). Update the page's "How to run" with the full flow: `rpipe gold suggest` → `rpipe gold draft …` → `rpipe eval run --judge` → `rpipe eval report`.
- [ ] **Step 6: Commit** `feat: rpipe eval run --judge grades every key fact and compares with the last run`.

---

### Task 5: Owner run-through (real, not a test)

- [ ] Reinstall the tool (`uv tool install --editable '.[embed,serve]' --force`); `rpipe gold suggest`; `rpipe gold draft` the suggested ids (the owner reviews); `rpipe eval run --judge`; `rpipe eval report`. Record in `docs/superpowers/archive/handoff-log-to-2026-11-25.md` the baseline numbers (facts covered, contradictions, unverified, time) — this is the baseline Parts B.2 and B.3 are compared against.
