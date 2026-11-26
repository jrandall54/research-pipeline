# Phase 9 — Hooks, key numbers, plain words and diagrams (design)

> **Phase 9** · Status: merged · Plan: [phase-09-hooks-and-diagrams.md](../plans/phase-09-hooks-and-diagrams.md) · Roadmap: [ROADMAP.md](../ROADMAP.md)

Date: 2026-11-25. Mockups: the "Research Feed — App Design" canvas (https://claude.ai/artifact/JnycnAXpyjtqyybVmv9xMK), look A (the robot-agents card and paper screen show the diagram).

## Intent

The feed works, but reading it is still work. Titles are written for other researchers, and the summary fields are long (270 to 390 characters each) and full of the paper's jargon, for example "task-balanced mean elapsed time to 13.67 minutes and a higher physical-progress AUC of 72.9". This phase makes each paper **digestible at a glance**:

- a **hook**: one line that crystallizes the paper's real finding;
- up to three **key numbers**;
- the summary fields rewritten in **plain words**;
- for papers whose method has a shape, a small **diagram** of how it works.

Everything stays true to the paper. In the owner's words: a hook "is not clickbait. Clickbait is exaggerating or misleading; hooks in our use case are merely crystallizing the truth of the article in a way that's digestible."

## Goals

- A card reads: topic, real title, **hook**, key numbers, why you're seeing this. Read mode adds the plain-words fields and the diagram.
- The paper screen shows the hook under the title, the key numbers, a "How it works" diagram, then the plain-words brief and critique.
- Every new piece points to the section it came from, and code checks it against the paper before it is shown (owner rule, 2026-11-25).
- It works on every machine: diagrams can be switched off with an `rpipe` command, and are off by default where the model runs on the CPU only.

## Non-goals

- Videos (Phase 12), publishing (Phase 10).
- Rewriting summaries already stored. Papers summarized before this phase keep their one-line summary and long fields; only new summaries (nightly or "Summarize this") get the new parts. A re-summarize command is not added here.
- Picture-style illustrations from an image generator.

## Anti-goals

- **Exaggeration or misleading lines.** A hook never claims more than the paper, never teases ("you won't believe…"), never asks a question and never holds back the answer.
- **Anything shown that failed its check.** A failed piece is left out quietly and listed under "About this summary". Nothing is shown with a warning badge instead.
- **Plain words that lose facts.** The gold-set evaluation must pass before this ships.
- **Technical noise on the card.** Check results live only in "About this summary".

## Constraints

- The app never runs a model. All generation happens in the nightly run or through the request list (Phase 7).
- The app stays in the app group: it reads the new parts through `library_api` only (`tests/test_architecture.py`).
- No third-party requests from pages; no new JavaScript library.
- Works with Ollama models of every size and with the Claude Code backend; tests run offline with the fake model.
- Every user setting has an `rpipe` command; every change updates `docs/guide/` in the same commit; every waiting step reports progress.
- No database schema change (new parts go in the summary JSON, and the diagram in a new `kind` of `summaries` row).

## Approaches

| | Approach | Trade-off | Verdict |
|---|---|---|---|
| **A** | Hook, key numbers and plain-words fields come from the **existing summary call** (a few more fields in its answer). The diagram is **one extra call** per paper, after the critique, and can be switched off. The model answers the diagram as data (boxes and arrows); the app draws it as SVG itself. | Hook and numbers cost almost nothing extra. The diagram costs one call, which is real time on a CPU-only machine, hence the switch. Drawing our own SVG keeps the look of the mockups and lets code check every box. | **Chosen** (Claude's pick; owner: "everything looks good"). |
| B | Everything, diagram included, in the summary call. | No extra call, but one much larger answer; small local models get worse at long, many-field JSON, and a broken diagram would then fail the whole summary. | Rejected. |
| C | The model writes Mermaid diagram code; the app draws it with the Mermaid script. | Familiar format, but adds a large JavaScript library to the pages, and its free-form text is hard to check box by box. | Rejected. |

## How it works

### 1. The summary call writes three new things (all new summaries)

The summary schema (`Summary` in `summary_models.py`) gains, both optional so old rows still load:

- `hook`: a `Claim` (text plus cited sections). One line, at most about 90 characters, saying what the paper found or showed, in plain words.
- `key_numbers`: a list of 0 to 3 items, each `value` (as written, e.g. `28%`), `label` (what it measures, plain words, a few words) and `sections`.
- The existing `problem`, `approach`, `results`, `limitations` stay, but the prompt now asks for plain words: short sentences, no unexplained jargon, terms the reader might not know replaced or explained in a few words, numbers kept. Each field at most two short sentences (Phase 8.1 shows two sentences as two points).

`one_line` is still written. It stays the fallback when the hook fails, and the text digest, `rpipe show` and the judge keep using it.

The critique prompt gets the same plain-words instruction.

Hook rules written into the prompt (owner's definition, Claude's wording):

- State the main finding or result as a fact, using the paper's own numbers if it has them.
- Never claim more than the paper shows (no "solves", "revolutionizes", no rounding 28% to "a third").
- No questions, teasers or held-back answers.
- Cite the section it comes from.

`PROMPT_VERSION` is bumped.

### 2. Code checks them

The citation check that already exists (`citations.py`) covers the hook and each key number like any other claim: an unknown section title gets the usual one retry, then the piece is marked unverified. On top of that, a **number check** (new, `numbers.py` or inside `citations.py`, builder's choice):

- Every number in the hook text (digits, decimals, percentages, multipliers like `1.37x`) must appear in the text of a section the hook cites. The stored full text (`fulltexts` table) is used, so it works for papers read whole and read in parts.
- A key number's `value` must appear the same way in its cited sections.
- Matching ignores spacing and formatting differences (`28 %`, `28\%`, `28%` match; `1.37x` and `1.37×` match). A number rounded to fewer decimals than the paper's also matches (`28%` matches `28.3%`), since that is not exaggeration. A number the paper never writes (for example "twice as fast" worked out from 13.8 and 26.0) does not match.
- Written-out small numbers (`three agents`) are not checked as numbers. Claude's call: they are rarely the claim, and parsing them adds error.

Results:

| Piece | Check fails | Shown instead |
|---|---|---|
| Hook | bad citation after retry, or a number not found | the one-line summary, as today |
| A key number | bad citation, or value not found | that number is left out; the others stay |
| Whole summary | (unchanged) | (unchanged) |

Each failure is stored (a list of short plain reasons on `PaperSummary`, e.g. "hook: 2.5x not found in §5") and printed under "About this summary". Nothing else changes for the existing `unverified` list, so the eval counts keep their meaning.

What code cannot check: whether a hook *overstates* in words alone ("solves robot control"). That rests on the prompt rules and the gold-set evaluation, and the docs say so plainly.

### 3. The diagram call (optional, one more call)

After the critique, one more call asks for a diagram of how the method works, as data:

- `shape`: `parts` (one thing coordinating several parts, like the robot-agents mockup), `steps` (a pipeline, A then B then C) or `loop` (steps that repeat).
- `nodes`: 2 to 8 boxes, each `label` (at most about 4 words), optional `detail` (a few words) and `sections`.
- `edges`: arrows between node ids, each with an optional short label.
- `caption`: one plain sentence.
- or `null` when the method has no clear shape (a survey, a theory paper). That is a normal answer, not a failure.

Checks (code): ids valid and edges point to real nodes; node count in range; every node cites a section that exists; each node label's main words appear in the paper's text (ignoring case and small words), so the model cannot invent a component. If any check fails, the diagram is dropped and the reason goes in "About this summary". Claude's call: one retry with the error, like citations, before dropping.

Stored as a `summaries` row with `kind = 'diagram'` (the column exists, no schema change), so it can be added or dropped without touching the summary.

**Drawing:** the app turns the data into inline SVG on the server (pure Python in the app group), in look A's style: rounded boxes, the accent colour for the coordinating box, grey connector lines, the caption below. Three layouts, one per shape: `parts` = one box above a row of boxes; `steps` = boxes top to bottom with arrows (fits a phone); `loop` = chips with arrows and a return arrow. Light and dark themes come from the existing CSS variables. No JavaScript.

**The switch:** a new setting, `diagrams`: `auto` (default), `on`, `off`, set with an `rpipe` command (name left to the builder, next to `rpipe model set-mode`, and shown by `rpipe model show`). `auto` means on, except when the model runs entirely on the CPU (the same GPU check the reading-mode choice already makes). The diagram call reports progress like the other calls (`diagram` phase).

### Where it shows

```
Card, Skim                          Card, Read (adds)
──────────────────────────          ──────────────────────────
AGENT-BASED DEVELOPMENT             The problem / What they did /
ACAP: Asynchronous Coding           What they found (plain words,
Agents for Efficient Robot Control  as points)
One robot, many coding agents:      [diagram]
it finishes 28% sooner     §5.1
 28% less time · 25 tasks
Why you're seeing this: ...
[More] [Less] [Save] Open
```

- **Card:** the hook replaces the one-line summary line under the title (owner, 2026-11-25). If there is no hook (old summary or failed check), the one-line summary shows there, as today. Key numbers show as a small row of chips under it, in Skim and Read (Claude's call: they are the most digestible part). The diagram shows in Read mode (as in the mockup).
- **Paper screen:** the hook under the title (instead of the one-liner, same fallback), key numbers, a "How it works" section with the diagram, then the brief and critique as today.
- **Text digest and `rpipe show`:** gain the hook line when present (Claude's call; small change, keeps them in step with the app). No diagram there.
- **`library_api`:** exposes hook, key numbers, diagram data and the check reasons on the paper view; exact names are the builder's.

## Checking it is good (before merging)

- Unit tests with the fake model: schema round-trip (old rows load), number matching (rounding, formats, misses), hook fallback, key numbers dropped one by one, diagram checks (bad ids, invented labels, `null`), retry, progress phases, the setting command (valid, invalid, shown by `model show`), SVG drawing for the three shapes, app pages with and without the new parts.
- **Gold-set evaluation** (`rpipe eval run`, see `docs/guide/evaluation.md`), before and after the prompt change, on the owner's machine: the pass rule is the existing one (facts covered do not drop, contradictions and unverified citations do not rise). If plain words lose facts, the prompt is adjusted before merging. Also report how many hooks and key numbers passed their checks, and how many diagrams were drawn.
- Time cost: the eval report states the added time per paper (the diagram call), so the CPU-only default can be checked against it.

## Docs

`docs/guide/summaries.md` (new fields, plain-words prompt, number check, diagram call, what code can and cannot check), `docs/guide/app.md` (hook, numbers, diagram on card and paper screen; the SVG drawing), `docs/guide/digest.md` (hook line), `docs/guide/llm.md` or `hardware.md` (the `diagrams` setting and its `auto` rule), `docs/guide/data-model.md` (new JSON fields, `diagram` rows), `docs/guide/glossary.md` (hook, key number, SVG).

## Decided by Claude (owner may override)

- Hook at most about 90 characters; key numbers at most 3; diagram 2 to 8 boxes.
- Key numbers visible in Skim mode too.
- Rounded numbers pass the check; numbers worked out by the model fail it.
- Written-out numbers are not checked.
- Diagram default `auto` (off on CPU-only); one retry before dropping a diagram.
- Hook added to the text digest and `rpipe show`.
- No re-summarize of old papers in this phase.

## Settled after the builder check (from the code, or Claude's calls)

- **Retries.** `generate_checked` (`citations.py`) re-asks the whole call once when any claim cites an unknown section. The hook and key numbers are claims inside the summary, so they share that one retry; no new retry is added for the summary call. A failed *number* check does not trigger a retry (Claude's call: a second whole summary call for one line is not worth it); the hook falls back and the key number is dropped.
- **Which text a number is checked against.** The stored sections (`db.get_fulltext`) carry their titles, so a cited title is matched to its section with the same `norm_title` the citation check uses. An abstract-only paper's one section is "Abstract", checked against the abstract text. If a cited section's text cannot be found, a hook or key number *with a number in it* fails (strict); a hook with no number only needs its citation to be valid.
- **Number matching is by the number itself, not units.** Digits are compared after removing spaces, thousands separators and formatting (`28 %`, `28\%`, `1.37×`). Rounding to fewer decimals of the same number passes (`28%` for `28.3%`, `1.4x` for `1.37x`, `13.7` for `13.67`). Unit or form changes fail (`0.28` for `28%`, `2 s` for `2000 ms`). Strict on purpose: a failure only costs the hook, never the summary.
- **Diagram labels.** Each node label's content words (ignoring case, small words and simple endings like plural `-s`, `-ing`, `-ed`) must each appear in the paper text. A label the paper never uses ("Coordinator" when the paper says "orchestrator") fails; the diagram prompt tells the model to use the paper's own names for things. If the eval shows more than half of the diagrams dropped for labels, loosen to "at least one content word per label" before merging, and say so in the docs.
- **`auto` for diagrams** follows `resolve_mode`'s inputs: any backend other than Ollama is on; Ollama with an unknown GPU share is on; GPU share 0.0 (CPU only) is off; otherwise on. It is decided at each run, like the reading mode, and the progress note says why.
- **Old papers and diagrams.** No diagram-only request is added in this phase; a paper gets a diagram when it is summarized. (Possible later: a `diagram` request kind.)
- **The gold set is a weak gate** (10 papers, Claude-drafted answer keys nobody reviewed, ±3 facts of noise). So in addition: during the eval, Claude reads 10 new hooks and their key numbers against the cited section text and writes a short verdict per hook (true / overstated / wrong) into the eval report. No owner work is needed.
- **`one_line` is required** in the schema and always present, so the fallback chain ends there.
- **`PROMPT_VERSION`** is only recorded in each summary's provenance and in eval reports; bumping it does not re-summarize anything.

## Left to the builder

Exact field and module names, the number-matching code, the SVG layout code and its sizes, the setting command's name and place, how check reasons are worded, and test layout.
