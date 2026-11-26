# Phase 6 — Pipeline hardening: design

> **Phase 6** · Status: approved, built · Plan: [phase-06-hardening.md](../plans/phase-06-hardening.md) · Roadmap: [ROADMAP.md](../ROADMAP.md)

Date: 2026-11-22. Status: draft for owner approval. Master plan: "Phase 6 — Pipeline hardening".

## Intent

Finish the pipeline before the app is built on it. The nightly run must be trustworthy on the default local (Ollama) tier, must never look frozen, and must handle long papers. The owner reads the output daily and must be able to explain every step to the AI club, so each piece ships with its `docs/guide/` page in the same commit.

**Exit (from the master plan):** the nightly run is trustworthy on the default (Ollama) tier, never looks frozen, and handles long papers.

## Goals
1. Every step that waits shows it is alive: what it is doing, elapsed time, and a count when the total is known.
2. Long papers: summaries read the whole paper, or say plainly what was left out, on the owner's 16 GB GPU.
3. The Ollama path is measured for real: time per paper and summary quality, on the papers the owner actually gets.
4. Close the small gaps the audit found: three settings without an `rpipe` command, the category check, `rpipe show`, and a guard that keeps `my_work/` text away from models.
5. Settle where Phase 4's leftover slices live.

## Non-goals
- No app work (Phases 8–11) and no pipeline/app split (Phase 7). Phase 6 only makes choices that keep Phase 7 easy (progress as data objects).
- No new sources (RSS, Marker): those stay in Ongoing.
- No change to ranking.

## Anti-goals (a failure even if it works)
- Progress output that clutters the nightly log or breaks when output is piped (systemd journal, `| tee`).
- Progress that lies: a bar that reaches 100% while work continues, or a total that is a guess.
- A long-paper fix that makes summaries worse on the papers that already fit.
- New user settings that can only be changed by hand-editing YAML (CLAUDE.md rule).

## Constraints
- Offline test suite (fake embedder, fake model); no network or GPU in tests.
- Owner hardware: RTX 5070 Ti, 16 GB. Installed models include a 27B dense model (IQ3_S, ~34K context; what `auto` picks today) and a 14B long-context model (143K context).
- Measured 2026-11-22 on the owner's vault: stored full texts are 90K–198K characters (median ~106K). The prompt budget at 34K context is about 90K characters, so most papers are truncated today.
- `typer` already brings `rich`; no new dependency is needed for progress display.
- Phase 7 will route the app through `library_api`, which returns data objects: progress must be data, not pre-formatted text.

## Part A — Progress feedback

### Approaches considered
1. **Shared progress module with event objects (chosen).** A small `progress.py` defines a `ProgressEvent` data object (stage, kind: start / step / note / done, current, total, label, elapsed) and a `Reporter` interface. Pipeline stages accept an optional reporter and emit events; they never print. The CLI supplies one of two renderers: a live display with a spinner, elapsed time and a bar when the total is known (terminal), or one plain line per event plus a heartbeat line every 30 s during a long wait (not a terminal: systemd journal, pipes). Trade-off: touches every stage's signature once; in return stages stay testable (tests collect events) and the future app can show the same events.
2. **Wrap calls in `rich.progress` inside `cli.py` only.** Fastest, but cannot see inside a stage (arXiv pages, papers, the summary-vs-critique step), produces strings the app cannot use, and leaves the 600 s model call silent. Rejected.
3. **`tqdm` bars inside stages.** New dependency, prints from inside library code, poor non-terminal output. Rejected.

### Design
- `progress.py` (shared module; Phase 7 classifies it *shared*): `ProgressEvent` (frozen dataclass), `Reporter` protocol (`emit(event)`), `NullReporter`, `ListReporter` (tests), and a `stage(reporter, name, total=None)` context manager that emits start/done with elapsed time, including on error.
- Renderers live in the CLI layer: `LiveRenderer` (rich, stderr, used when stderr is a terminal) and `LineRenderer` (plain lines to stderr). Progress always goes to stderr so stdout stays clean.
- Heartbeat: while a stage is waiting on one long call (model generate, embedder load, arXiv page), the live display's clock keeps ticking on its own; the line renderer prints `still waiting: <label> (1m30s)` every 30 s.
- Stages that emit events (the audit's silent list): embedder load/first download; collect per source and per arXiv page; triage embedding (count of papers); shelf sync (count of chunks); seed lookups; summarize per paper with steps fetch → summary → critique (and the citation retry); `model fit` probes (existing log lines become `note` events); `model pull` percent; `eval run` per paper; `profile suggest` / `init` lookups; `model check` probes.
- `run_summarize`'s current `on_progress(index, total, paper_id, outcome, seconds, detail)` callback is replaced by the reporter; the existing per-paper result line (`[2/3] 2611.25005 done (41s)`) is kept as the `done` event's rendering so nothing users see today disappears.
- Docs: a "Progress" section in `docs/guide/running.md` (what you see on a terminal vs in the journal, and why), glossary entries (TTY, stderr, heartbeat). CLAUDE.md gains one line: every waiting step reports through `progress.py` (owner approves the CLAUDE.md edit when the plan runs).

## Part B — Long papers and the Ollama check

### Approaches considered
1. **Bigger context window (owner's first choice).** Use a long-context model (the 14B one, 143K context) so the whole paper fits. Simple; costs model size (14B vs 27B) and speed.
2. **Chunked summaries (map-reduce).** Summarize sections in parts, then merge. Keeps the bigger model but costs more calls, a merge step that can drop or duplicate claims, and weaker cross-section critique. Owner's second choice.
3. **Smarter trimming.** Keep the current model; cut by section priority (methods and results kept, related work and appendices dropped first) instead of trimming every section by the same fraction. Cheapest; still loses text.

### Decision process (owner chose the quick comparison first)
- Measure now: both models summarize the same three long papers (2611.25005, 2611.26408, 2611.23375) in copies of the owner's vault; record time per paper and whether each was truncated; the owner judges the pairs side by side (about 20 minutes).
- Then decide in this phase:
  - If the 14B long-context model is as good or better: make long context the default path. `auto` gains one rule: prefer the model whose fitted context holds a typical paper (budget measured from the vault's stored full texts), falling back to the current "most parameters" rule. Pin is still available.
  - If the 27B model is clearly better despite truncation: build approach 3 (section-priority trimming) first, and approach 2 only if truncation still loses what matters.
- The gold set (`rpipe gold add`, `rpipe eval`) is the later, rigorous check; the owner fills it during owner setup, and it closes Phase 2's exit.

## Part C — Small gaps

1. **Setting commands** (CLAUDE.md rule):
   - `rpipe sources set-max-results N` for `arxiv_max_results` (validated range).
   - `rpipe model set-embedding NAME` for `embedding_model`. Embeddings are stored per model name, so switching re-embeds on the next run; the command says so and how long the first download may take.
   - `rss_feeds`: no RSS source exists. Remove it from the default config template and the docs; old config files that still contain it keep loading. It returns with the RSS source (Ongoing), with its command.
2. **Category check:** ship a packaged list of arXiv category codes; `rpipe profile add-category` / `set-categories` reject unknown codes (`cs.ML` today) and suggest the closest real one. `all` keeps its meaning.
3. **`rpipe show <id or title>...` and `rpipe show --recent [N]`:** print title, id, why it matched, summary and critique (reusing the existing paper display from `rpipe review`), no voting.
4. **Keep `my_work/` away from models:** no code sends shelf text to a model today. Add a boundary test that fails if any prompt-building or model-calling module reads shelf text, and document in `shelf.md` that the first feature needing it must call `llm.require_local`. (Building a guarded accessor now would be code with no caller.)

## Part D — Phase 4 leftovers (decision, recorded in the master plan)
Proposal: overlap alerts and archive connections are things you look at while reading, so they move to app Phase 9 (library); weekly synthesis moves to app Phase 9 as a weekly page; the feasibility tag stays in the pipeline and goes to Ongoing (small, scheduled when wanted). The `links` table stays for Phase 9.

## Part E — Owner actions (not code)
1. Read the model comparison and pick (Part B).
2. Reinstall with extras: `uv tool install --editable '.[embed,serve]' --force`; `rpipe schedule status`; `loginctl enable-linger $USER`.
3. Add seed papers: `rpipe profile suggest "<a few sentences about your interests>"` or `rpipe profile add-seed <arXiv link>`.
4. Vote on digest papers (`rpipe review`) so the implicit profile learns.
5. Gold set: 5–10 papers you know well (`rpipe gold add`), then `rpipe eval run|grade|report`.
6. After the exit holds: AI club pilot with 3–4 members.

## Order of work
1. Part A (progress) — largest code change, no dependency on the owner.
2. Part C (small gaps) — independent tasks.
3. Part B — after the owner reads the comparison.
4. Part D — the owner confirms; master plan updated.
Each task: test first, docs in the same commit, one commit per task, subagent-driven with review.

## Left to the builder
- Exact event fields beyond those listed, the heartbeat interval (30 s proposed), and rich layout details.
- Valid range for `set-max-results` (proposed 50–5000).
- Where the arXiv category list comes from (a pinned copy of arXiv's published taxonomy, refreshed by hand).

## Settled after the builder check (designer's calls unless marked)
1. **Plan scope.** The plan fully covers Parts A and C. Part B and Part D are gated: B is planned after the owner's verdict on the comparison (one branch only, not both pre-planned); D is a decision recorded in the master plan, no code. The comparison itself is a one-off script outside the repo (copies of the vault, `rpipe model set` per copy, `rpipe summarize <ids> --vault <copy>`), not a product feature.
2. **`auto` and long papers** are decided in Part B's own plan, after the verdict. Constraint carried forward: `auto` must not silently move every user to a smaller model; any change to `auto` is checked against the gold set (or the comparison papers) before it ships, and works on an empty vault (a fixed default budget when no full texts are stored).
3. **Signatures change at once, no shim.** Stages take a keyword-only `reporter: Reporter | None = None` (None means silent). `run_summarize`'s `on_progress` is removed; `cli.py` callers (`_paper_progress`, lines ~392 and ~555) and tests change in the same commit.
4. **Event shape.** `kind` is one of `start`, `step`, `note`, `done`. Stage-level start/done come from `stage()`. A per-item result (a paper, a page) is a `step` event with `current`, `total`, `label`, `elapsed`, and an `outcome` (`ok`, `skipped`, `failed`, `abstract-only`, ...) plus `detail`. The line renderer prints an item `step` exactly as today's `[2/3] 2611.25005 done (41s)`. The heartbeat is a timer thread owned by `LineRenderer`, with the interval and clock injectable so tests run instantly and offline.
5. **Categories.** The packaged list is arXiv's full published taxonomy, including old-style archives without a dot (`hep-th`, `astro-ph`) and non-cs archives (`math.PR`, `stat.ML`). Commands reject unknown codes with a nearest-match suggestion. Values already stored that are not in the list still load: `rpipe profile show` and the run's health line warn about them; nothing fails.
6. **`rpipe show`** resolves ids and titles the same way `rpipe vote` does (exact id, then title substring, a numbered pick when several match). A paper without a summary shows its abstract (as `rpipe review` does). `--recent [N]` shows the papers of the latest N digests (default 1).
7. **`my_work/` boundary test:** modules that build prompts or call models (`prompts`, `llm`, `summarize`, `backends`, `onboarding`, `evaluate`) must not import `shelf`. Ranking reading shelf *embeddings* is allowed: vectors are not text sent to a model.
8. **`set-embedding` / `set-max-results`:** no migration (embeddings are keyed by model name). `set-embedding` accepts any name, warns that the next run downloads the model and re-embeds everything (with progress), and offers `rpipe model set-embedding BAAI/bge-small-en-v1.5` to go back. `set-max-results` range 50–5000.
