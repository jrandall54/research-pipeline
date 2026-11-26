# Phase 10.1 — Clearer first-time setup: Implementation Plan

> **Phase 10.1** · Status: merged · Design: none (small phase; owner request 2026-11-26 after the start-to-finish walkthrough) · Roadmap: [ROADMAP.md](../ROADMAP.md)

**Goal:** Someone new to rpipe (and to arXiv) can get through `rpipe init` knowing, at every step, what is being asked and why. The categories step becomes a numbered menu with plain descriptions, so nobody needs to know arXiv codes.

**Owner's words:** "someone may not be familiar with arXiv categories, so maybe a little more guidance in that section to make the options clear … explaining what a seed paper is and just every step in general having some little explainer of what is needed from the user and the why."

**Architecture:** Copy and one input change in the `rpipe init` wizard (`cli.py`: `_wizard`, `_wizard_backend` intro, `_wizard_describe`, `_wizard_topics`, `_wizard_categories`, `_wizard_seeds`, the non-terminal "Next steps" in `init`). One new data list in `categories.py` (shared). The README "First run" section is updated to match.

## Global constraints

- Tests offline (`uv run pytest -q`), ruff clean. Existing wizard tests keep passing except where they assert old wording; update those to the new copy.
- Every explainer is short: at most about 4 lines on an 80-column terminal, plain words, no jargon without a gloss.
- Nothing else about the wizard's behaviour changes: same order, same saves-as-you-go, same Ctrl-C message.
- `rpipe profile set-categories` keeps accepting codes and `all` exactly as today.
- Docs in the same commit: `docs/guide/profile.md` ("The guided `rpipe init`"), `docs/guide/vault-and-config.md` (sample session), `README.md` ("First run": install line with the `app` extra; steps init → run → schedule → app → share, each with one line of why).

## The copy (exact; the builder may wrap lines but not reword)

**Opening (before step 1, terminal only):**
```
Setup has 5 short steps. Each answer is saved as you go: Ctrl-C stops, and running
rpipe init again carries on. You can change anything later with rpipe profile.
```

**Step headers:** each step starts with a blank line and `Step N of 5: <name>` — names: `Summaries`, `Describe your research (optional)`, `Topics`, `arXiv categories`, `Seed papers`.

**Step 1, Summaries (printed before the machine scan):**
```
rpipe reads the papers it picks for you and writes a summary of each. That needs a
language model: one on this computer (Ollama: private; best with a graphics card),
Claude through your Claude Code login (no graphics card needed; paper text is sent
to Anthropic), or none (you still get ranked papers, with their abstracts).
I'll check this computer and recommend one.
```

**Step 2, Describe (before the prompt; only when a model is set up):**
```
A shortcut: describe what you research or want to follow, in a few sentences, and the
model proposes topics and seed papers for you to accept or reject one by one.
Example: "I study security of LLM agents, especially prompt injection, and I teach an
intro AI course." Press Enter to skip and add them yourself.
```

**Step 3, Topics (fresh profile; replaces the current one-line intro):**
```
Topics are short phrases for what you want to read about. Every new paper is compared
with each topic, and the closest matches rank highest. Specific phrases work better
than broad fields: "prompt injection attacks on LLM agents" beats "security".
5 to 15 topics is plenty; near-duplicates add nothing, so use one per idea.
```

**Step 4, arXiv categories (replaces `CATEGORY_HELP` and the "Press Enter…" paragraph):**
```
arXiv (arxiv.org) is the free site where most computer-science and AI research appears
first. Every paper is filed under subject categories, and rpipe only collects papers
from the categories you pick here; your topics then rank what was collected. Pick the
areas your topics fall in. More categories means more papers to sort (slower, a bit
noisier); too few means good papers are never seen.
```
then the menu (see below), then:
```
Type the numbers you want, separated by commas (e.g. 1,2,5), or arXiv codes, or all
for every computer-science category. Press Enter to keep the ticked ones.
Change it any time with rpipe profile set-categories. Full list: https://arxiv.org/category_taxonomy
```
Prompt: `Categories` with the current selection as the default (shown as numbers/codes as today).

**Step 5, Seed papers (replaces the current one-line intro):**
```
A seed paper is an example of exactly the kind of paper you want more of. You don't
need to have read it; it just has to be on target. New papers are compared with each
seed's title and abstract, which says far more than a short topic, so seeds are the
strongest way to sharpen your feed. Paste an arXiv link, its id (e.g. 2307.15043) or
its title; I look it up and you confirm. 3 to 10 is a good start.
```
The closing tip (fewer than 3 seeds) becomes: `Tip: add more any time with rpipe profile add-seed. "More like this" in the app also nudges ranking, but fades over weeks; seeds don't.`

**End (after the profile is shown; replaces "Next step: rpipe run"):**
```
All set. Next:
  rpipe run               collect today's papers, rank them and summarize the top ones
                          (the first run can take an hour or more with a local model)
  rpipe schedule install  do that every night by itself
  rpipe app install       read your feed on your phone and desktop
  rpipe share setup       optional: publish your feed so others can follow it
```
The non-terminal `init` "Next steps" list gets the same five lines (model setup first, then add-seed, then these).

## The categories menu

`categories.COMMON: list[tuple[str, str]]` (code, plain description), in this order:

| # | Code | Shown as |
|---|---|---|
| 1 | cs.AI | Artificial intelligence (general) |
| 2 | cs.LG | Machine learning |
| 3 | cs.CL | Language and LLMs |
| 4 | cs.CV | Computer vision (images, video) |
| 5 | cs.CR | Security and cryptography |
| 6 | cs.SE | Software engineering |
| 7 | cs.MA | Multi-agent systems |
| 8 | cs.HC | Human-computer interaction |
| 9 | cs.CY | Computers and society (ethics, policy, education) |
| 10 | cs.IR | Search and recommendation |
| 11 | cs.RO | Robotics |
| 12 | cs.DC | Distributed and parallel computing |
| 13 | cs.OS | Operating systems |
| 14 | cs.NI | Networking |
| 15 | cs.PL | Programming languages |
| 16 | cs.DB | Databases |
| 17 | stat.ML | Statistical machine learning |

Printed as `  [x]  1. cs.AI   Artificial intelligence (general)` (ticked = in the current selection, `[ ]` otherwise; codes aligned). Current categories not in the list are printed after it as `Also selected: cs.XX, …` so nothing is hidden.

Input rules (`categories.parse_choice(text: str, current: list[str]) -> list[str]`, raises `ValueError` with a plain message):
- Blank → `current` unchanged.
- `all` (any case) → `["all"]`.
- Otherwise comma-separated items; each is either a menu number (1–17) or an arXiv code (case as typed; validated later by `set_categories` as today). The result **replaces** the selection (not adds), in the order typed, duplicates removed.
- A number outside 1–17 → `ValueError("There is no option 18. Pick numbers from 1 to 17, or type arXiv codes.")`; the wizard prints `Skipped: …` and keeps the current categories (same pattern as today's invalid code).

## Tasks

### Task 1: categories menu, parsing, and all the step explainers

**Files:** `src/research_pipeline/categories.py`, `src/research_pipeline/cli.py`, `tests/test_categories.py`, `tests/test_init_wizard.py`, `README.md`, `docs/guide/profile.md`, `docs/guide/vault-and-config.md`, `docs/guide/glossary.md` (add "arXiv" and "seed paper" if missing).

- [ ] Tests first:
  - `test_common_categories_are_real` (every code in `COMMON` is in `known_categories()`; 17 entries; no duplicates).
  - `test_parse_choice` (blank → current; `"all"`/`"ALL"` → `["all"]`; `"1,3"` → `["cs.AI","cs.CL"]`; `"2, cs.CR, 2"` → `["cs.LG","cs.CR"]`; `"18"` → ValueError with the exact message).
  - `test_wizard_shows_step_headers_and_explainers` (output contains `Setup has 5 short steps`, `Step 1 of 5: Summaries` … `Step 5 of 5: Seed papers` in order, `arxiv.org`, `A seed paper is an example`).
  - `test_wizard_category_menu_ticks_current` (default categories show `[x]` on cs.LG and `[ ]` on cs.CV).
  - `test_wizard_categories_by_number` (answer `3,5` → config `["cs.CL","cs.CR"]`).
  - `test_wizard_bad_category_number_keeps_current`.
  - `test_wizard_ends_with_next_steps` (all four commands in the end block).
  - Update the existing category-explainer and default-accept tests to the new copy and prompt.
- [ ] Implement; run `uv run pytest -q tests/test_categories.py tests/test_init_wizard.py`, then the full suite and ruff.
- [ ] Docs as listed in Global constraints. README "First run" must use `uv tool install "research-pipeline[embed,app] @ git+https://github.com/jrandall54/research-pipeline.git"` and list init → run → schedule install (+ `loginctl enable-linger $USER`) → app install → share setup, one line of why each; keep the macOS/Windows scheduler note; replace the old `--editable` app line in "Daily use" with a pointer to `rpipe app install`.
- [ ] Commit `feat: clearer rpipe init, every step explained, categories as a menu (Phase 10.1)` with the ROADMAP row added (Phase 10.1, merged at merge time, plan link) and this plan's status.
