# Phase 9 — Hooks, key numbers, plain words and diagrams: Implementation Plan

> **Phase 9** · Status: planned · Design: [phase-09-hooks-and-diagrams-design.md](../specs/phase-09-hooks-and-diagrams-design.md) · Roadmap: [ROADMAP.md](../ROADMAP.md)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each newly summarized paper gets a checked hook line, up to 3 checked key numbers, plain-words fields and (optionally) a checked diagram, shown in the app.

**Architecture:** The summary call's schema gains `hook` and `key_numbers`; a new pure check (`extras.py`, using `numbers.py`) runs at the end of `summarize_paper` and drops anything that fails, recording plain reasons in `PaperSummary.checks`. A separate diagram call (`diagram.py`) runs in `run_summarize` when the `diagrams` setting resolves to on, and stores a `summaries` row of `kind='diagram'`. The app reads all of it through `library_api` and draws the diagram as server-side SVG (`webapp/diagram_svg.py`).

**Tech Stack:** Python 3, Pydantic, SQLite, FastAPI + Jinja2 (app), Typer (CLI), pytest with `FakeLLM`.

**Spec:** `docs/superpowers/specs/phase-09-hooks-and-diagrams-design.md`

## Global Constraints

- The app never runs a model; generation only in the nightly run, `rpipe summarize`, requests and eval.
- App group modules reach data only through `library_api`; new modules must be added to the right group in `tests/test_architecture.py` **and** `docs/guide/architecture.md` (pipeline group: `numbers`, `extras`, `diagram`; app group: nothing new at top level, `webapp` already listed).
- No schema change: new summary parts live in the `summary` row's JSON; the diagram is a `summaries` row with `kind = 'diagram'`.
- Old rows must still load (`hook=None`, `key_numbers=[]`, `checks=[]`, no diagram).
- `unverified` keeps its meaning: only the summary's and critique's main claims (problem, approach, results, limitations, strengths, weaknesses). Hook and key numbers never go in it.
- Limits from the spec: hook about 90 characters (prompt says "at most 90 characters"), 0 to 3 key numbers, diagram 2 to 8 nodes, node label at most 4 words.
- Pages make no third-party requests and load no new JavaScript.
- Every waiting step reports progress (`progress.py`); every setting has an `rpipe` command; tests offline; `uv run pytest -q` and `uv run ruff check .` clean after every task.
- Each task updates its `docs/guide/` pages in the same commit (page shape: What it does → How it works → Why → How to check → Where the code is). New terms go in `docs/guide/glossary.md`.
- **Build in a git worktree** (`.worktrees/phase-09`, branch `phase-09-hooks-and-diagrams`), never by switching branches in the main folder: the nightly run (03:00) and the app service run whatever the main folder has checked out (editable install), and half-built Phase 9 code must not run there.
- Commit per task. Never `git add -A` (the owner's `.idea/` is untracked); add files by name.

## Review Focus

1. **A hook that only rewords the title** (no number, valid citation) is shown. That is allowed; the check must not demand a number.
2. **The same number twice in a hook** (`28% … 28%`) or a number that is part of a word (`GPT-5.5`, `Llama-3`, `S-LoRA`) must not fail the hook: model/version names are not claims. → Task 2 test `test_numbers_in_skips_names_and_versions`.
3. **A hook citing "Abstract"** on a full-text paper: the stored sections may not include an "Abstract" section. The paper's abstract text must count as a section named "Abstract". → Task 4 test `test_hook_citing_abstract_checks_against_abstract`.
4. **A model that ignores the new fields** (old or small models return no `hook`/`key_numbers`): summary still saves, card shows the one-liner. → Task 4 test `test_missing_hook_is_not_a_failure`.
5. **A diagram whose edges form a cycle in a `parts` shape, or with an isolated node**: drawing must not crash or loop; any node set of 2 to 8 draws. → Task 8 test `test_svg_draws_any_valid_graph`.

---

### Task 1: Schema for the new parts

**Files:**
- Modify: `src/research_pipeline/summary_models.py`, `src/research_pipeline/db.py` (`save_summary`, `get_summary`; new `save_diagram`, `get_diagram`)
- Test: `tests/test_db.py`, `tests/test_summary_models.py` (create if missing)
- Docs: `docs/guide/data-model.md`

**Interfaces:**
- Produces:
  - `class KeyNumber(BaseModel)`: `value: str`, `label: str`, `sections: list[str] = Field(min_length=1)`
  - `Summary.hook: Claim | None = None`, `Summary.key_numbers: list[KeyNumber] = []`
  - `PaperSummary.checks: list[str] = []` (plain reasons a hook, key number or diagram was left out)
  - `class DiagramNode(BaseModel)`: `id: str`, `label: str`, `detail: str | None = None`, `sections: list[str] = Field(min_length=1)`
  - `class DiagramEdge(BaseModel)`: `source: str`, `target: str`, `label: str | None = None` (JSON keys `from`/`to` via `Field(alias=...)`, `populate_by_name=True`)
  - `class Diagram(BaseModel)`: `shape: Literal["parts", "steps", "loop"]`, `nodes: list[DiagramNode]`, `edges: list[DiagramEdge]`, `caption: str`
  - `class DiagramAnswer(BaseModel)`: `diagram: Diagram | None` (the model's whole answer; `null` = no clear shape)
  - `db.save_diagram(conn, paper_id: str, diagram: Diagram | None, model: str) -> None` (None deletes the row; does not commit)
  - `db.get_diagram(conn, paper_id: str) -> Diagram | None`

- [ ] **Step 1: Write failing tests**: `test_old_summary_row_loads_without_new_fields` (a row JSON without `hook`, `key_numbers`, `checks` loads: `hook is None`, `key_numbers == []`, `checks == []`); `test_hook_key_numbers_checks_round_trip`; `test_diagram_round_trip_and_delete` (save, get equal; save None, get None; `get_summary` unaffected by a diagram row); `test_diagram_edge_accepts_from_to_keys`.
- [ ] **Step 2: Run** `uv run pytest -q tests/test_db.py tests/test_summary_models.py`: new tests FAIL.
- [ ] **Step 3: Implement.** `save_summary` writes `checks` into the summary JSON next to `unverified`; `get_summary` pops `checks` with default `[]`. Diagram row: `kind='diagram'`, `text = diagram.model_dump_json(by_alias=True)`.
- [ ] **Step 4: Run** the full suite: PASS.
- [ ] **Step 5: Docs + commit.** `data-model.md`: new JSON keys and the `diagram` row kind. `git commit -m "feat: schema for hooks, key numbers, checks and diagrams (Phase 9)"`

### Task 2: Number matching (`numbers.py`)

**Files:**
- Create: `src/research_pipeline/numbers.py`; add `"numbers"` to `PIPELINE` in `tests/test_architecture.py` and to `docs/guide/architecture.md`
- Test: `tests/test_numbers.py`

**Interfaces:**
- Produces:
  - `numbers_in(text: str) -> list[str]`: the numbers a reader would take as claims, normalized (no spaces, no thousands separators, `×`→`x`, `\%`→`%`), in order, duplicates removed.
  - `number_found(number: str, text: str) -> bool`

Rules (from the spec's "Settled after the builder check"):
- A number is digits with an optional decimal part, optionally followed by `%` or `x`/`×` (spaces allowed before the sign). Commas between digit groups of 3 are thousands separators (`1,200` → `1200`).
- Skipped (not claims): digits glued to letters or a hyphen on the left (`GPT-5.5`, `Llama-3`, `S-LoRA`, `v2`), section numbers after `§`.
- `number_found`: true if any number in `text` (same extraction, but *without* the name/version skip) equals it, or rounds to it: same sign suffix (`%`, `x`, or none), and the text's number rounded to the hook number's decimal places equals it (`28%` ← `28.3%`, `1.4x` ← `1.37x`, `13.7` ← `13.67`). `28%` does not match `0.28`; `2` does not match `2000`.

- [ ] **Step 1: Write failing tests**:

```python
def test_numbers_in_formats():
    assert numbers_in("28 % less, 1.37× faster, 1,200 tasks, 28\\% again") == ["28%", "1.37x", "1200"]

def test_numbers_in_skips_names_and_versions():
    assert numbers_in("GPT-5.5 and Llama-3 beat S-LoRA v2 in §5.1") == []

def test_number_found_exact_and_rounded():
    text = "It reduced time by 28.3% and ran 1.37x faster in 13.67 minutes over 1,200 runs."
    for n in ["28.3%", "28%", "1.4x", "1.37x", "13.7", "1200"]:
        assert number_found(n, text), n

def test_number_found_rejects_unit_and_form_changes():
    text = "It reduced time by 28% in 2000 ms; 13.8 versus 26.0 minutes."
    for n in ["0.28", "2", "2x", "29%", "27%"]:
        assert not number_found(n, text), n
```

- [ ] **Step 2: Run** `uv run pytest -q tests/test_numbers.py`: FAIL (module missing).
- [ ] **Step 3: Implement** with one compiled regex; compare as `decimal.Decimal` with `quantize` to the hook number's decimal places (`ROUND_HALF_UP`).
- [ ] **Step 4: Run** full suite: PASS.
- [ ] **Step 5: Commit** `feat: number matching for checked hooks (Phase 9)` (docs for it land in Task 4's `summaries.md` section; `architecture.md` updated here).

### Task 3: Prompts: hook, key numbers, plain words

**Files:**
- Modify: `src/research_pipeline/prompts.py`
- Test: `tests/test_prompt_layout.py`, `tests/test_prompt_limits.py`
- Docs: `docs/guide/summaries.md` (prompt section)

**Interfaces:**
- Produces: `PROMPT_VERSION = "2026-11-p9"`; unchanged builder signatures (`build_summary_prompt`, `build_critique_prompt`, `build_combine_prompts`).

Exact text to add (Claude's wording of the owner's rules):

- In `_TASK_FIELDS`, summary fields become: `one_line (one sentence on what the paper does), hook, key_numbers, problem, approach, results, limitations.` plus:
  `hook is a claim: one line (at most 90 characters) stating the paper's main finding as a plain fact, using the paper's own numbers when it has them. Never claim more than the paper shows, never round a number into a bigger-sounding one, no questions, no teasers, no held-back answers. key_numbers: 0 to 3 numbers the paper reports that matter most, each with value (exactly as the paper writes it, e.g. "28%"), label (what it measures, a few plain words) and sections.`
- New shared plain-words rule, used by both summary and critique tasks:
  `Write for a reader who is not an expert in this field: short sentences, everyday words, and when a technical term is needed, explain it in a few words. Keep the paper's numbers.`
- `_SUMMARY_LIMITS`: `Length limits: one_line is one sentence; hook at most 90 characters. Each claim text is at most two short sentences.`
- `_CRITIQUE_LIMITS`: "at most two sentences" → "at most two short sentences".
- The combine (reading in parts) summary and critique tasks use the same limits and rule (they already reuse `_SUMMARY_LIMITS`/`_CRITIQUE_LIMITS`).

- [ ] **Step 1: Failing tests**: `test_prompt_version_is_p9`; `test_summary_prompt_asks_for_hook_and_key_numbers` (both `"hook"` and `"key_numbers"` and `"at most 90 characters"` in the summary system + task text); `test_plain_words_rule_in_summary_critique_and_combine`. Existing layout tests (shared prefix, task last) must keep passing.
- [ ] **Step 2: Run**: new tests FAIL.
- [ ] **Step 3: Implement** the text changes.
- [ ] **Step 4: Run** full suite: PASS.
- [ ] **Step 5: Docs + commit.** `summaries.md`: the new fields, the hook rules, plain-words rule, version bump, and why (owner's definition of a hook; Phase 8.1 shows two sentences as two points). `feat: prompts ask for a hook, key numbers and plain words (Phase 9)`

### Task 4: Check hook and key numbers (`extras.py`), wired into `summarize_paper`

**Files:**
- Create: `src/research_pipeline/extras.py` (add to `PIPELINE` in `tests/test_architecture.py` and `architecture.md`)
- Modify: `src/research_pipeline/citations.py` (`extra_claims`, retry covers extras), `src/research_pipeline/summarize.py`, `src/research_pipeline/parts.py` (combine path uses the same retry)
- Test: `tests/test_extras.py`, `tests/test_summarize.py`
- Docs: `docs/guide/summaries.md` (new section "Hook and key numbers: how they are checked")

**Interfaces:**
- Consumes: `numbers_in`, `number_found` (Task 2); `KeyNumber`, `Summary.hook`, `PaperSummary.checks` (Task 1).
- Produces:
  - `citations.extra_claims(summary: Summary) -> list[Claim]`: the hook, and one `Claim(text=f"{k.value} {k.label}", sections=k.sections)` per key number.
  - `generate_checked` unchanged signature; when `schema is Summary`, `bad` (which triggers the one retry) also counts `extra_claims`; the returned `unverified` list still uses `claims()` only.
  - `extras.check_extras(ps: PaperSummary, sections: list[Section], abstract: str) -> PaperSummary`: returns a copy with a failed hook set to `None`, failed key numbers removed, and one reason per failure appended to `checks`.

Check rules (spec §2):
- Cited section lookup: `norm_title` match against `sections`; the title "Abstract" always also matches the `abstract` text.
- Hook fails if any cited section is unknown (`"hook: cites a section the paper does not have"`), or if any `numbers_in(hook.text)` is not `number_found` in the joined text of its cited sections (`f"hook: {n} not found in {tag}"`, `tag` like `§5.1`, the first cited title when unnumbered).
- A key number fails the same two ways (`f"key number {value}: …"`); the check uses `numbers_in(value)` (a value with no number fails: `"key number {value}: not a number"`).
- A hook with no numbers and valid citations passes.

Wiring: `summarize_paper` keeps its signature. Its body is moved to `_read_paper(...)` (same parameters) and `summarize_paper` returns `check_extras(_read_paper(...), full_sections, abstract)`, where `full_sections` is the `sections` argument (or `[Section("Abstract", abstract)]` when it is None). So whole, split, map-reduce, spill reruns and eval all get the check.

- [ ] **Step 1: Failing tests** in `tests/test_extras.py`:
  - `test_hook_with_found_number_is_kept`
  - `test_hook_with_missing_number_falls_back_with_reason` (`hook is None`, `checks == ["hook: 2.5x not found in §5"]`)
  - `test_hook_with_unknown_section_falls_back`
  - `test_hook_without_numbers_is_kept`
  - `test_hook_citing_abstract_checks_against_abstract`
  - `test_key_numbers_dropped_one_by_one` (3 in, the bad middle one out, order kept)
  - `test_missing_hook_is_not_a_failure` (`hook=None` in, `checks == []`)
  - In `tests/test_summarize.py`: `test_summarize_paper_applies_extras_check` (FakeLLM answers a hook with an unfound number → result `hook is None`, reason in `checks`, `unverified == []`); `test_bad_hook_citation_triggers_the_one_retry`.
- [ ] **Step 2: Run**: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** full suite: PASS.
- [ ] **Step 5: Docs + commit.** `summaries.md` new section with a worked example (the ACAP hook: "28%" found in §5.1 passes; "twice as fast" has no number and passes on citation only; "2.5x" fails), what code cannot check (overstatement in words), rejected alternatives (model grading itself; loose matching). `glossary.md`: hook, key number. `feat: check hooks and key numbers against the paper text (Phase 9)`

### Task 5: The `diagrams` setting

**Files:**
- Modify: `src/research_pipeline/models.py` (`Config.diagrams: Literal["auto", "on", "off"] = "auto"`), `src/research_pipeline/profile_edit.py` (`set_diagrams`), `src/research_pipeline/parts.py` (`resolve_diagrams`), `src/research_pipeline/cli.py` (`rpipe model set-diagrams`, `rpipe model show` line, `_read_mode` / `_summarize_plan`)
- Test: `tests/test_parts.py`, `tests/test_profile_edit.py`, `tests/test_cli_model.py`, `tests/test_spill.py` (callers)
- Docs: `docs/guide/llm.md` (or wherever `set-mode` is documented: same page), `docs/guide/hardware.md` (the CPU-only default)

**Interfaces:**
- Produces:
  - `parts.resolve_diagrams(setting: str, backend: str, gpu_share: float | None) -> tuple[bool, str]`: `on`→True, `off`→False, `auto`: non-Ollama True, share None True, share 0.0 False, else True. Reasons: `"diagrams on: set with `rpipe model set-diagrams on`"`, `"diagrams off: the model runs on the CPU only (GPU share 0.0)"`, etc.
  - `profile_edit.set_diagrams(vault: Path, value: str) -> None` (same validation style as `set_summarize_mode`: any capitals, spaces trimmed, bad value raises `ValueError` listing `auto, on, off`).
  - `_read_mode` returns `tuple[Literal["whole","split"], float | None]` (mode and the GPU share it read) and notes the diagrams reason in the same "reading mode" stage; `_summarize_plan` returns `tuple[mode, fallback_above, diagrams: bool]`. All three callers unpack three values and pass `diagrams=` on (Task 6 adds the parameter; until then pass nothing).

- [ ] **Step 1: Failing tests**: `test_resolve_diagrams_cases` (each row above); `test_set_diagrams_accepts_capitals_refuses_bad`; `test_model_set_diagrams_command` (prints `diagrams is now off.`, config changed, bad value exits 1 with the choices and config unchanged); `test_model_show_prints_diagrams_line` (`diagrams: auto`).
- [ ] **Step 2: Run**: FAIL.
- [ ] **Step 3: Implement.** For `auto` with Ollama, reuse the share `_read_mode` already reads; when the summarize mode is fixed (no load), the share is unknown → diagrams on unless `off`.
- [ ] **Step 4: Run** full suite: PASS.
- [ ] **Step 5: Docs + commit.** `feat: rpipe model set-diagrams (auto, on, off) (Phase 9)`

### Task 6: The diagram call (`diagram.py`) in summarize, requests and eval

**Files:**
- Create: `src/research_pipeline/diagram.py` (add to `PIPELINE` + `architecture.md`)
- Modify: `src/research_pipeline/prompts.py` (`build_diagram_prompt`), `src/research_pipeline/summarize.py` (`run_summarize(..., diagrams: bool = False)`, `phase_note("diagram")`), `src/research_pipeline/requests_queue.py` (`drain_requests(..., diagrams: bool = False)` passes it on), `src/research_pipeline/evaluate.py` (`run_eval(..., diagrams: bool = False)`, `EvalEntry.diagram: Diagram | None = None`, `EvalEntry.diagram_problems: list[str] = []`), `src/research_pipeline/cli.py` (pass `diagrams` from `_summarize_plan` to all three)
- Test: `tests/test_diagram.py`, `tests/test_summarize.py`, `tests/test_requests_queue.py`, `tests/test_eval.py` (whichever file tests `run_eval`)
- Docs: `docs/guide/summaries.md` (new section "The diagram"), `docs/guide/running.md` (progress note), `docs/guide/evaluation.md`

**Interfaces:**
- Consumes: `Diagram`, `DiagramAnswer`, `db.save_diagram` (Task 1); `norm_title`; `fit_to_budget`.
- Produces:
  - `prompts.build_diagram_prompt(title: str, sections: list[Section], basis: str) -> tuple[str, str]`: same `SHARED_SYSTEM`-style layout (paper first, task last) with a diagram task: shapes `parts`/`steps`/`loop`, 2 to 8 nodes, labels at most 4 words **using the paper's own names for things**, each node cites sections, `caption` one plain sentence, answer `{"diagram": null}` when the method has no clear shape (surveys, theory).
  - `diagram.check_diagram(d: Diagram, sections: list[Section], abstract: str) -> list[str]`: problems, empty = ok. Checks: 2–8 nodes; unique ids; every edge's `source`/`target` is a node id; label ≤ 4 words; every node cites a known section (Task 4's lookup, shared: move the lookup helper into `citations.py` as `section_texts(titles, sections, abstract) -> list[str] | None`); every content word of each label appears in the paper text (lowercase, words of 3+ letters minus a small stop list, strip a trailing `s`, `ing` or `ed` on both sides before comparing).
  - `diagram.make_diagram(llm: LLM, title: str, sections: list[Section] | None, abstract: str, num_ctx: int, on_phase: Callable[[str], None] | None = None) -> tuple[Diagram | None, list[str]]`: one call (`on_phase("diagram")`), check, one retry with the problems listed (`on_phase("retry")`), then drop. Returns `(None, [])` for a `null` answer, `(None, ["diagram: …"])` when dropped.
  - `run_summarize` with `diagrams=True`: after `summarize_paper`, `make_diagram(...)`; save with `save_diagram`, append drop reasons to the stored `PaperSummary.checks` before `save_summary`; counts gain `"diagrams"` (drawn). An `LlmError` in the diagram call does not fail the paper: the summary is saved, reason `"diagram: the model call failed"`. With `diagrams=False` no call is made and any existing diagram row is left alone.

- [ ] **Step 1: Failing tests**: `test_check_diagram_ok`; `test_check_diagram_bad_edge_and_count`; `test_check_diagram_invented_label` (`"Coordinator"` absent from text → problem); `test_check_diagram_plural_and_ing_match` (`"agents"` vs `"agent"`, `"planning"` vs `"plan"`); `test_make_diagram_null_answer`; `test_make_diagram_retry_then_drop`; `test_run_summarize_draws_diagram_when_on` (row saved, `counts["diagrams"] == 1`, note `drawing a diagram for <id>`); `test_run_summarize_no_diagram_call_when_off` (FakeLLM gets exactly 2 calls); `test_diagram_call_failure_keeps_summary`; `test_drain_requests_passes_diagrams`; `test_run_eval_records_diagram`.
- [ ] **Step 2: Run**: FAIL.
- [ ] **Step 3: Implement.** The diagram prompt gets `fit_to_budget(sections, num_ctx).sections` (abstract-only: the abstract section).
- [ ] **Step 4: Run** full suite: PASS.
- [ ] **Step 5: Docs + commit.** `summaries.md` "The diagram" (what, worked example with the ACAP data, checks, why data not Mermaid, why one retry), `running.md` progress note, `evaluation.md` diagram fields. `feat: diagram call with checks, in summarize, requests and eval (Phase 9)`

### Task 7: `library_api` and card shaping

**Files:**
- Modify: `src/research_pipeline/library_api.py` (`PaperView.diagram: Diagram | None`), `src/research_pipeline/webapp/present.py`
- Test: `tests/test_library_api.py`, `tests/test_webapp_present.py`

**Interfaces:**
- Produces:
  - `PaperView.diagram` filled from `db.get_diagram` in `_view`.
  - Card dataclass gains `hook: Field | None` (text, section tag) and `key_numbers: list[tuple[str, str, str | None]]` (value, label, tag); `one_line` stays (template picks hook first).
  - `paper_detail` "about" lines gain one line per `checks` reason, prefixed `Left out: ` (e.g. `Left out: hook: 2.5x not found in §5`).

- [ ] **Step 1: Failing tests**: `test_paper_view_has_diagram`; `test_card_has_hook_and_key_numbers`; `test_card_without_hook_has_one_line_only`; `test_about_lists_checks`.
- [ ] **Step 2–4:** run (FAIL), implement, run full suite (PASS).
- [ ] **Step 5: Commit** `feat: library_api and cards carry hook, key numbers, diagram (Phase 9)` (docs in Task 8).

### Task 8: App pages and the SVG diagram

**Files:**
- Create: `src/research_pipeline/webapp/diagram_svg.py`
- Modify: `templates/_card.html`, `templates/paper.html`, `static/app.css`, `webapp/deps.py` (register `diagram_svg` as a template global or filter)
- Test: `tests/test_webapp_diagram_svg.py`, `tests/test_webapp_feed.py`, `tests/test_webapp_pages.py`
- Docs: `docs/guide/app.md`, `docs/guide/glossary.md` (SVG)

**Interfaces:**
- Consumes: `Diagram` (Task 1), card fields (Task 7).
- Produces: `diagram_svg.render(d: Diagram) -> markupsafe.Markup`: one `<figure class="diagram">` with an inline `<svg role="img" aria-label="{caption}">` and a `<figcaption>`; all text escaped; colours only via CSS classes using existing variables (`--accent`, `--line`, `--surface`, `--ink`), so light and dark both work.

Layouts (spec §3): `parts`: the node with the most outgoing edges (first on ties) on top in the accent style, the rest in a row below (wrap to two rows above 4), connector lines; `steps`: nodes top to bottom in edge order (unreached nodes appended), arrows between; `loop`: chips in order with arrows and a return arrow from last to first. Width 320 user units with `viewBox`, so it scales to the card; labels wrap to 2 lines by word.

Page placement (spec "Where it shows"):
- Card: the hook (with its tag) replaces the `one-line` paragraph when present; key numbers as `<ul class="key-numbers">` chips (`<li><strong>28%</strong> less time</li>`) right under it, outside `.read-more` (visible in Skim); the diagram inside `.read-more` after the fields.
- Paper screen: hook instead of the `lead` one-liner (fallback the same), key numbers under it, `<h2>How it works</h2>` + diagram before the brief.

- [ ] **Step 1: Failing tests**: `test_svg_parts_steps_loop_render` (each contains every label, `role="img"`, caption); `test_svg_escapes_labels` (`<b>` label appears escaped); `test_svg_draws_any_valid_graph` (cycle in `parts`, isolated node in `steps`: renders, all labels present); `test_card_shows_hook_instead_of_one_line`; `test_card_falls_back_to_one_line`; `test_key_numbers_visible_outside_read_more`; `test_paper_page_how_it_works_section`; `test_no_diagram_no_heading`.
- [ ] **Step 2–4:** run (FAIL), implement, run full suite (PASS). Restart the app service (`systemctl --user restart research-pipeline-app.service`) and fetch a paper page to confirm it renders.
- [ ] **Step 5: Docs + commit.** `app.md`: card and paper screen with the new parts, a text sketch, the SVG drawing and why (no JS library, checkable data, look A). `feat: hook, key numbers and diagram in the app (Phase 9)`

### Task 9: Text digest and `rpipe show`

**Files:**
- Modify: `src/research_pipeline/digest.py`, `src/research_pipeline/cli.py` (show, around line 1823)
- Test: `tests/test_digest.py`, `tests/test_cli_show.py`
- Docs: `docs/guide/digest.md`, `docs/guide/running.md` (or wherever `show` is documented)

- [ ] **Step 1: Failing tests**: `test_digest_shows_hook_line` (`**Hook:** … [§ 5.1 Efficiency]` above `**Summary:**`), `test_digest_without_hook_unchanged`, `test_show_prints_hook`.
- [ ] **Step 2–4:** run (FAIL), implement, run full suite (PASS).
- [ ] **Step 5: Docs + commit.** `feat: hook line in the digest and rpipe show (Phase 9)`

### Task 10: Gold-set check, spot check and finish

Runs on the owner's machine with the real model. **Takes about 30 to 60 minutes per eval run** (10 papers, plus judging); two runs. Use the real vault read-only for eval (eval writes only under `eval/`); no config left changed.

- [ ] **Step 1: Baseline** on `main` before merging (old prompts): `rpipe eval run` then `rpipe eval report`; save the report path.
- [ ] **Step 2: New run** on the branch with diagrams on: `rpipe model set-diagrams on`, `rpipe eval run`, `rpipe eval report` (it compares with the baseline). Then `rpipe model set-diagrams auto` (put the setting back).
- [ ] **Step 3: Pass rule** (`docs/guide/evaluation.md`): facts covered not lower, contradictions and unverified citations not higher (noise ±3). If it fails, adjust the plain-words text in `prompts.py`, re-run Step 2, and record what changed.
- [ ] **Step 4: Spot check** (Claude, not the owner): for 10 hooks from the new run, read the cited section text and mark each true / overstated / wrong; count key numbers kept and dropped, diagrams drawn, dropped (and why) and `null`, and the added time per paper. If more than half of non-null diagrams are dropped for labels, loosen the label rule to "at least one content word per label" (spec), re-test, note it in `summaries.md`.
- [ ] **Step 5: Record** the results in `docs/guide/summaries.md` (a short "Measured" paragraph, like the reading-in-parts one), update `ROADMAP.md` (Phase 9 merged, plan link), replace `handoff.md`'s state and next steps, set this plan's and the design's status to merged. Commit `docs: Phase 9 measured and merged`, merge to `main` with `--no-ff`, push.
