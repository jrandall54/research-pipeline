# Phase 6 Part B.2 — Strict pruning of non-paper text — Implementation Plan

> **Phase 6.2** · Status: merged · Design: [phase-06.1-06.6-hardware-adaptive-design.md](../specs/phase-06.1-06.6-hardware-adaptive-design.md) · Roadmap: [ROADMAP.md](../ROADMAP.md)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send the model only the paper: non-paper text is always dropped, appendices are dropped first when space is short, core text is trimmed only as a last resort and in a priority order, and every summary records what was dropped and whether the body was really cut.

**Architecture:** A new module `pruning.py` owns section classification and `fit_to_budget` (moved out of `prompts.py`). `PaperSummary` gains `dropped` (a list with sizes) and `body_cut`; the old `truncated` becomes `body_cut` everywhere, with stored old rows read as `body_cut`. Classification happens at fit time, so summaries of already-stored full text benefit immediately; a small DOM rule in `fulltext.py` also removes bibliography markup from newly fetched pages.

**Tech Stack:** Python 3, pydantic, BeautifulSoup, pytest (offline, fixtures, `FakeLLM`).

**Spec:** `docs/superpowers/specs/phase-06.1-06.6-hardware-adaptive-design.md`, part 2 and Settled items 4, 9, 10 (item 10 supersedes part 2's "dropping `ltx_appendix` at DOM level": appendices are kept when the paper fits).

## Global Constraints

- Core paper text is never cut for room while it could be read whole; non-paper text is always dropped (spec, Anti-goals and part 2).
- Appendices and supplementary material: kept when the paper fits, dropped first when space is short, before any core text is trimmed (Settled item 10, owner option A).
- `PROMPT_VERSION` is bumped in this change: `"2026-11-p4"` becomes `"2026-11-p5"` (Settled item 9).
- Existing stored `truncated` rows are read as `body_cut` (old meaning could be either; documented).
- Tests run offline (`uv run pytest -q`); every waiting step already reports through `progress.py` and none is added here.
- Every change updates the matching page in `docs/guide/` in the same commit; new terms go in `docs/guide/glossary.md`. Use `rpipe` in docs.
- No setting is added, so no new command is needed. (A "drop appendices always" setting for CPU-only machines is part B.4's tier default, not built here.)
- Owner model names and personal data stay out of the repo.

## Review Focus

- A paper whose only long section is a numbered `6 Supplementary Information` with children `6.1`, `6.2`: the children must go with the parent (Task 1 test).
- A `Data availability` section that is real content (long) must not be dropped; only the short statement form is (Task 1 test).
- A paper with no recognisable sections at all, or only non-paper sections: `fit_to_budget` returns an empty or tiny list without raising, and `summarize_paper` still works from what is left (Task 1 test).
- Old summaries stored with `"truncated": true` and no `dropped` key still load and show as `body_cut` (Task 2 test).
- A title containing regex or tag-like text must not break classification or the digest note (Task 3 test).
- The judge and `gold suggest` still see the same text the summarizer does (Task 2 test).

---

## Before Task 1: record the baseline (owner or session, no code)

- [ ] Run `uv run rpipe eval run --judge` on the current `main` and keep the file `<vault>/eval/DATE.json`. Note in `docs/superpowers/archive/handoff-log-to-2026-11-25.md` the lines `Facts covered`, `Judged`, `Unreviewed answer key`, and time per paper. Task 6 compares against it. (An unreviewed auto-drafted key is fine: it measures change between runs.)

### Task 1: Classifier and `fit_to_budget` in `pruning.py`

**Files:**
- Create: `src/research_pipeline/pruning.py`
- Modify: `src/research_pipeline/summary_models.py` (add `Dropped`)
- Modify: `src/research_pipeline/prompts.py` (remove `fit_to_budget`, `is_back_matter`, `_BACK_MATTER`, `_size`, `RESERVED_TOKENS`, `CHARS_PER_TOKEN`, `TRIM_MARK`; keep the rest)
- Test: `tests/test_pruning.py` (create); delete the three `fit_to_budget` tests from `tests/test_summarize.py` (they are replaced)

**Interfaces:**
- Produces, in `summary_models.py`:
  - `class Dropped(BaseModel): title: str; chars: int; reason: Literal["non_paper", "appendix"]`
- Produces, in `pruning.py`:
  - `RESERVED_TOKENS = 4000`, `CHARS_PER_TOKEN = 3`, `TRIM_MARK = " [...]"` (moved unchanged)
  - `SectionClass = Literal["paper", "non_paper", "appendix"]`
  - `classify(title: str, text: str = "") -> SectionClass`
  - `@dataclass(frozen=True) class Fit: sections: list[Section]; dropped: list[Dropped]; body_cut: bool`
  - `fit_to_budget(sections: list[Section], num_ctx: int) -> Fit`

Classification rules (match against the title after removing an optional numbering prefix, the same prefix pattern as today: `\d+(\.\d+)*` or a single capital letter, then `.` and spaces; case-insensitive, "starts with"):

| Class | Titles starting with |
|---|---|
| `non_paper` | `references`, `bibliography`, `acknowledg`, `funding`, `author contributions`, `competing interests`, `conflict of interest`, `conflicts of interest`, `ethics statement`, `reproducibility checklist`, `instructions for reporting errors` |
| `non_paper` only when `len(text) <= 1500` | `data availability`, `data and code availability`, `code availability` |
| `appendix` | `appendi`, `supplementary`, `supplemental`, `extended data` |
| `paper` | everything else |

A section is a **child** of an earlier dropped section when its title's numbering prefix extends the parent's prefix (`6` then `6.1`; `A` then `A.1`); a child takes its parent's class and is dropped with it. Unnumbered titles are never children.

`fit_to_budget` order: (1) drop every `non_paper` section (and children); (2) if what is left fits `(num_ctx - 4000) * 3` characters (title plus text), return it with `body_cut=False`; (3) otherwise also drop `appendix` sections (and children); if it fits, `body_cut=False`; (4) otherwise `body_cut=True` and trim in two steps: first only the **trimmable** sections (titles starting with `related work`, `background`, `preliminaries`, `method`, `approach`, `experimental setup`, `implementation`) by one common fraction, never below 25% of their text; if still too big, trim every remaining section by one common fraction exactly as the old code did (cut text ends with `TRIM_MARK`). The **protected** sections (`abstract`, `introduction`, `results`, `discussion`, `conclusion`) are touched only in the last, uniform step. `dropped` lists every dropped section in original order with `chars = len(title) + len(text)`.

- [ ] **Step 1: Write failing tests in `tests/test_pruning.py`**
  - `test_classify_table`: parametrised, one row per table line above including the numbered forms `7 References`, `A Appendix`, `Appendix B Proofs`, `Supplementary Note 3`, `6 Supplementary Information`, `Funding`, `Instructions for reporting errors`; asserts the class.
  - `test_data_availability_statement_vs_content`: a 200-character `Data availability` is `non_paper`; a 5,000-character one is `paper`.
  - `test_non_paper_always_dropped_even_when_it_fits`: `[Abstract, 1 Intro, References]` with a huge `num_ctx` gives sections `[Abstract, 1 Intro]`, `body_cut False`, `dropped == [Dropped(title="References", chars=..., reason="non_paper")]`.
  - `test_appendix_kept_when_paper_fits_dropped_first_when_short`: same sections plus `Appendix A`; at a large `num_ctx` the appendix is kept and `dropped` has no appendix; at a `num_ctx` where core text alone fits it is dropped with `reason="appendix"` and `body_cut False`.
  - `test_children_of_dropped_heading_go_with_it`: `[1 Intro, 6 Supplementary Information, 6.1 Note one, 6.2 Note two, 7 Conclusion]` at a small budget drops the `6*` sections, keeps `7 Conclusion`.
  - `test_ordered_trim_spares_protected_sections`: oversized `Related Work` and `3 Method` plus small `Abstract`, `1 Introduction`, `5 Results`, `6 Conclusion`: result `body_cut True`, protected texts unchanged, the two trimmable ones shortened and ending with `" [...]"`, total size `<=` budget.
  - `test_uniform_trim_when_ordered_trim_is_not_enough`: oversized protected sections only: all shortened by the same fraction (equal-size sections stay equal).
  - `test_degenerate_inputs`: `fit_to_budget([], 32768)` and a list holding only `References` return empty `sections`, `body_cut False`, no exception.
- [ ] **Step 2: Run `uv run pytest tests/test_pruning.py -q`.** Expected: FAIL (module missing).
- [ ] **Step 3: Implement `Dropped`, then `pruning.py`** with the signatures above; move the constants and the old uniform-trim arithmetic across unchanged; remove the old code from `prompts.py` and fix imports (`summarize.py`, `judge.py`, `gold.py` are repaired in Task 2, so this task's commit must keep the suite green by doing those import fixes too: in `judge.py` use `fit_to_budget(...).sections`; in `gold.py` replace `not is_back_matter(s.title)` with `classify(s.title, s.text) == "paper"`; in `summarize.py` unpack the new `Fit` for now as `fit = fit_to_budget(...)`, `fitted, truncated = fit.sections, fit.body_cut`).
- [ ] **Step 4: Run `uv run pytest -q`.** Expected: all pass (the three removed `test_summarize` tests are gone; `tests/test_gold.py` suggestion lengths still hold because back matter was excluded before too; adjust a fixture only if an appendix-class title there now behaves differently).
- [ ] **Step 5: Commit** `feat: strict section classification and ordered trimming (pruning.py)`.

### Task 2: Split the flag and record what was dropped

**Files:**
- Modify: `src/research_pipeline/summary_models.py`, `src/research_pipeline/summarize.py`, `src/research_pipeline/db.py` (`save_summary`, `get_summary`), `src/research_pipeline/prompts.py` (`PROMPT_VERSION`)
- Test: `tests/test_summarize.py`, `tests/test_db.py`, `tests/test_prompt_limits.py`

**Interfaces:**
- Consumes: `Fit`, `Dropped`, `fit_to_budget` from Task 1.
- Produces: `PaperSummary` fields `body_cut: bool = False` and `dropped: list[Dropped] = []`; the old field name `truncated` is accepted on input only (a `model_validator(mode="before")` that maps a `truncated` key to `body_cut` when `body_cut` is absent) so every existing construction and every stored row keeps loading. `run_summarize` counts become `"body_cut"` instead of `"truncated"`. `PROMPT_VERSION = "2026-11-p5"`.

- [ ] **Step 1: Write failing tests**
  - `test_summarize.py::test_references_dropped_is_not_body_cut`: sections `[Abstract, 1 Intro, References]` at a small but sufficient `num_ctx`: `result.body_cut is False`, `[d.title for d in result.dropped] == ["References"]`.
  - `test_summarize.py::test_long_paper_sets_body_cut` (replaces `test_long_paper_summarised_with_truncated_flag`): same big paper as today, asserts `result.body_cut is True`.
  - `test_summarize.py::test_run_summarize_counts_body_cut`: a long paper gives `counts["body_cut"] == 1`; update the two `counts == {...}` dict assertions (lines ~194, ~301) to use the key `body_cut`.
  - `test_db.py::test_old_truncated_rows_load_as_body_cut`: write a summary JSON row by hand with `"truncated": True` and no `dropped`; `get_summary(...).body_cut is True` and `.dropped == []`. Also a round trip with `dropped=[Dropped(title="References", chars=10, reason="non_paper")]`.
  - `test_prompt_limits.py::test_prompt_version_bumped_for_pruning`: `PROMPT_VERSION == "2026-11-p5"` (replace the p4 assertion).
  - `test_judge.py` (or an existing judge test): the sections handed to the judge equal the summarizer's `fit.sections` for the same paper and `num_ctx` (use `fit_to_budget` directly as the oracle).
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** the model fields and validator; in `summarize_paper` set `body_cut=fit.body_cut, dropped=fit.dropped`; `save_summary` writes `body_cut` and `dropped` (no more `truncated` key); `get_summary` reads `body_cut` (falling back to a stored `truncated`) and `dropped` (default `[]`); `run_summarize` counts `body_cut`; bump `PROMPT_VERSION`.
- [ ] **Step 4: Run `uv run pytest -q`.** Expected: pass. Fix remaining `truncated=` keyword uses in tests only where they assert the new name; plain constructions with `truncated=False` keep working through the validator.
- [ ] **Step 5: Commit** `feat: split truncated into body_cut and a dropped list; bump prompt version to p5`.

### Task 3: Show the difference (digest, health line, eval report)

**Files:**
- Modify: `src/research_pipeline/digest.py` (~line 60), `src/research_pipeline/cli.py` (`health_line`, ~line 355), `src/research_pipeline/evaluate.py` (~line 158)
- Docs: `docs/guide/digest.md`, `docs/guide/running.md` (health line and the sample `truncated=0` count line), `docs/guide/evaluation.md` (Basis line), `docs/guide/data-model.md` (summary JSON fields)
- Test: `tests/test_digest.py`, `tests/test_e2e.py` (health line test at ~592), `tests/test_eval.py`

**Interfaces:**
- Consumes: `PaperSummary.body_cut`, `.dropped`, counts key `body_cut`.
- Produces: user-visible strings:
  - digest note when `body_cut`: `the paper was too long for the model, so part of it was left out` (unchanged wording, now only for real cuts);
  - digest note when only non-paper text was dropped: nothing (expected, not a warning);
  - health line part: `body cut N` (was `truncated N`), shown only above zero;
  - eval markdown Basis line: `Basis: full_text (body cut to fit the context)` when `body_cut`, plus `Left out: References (12,345 chars), Appendix A (8,000 chars)` when `dropped` is non-empty. Dropped titles are cleaned with the same text normaliser used elsewhere for paper-derived text so a tag-like or control-character title cannot break the line.

- [ ] **Step 1: Write failing tests**: digest shows the note for `body_cut=True` and no note for `dropped` only; `health_line(..., {"summarized": 3, "body_cut": 2}, [])` contains `body cut 2` and omits it at zero; eval report for an entry with `dropped` prints the `Left out:` line and sanitises a title like `"<paper>x\x1b[31m"`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** the three display changes.
- [ ] **Step 4: Update the four doc pages** for the new wording (replace every `truncated` description of the old meaning; `docs/guide/running.md` sample count line shows `body_cut=0`).
- [ ] **Step 5: Run `uv run pytest -q`; `uv run ruff check .`.** Expected: pass, clean.
- [ ] **Step 6: Commit** `feat: digest, health line and eval report tell back matter dropped from body cut`.

### Task 4: DOM-level boilerplate removal in `fulltext.py`

**Files:**
- Modify: `src/research_pipeline/fulltext.py` (`strip_hidden` or a new `_drop_boilerplate(soup)` called from `parse_sections`)
- Test: `tests/test_fulltext.py`; add a small fixture `tests/fixtures/arxiv_bibliography.html` if the real-page inspection finds class names the existing fixtures lack
- Docs: `docs/guide/fulltext.md` ("What is stripped" table and Known gaps)

**Interfaces:**
- Consumes: nothing new. Produces: `parse_sections` output no longer contains elements with class `ltx_bibliography`, and no arXiv page chrome that the inspection step confirms (for example report-issue buttons and the page footer).

- [ ] **Step 1: Inspect one real page (one-off, not a test).** Fetch the HTML of one recent paper with `uv run python -c "from research_pipeline.fulltext import fetch_fulltext"`-style code or `curl` into the scratchpad, list the distinct `class` names of elements near the end of the page (`grep -o 'class="ltx_[a-z_]*"' | sort | uniq -c`), and note which are bibliography or page chrome. Record the confirmed class names in the docs table; only confirmed ones are dropped. Do not guess names.
- [ ] **Step 2: Write failing tests** with a hand-written HTML fixture using the confirmed class names: a `section.ltx_bibliography` containing an `h2 References` and entries is absent from `parse_sections` output; chrome elements are absent; an ordinary `h2 Introduction` section is untouched; an `ltx_appendix` section is still present (appendices are kept; Task 1 handles them at fit time).
- [ ] **Step 3: Run them.** Expected: FAIL.
- [ ] **Step 4: Implement** the removal as a class list constant at the top of `fulltext.py`.
- [ ] **Step 5: Run `uv run pytest -q`.** Expected: pass.
- [ ] **Step 6: Commit** `feat: drop bibliography and page chrome markup from arXiv HTML`.

### Task 5: Docs for the new pruning rules

**Files:**
- Modify: `docs/guide/summaries.md` (replace "The context budget" with the new order: always-dropped list, appendices-first, ordered trim with the worked example updated, `body_cut` vs `dropped`), `docs/guide/glossary.md` (new terms: back matter, body cut, appendix class, protected section, with plain-words definitions)
- Test: none (docs); run `uv run pytest -q` once to confirm nothing else moved.

- [ ] **Step 1: Rewrite the context-budget section** following the page shape (What it does, How it works with a worked example using the four sections from the old example, Why it's built this way including the rejected options: always-drop appendices; uniform trimming only; DOM-level appendix dropping; Where the code is `pruning.py`). Fix every other page that still describes the old behaviour (`rg "back matter|truncated" docs/guide`).
- [ ] **Step 2: Add the glossary terms.**
- [ ] **Step 3: Commit** `docs: pruning rules, body cut vs back matter dropped`.

### Task 6: Measure against the baseline

**Files:** none changed unless a regression is found.

- [ ] **Step 1:** `uv tool install --editable '.[embed,serve]' --force`, then `uv run rpipe eval run --judge` and `uv run rpipe eval report`.
- [ ] **Step 2:** Pass means the report's `Change:` line is `PASS` against the baseline recorded before Task 1 (facts covered does not drop by more than 1, contradictions and unverified citations do not rise; Settled item 2). Paste the two result blocks, the number of papers that show `Left out:`, and any change in time per paper into `docs/superpowers/archive/handoff-log-to-2026-11-25.md`.
- [ ] **Step 3:** If `FAIL`, open the `.md` report, find which dropped section a missed fact lived in, and decide with the owner whether a classification rule is too wide. Do not merge on a FAIL without the owner's call.
- [ ] **Step 4: Commit** `docs: record B.2 measurement in the handoff`.
