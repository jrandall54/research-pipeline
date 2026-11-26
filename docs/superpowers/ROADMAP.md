# Roadmap

The single list of all work on rpipe: what is done, what is next, and where each piece's design and plan live. Updated in the same commit as the work it describes. Last updated 2026-11-26 (Phase 10 built on branch phase-10-sharing, awaiting merge).

**Next up:** the owner's first real `rpipe share setup`, then a first nightly run with sharing. Summaries and the app's look stay as they are (owner ruling 2026-11-26).

## How this is organized

- **One numbering.** Every piece of work is a phase: `Phase N`, and `Phase N.M` for work that grew out of phase N. No "parts", "projects" or other labels.
- **One pair of files per phase.** The design is `specs/phase-NN[.M]-<slug>-design.md`, the build plan `plans/phase-NN[.M]-<slug>.md` (two-digit numbers, so they sort in order). Small phases may have a plan only.
- **One header.** The line under each file's title says the phase, its status and links to its partner file and this roadmap.
- **Statuses:** idea → designed → planned → building → merged. A phase is "merged" when its branch is on `main` and pushed.
- **This file is the source of truth.** `handoff.md` holds only the current state and the next steps for the next session. History lives in git, in each phase's files, and in `archive/`.
- **Each phase starts with a design conversation** (what it's for, before how), then the design file, then the plan, then the build.

## Phases

| Phase | What it gave you | Status | Design | Plan |
|---|---|---|---|---|
| 1 | Abstract digest: nightly collection from arXiv and Hugging Face, ranked against your profile | merged | master plan (archived) | [plan](plans/phase-01-abstract-digest.md) |
| 1.5 | Your profile without editing files: `rpipe profile`, guided `rpipe init` | merged | master plan (archived) | [plan](plans/phase-01.5-profile-without-editing.md) |
| 2 | Summaries you can trust: full-text summary and critique, every claim cites its section | merged | master plan (archived) | [plan](plans/phase-02-summaries-you-can-trust.md) |
| 2.1 | Automatic model and context choice for Ollama | merged | (owner request) | [plan](plans/phase-02.1-auto-model-and-context.md) |
| 3 | Daily habit: votes and saves shape ranking, nightly timer, run log, onboarding | merged | master plan (archived) | [plan](plans/phase-03-daily-habit.md) |
| 3.1 | Parked-list fixes | merged | (handoff list) | [plan](plans/phase-03.1-parked-fixes.md) |
| 3.2 | `rpipe review`: read and vote in the terminal | merged | [design](specs/phase-03.2-review-loop-design.md) | [plan](plans/phase-03.2-review-loop.md) |
| 4 | The shelf: your own work boosts related papers (other Phase 4 items moved to Phase 11 and Ongoing) | merged | [design](specs/phase-04-shelf-design.md) | [plan](plans/phase-04-shelf.md) |
| 5 | Shareable: install anywhere, hardware-aware setup, Claude Code backend, plug-ins | merged | [design](specs/phase-05-shareable-design.md), [research](specs/phase-05-cli-backends-research.md) | [plan](plans/phase-05-shareable.md) |
| 6 | Pipeline hardening: progress on every wait, `rpipe show`, setting commands, category check | merged | [design](specs/phase-06-hardening-design.md) | [plan](plans/phase-06-hardening.md) |
| 6.1 | Answer key: Claude drafts and grades the gold set | merged | [design](specs/phase-06.1-06.6-hardware-adaptive-design.md) | [plan](plans/phase-06.1-answer-key.md) |
| 6.2 | Strict pruning: the model reads only the paper | merged | same | [plan](plans/phase-06.2-strict-pruning.md) |
| 6.3 | One prompt layout for caching | merged | same | [plan](plans/phase-06.3-prompt-layout.md) |
| 6.4 | Machine scan and model recommender (`rpipe model scan/recommend/tune/bench`) | merged | same | [plan](plans/phase-06.4-scan-recommend.md) |
| 6.5 | Rereading long papers overnight within a morning deadline | merged | same | [plan](plans/phase-06.5-spill-reruns.md) |
| 6.6 | Reading in parts on slow machines; embeddings moved to CPU | merged | same | [plan](plans/phase-06.6-split-map-reduce.md) |
| 7 | Pipeline/app split: one lock, `library_api`, request list | merged | [design](specs/phase-07-pipeline-app-split-design.md) | [plan](plans/phase-07-pipeline-app-split.md) |
| 8 | Your own feed: `rpipe app`, phone and desktop, Skim/Read, votes, saves, search | merged | [design](specs/phase-08-your-feed-design.md) | [plan](plans/phase-08-your-feed.md) |
| 8.1 | Readable text in the app: each summary field spaced out, longer ones as points | merged | (Phase 9 design conversation) | [plan](plans/phase-08.1-readable-text.md) |
| 9 | Diagrams in the feed, plus hooks, key numbers and plain-words fields, checked against the paper | merged | [design](specs/phase-09-hooks-and-diagrams-design.md) | [plan](plans/phase-09-hooks-and-diagrams.md) |
| 9.1 | Stricter diagrams: no diagram by default, generic ones dropped, boxes must come from the method | merged | (owner request after the Phase 9 eval) | [plan](plans/phase-09.1-stricter-diagrams.md) |
| 9.2 | Diagrams on the paper screen only, not in the feed | merged | (owner's choice after the 9.1 measurements) | [plan](plans/phase-09.2-diagram-on-paper-page.md) |
| 9.3 | Diagrams off: the setting also hides diagrams already drawn | merged | (owner ruling 2026-11-26: diagrams from summaries are pointless) | [plan](plans/phase-09.3-diagrams-off.md) |
| 10 | Sharing: publish your feed, a directory, follow others, custom mixes | merged | [design](specs/phase-10-sharing-design.md) | [plan](plans/phase-10-sharing.md) |
| 11 | Notes and link capture; the shelf grows into one library (overlap alerts, archive connections, weekly synthesis page) | idea | | |
| 12 | Narrated video explainers | idea | | |
| 13 | Study material: concept map linked to coursework, quizzes, the teacher's layered templates | idea | | |
| 14 | Thesis help: literature search, gap finder, citation export, an MCP tool over `library_api` | idea | | |
| 15 | "Point at anything": import any link, in any format | idea | | |

The vision and decisions behind Phases 8 to 15 (shared feeds, no interaction, local first, visuals that explain, never clickbait) are in [the app roadmap](specs/phase-08-15-app-roadmap-design.md).

## Still open from finished phases

- **Phase 2 exit:** the gold set has 10 Claude-drafted answer keys nobody has reviewed; scores swing about ±3 between runs.
- **Phase 6 owner setup:** add seed papers (there are none), review the gold set.
- **Phase 6:** wire the local-only guard (`require_local`) before any feature sends shelf text to a model. Phase 11 or 13 will be the first to need it.
- **Phase 6:** AI club pilot with 3 or 4 members.
- **Phase 6.6:** measure reading in parts on a CPU-only machine (a throwaway vault, one evening).
- **Phase 8 parked items:** listed in `handoff.md`.

## Ongoing (scheduled when wanted)

- Tier-comparison study on the gold set (local vs cloud vs no model).
- Marker PDF fallback; citation backfill.
- Critique compared against real OpenReview reviews.
- Triage safety net: a weekly triage check and a random sample of skipped papers in the digest.
- Flag papers whose stripped hidden text held instruction-like phrases.
- Fuller eval rubric: coverage, grounding, critique quality, blind grading, time and cost per paper.
- Search summaries and full text, not only title and abstract; keep earlier summaries when a paper is re-summarized.
- Semantic Scholar TL;DRs and open-access links; an RSS source for blog articles.
- Use ratings to suggest dropping categories that never produce good papers.
- Feasibility tag on each kept paper (code, weights, data released; could it run on 16 GB).
- Smaller ideas: a one-line model-written "why this matched", Docling as a Marker alternative, prompts in user-editable files, per-paper cloud routing.

## Separate projects (not scheduled)

- Optimised CPU pipeline for machines with no GPU and no subscription (needs its own design).
- Antigravity (`agy`) as a safe backend (free Gemini Pro for students); open problems: no-TTY hangs, no tools-off switch.
- Codex adapter, once verified on a real install.
- Other subscription CLIs (Copilot, Cursor, Kiro) and schedulers beyond systemd (launchd, Task Scheduler).

**Not planned:** Docker; API-key backends.

## Open questions

- Which embedding model ranks best on your seed papers (once there are seed papers)?
- How many papers per night do you actually read? Tune top N to that.
- Will the club pilot run on members' own machines, the university GPU server, or both?

## Reference

- **How every part works:** `docs/guide/` (start at `docs/guide/README.md`; the architecture is in `docs/guide/architecture.md`).
- **The original master plan** (purpose, goals, design principles, stage specifications, evaluation method, risks) was retired on 2026-11-25. It stays readable, marked as retired, in Claude Docs: https://claude.ai/artifact/Ajw2s52HwWuZfrYZXsZjfa. Everything still open in it is listed above.
- **The old handoff log** (session-by-session notes up to 2026-11-25): [archive/handoff-log-to-2026-11-25.md](archive/handoff-log-to-2026-11-25.md).
