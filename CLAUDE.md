# Research Pipeline

Nightly pipeline that collects CS/security/AI papers, ranks them against the user's profile and writes a digest. The single list of all work is `docs/superpowers/ROADMAP.md`; the current state and next steps are in `docs/superpowers/handoff.md`.

## Plans and phases

- Every piece of work is a phase, `Phase N` or `Phase N.M` for work that grew out of phase N. No other labels ("part", "project").
- Each phase has `docs/superpowers/specs/phase-NN[.M]-<slug>-design.md` and `docs/superpowers/plans/phase-NN[.M]-<slug>.md`, each opening with the standard header line (phase, status, partner file, roadmap).
- `ROADMAP.md` is the source of truth: update its row (status, links) in the same commit as the work. `handoff.md` holds only the current state and next steps; replace, don't append. The old Claude Docs master plan is retired.

## Documentation is part of every change

The owner does not write the code but must understand all of it and explain it to others (e.g. the AI club).

- Every change updates the relevant page in `docs/guide/` **in the same commit**. Code without matching docs is incomplete.
- Page shape: What it does → How it works (step by step, worked example or diagram) → Why it's built this way (decisions + rejected alternatives) → How to run / check it → Where the code is.
- Write for a technically literate reader who has not seen the code. Explain ML/IR terms in plain words and add them to `docs/guide/glossary.md`.
- When behaviour changes, fix the docs that describe the old behaviour. Stale docs are bugs.

## Users never hand-edit files

The pipeline is for the owner and for others (AI club members) with different interests. Every setting a user needs (topics, seed papers, authors, keywords, categories, top-N) must be settable through `rpipe` commands or guided prompts that validate input. `profile.yaml` and `config.yaml` stay as the storage format and may be hand-edited by power users, but no documented workflow may require it. A feature that adds a user setting also adds the command to change it.

## Conventions

- The CLI is `rpipe` (long alias `research-pipeline`). Use `rpipe` in docs, messages and examples.
- Engine separate from data: nothing personal (profile, vault, notes) in the repo.
- Tests run offline (fixtures + fake embedder). `uv run pytest -q`.
- Every step that waits reports through `progress.py` (stages emit events; the CLI renders them). See docs/guide/running.md, Progress.
