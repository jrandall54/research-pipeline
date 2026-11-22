# Handoff — state after Phase 2 and auto-model (2026-11-21)

Read this first in a new session. The master plan (Claude Docs, "Research Pipeline — Master Implementation Plan") says what Phase 3 is.

## Done and on `main`
- **Phase 2** (plan: `plans/2026-11-21-phase2-summaries-you-can-trust.md`): full-text fetch from arXiv HTML, cited summaries, gold set, eval.
- **Auto model and context** (plan: `plans/2026-11-21-auto-model-and-context.md`): `llm_model` and `llm_num_ctx` default to `auto`; `ollama_fit.py` probes Ollama; `rpipe model fit|show|check|set|set-url|set-context`.
- Tests: `uv run pytest -q` (407 passing at merge). Docs: `docs/guide/` (llm, fulltext, summaries, evaluation, glossary updated).

## Owner actions not yet done
- Run `rpipe model fit` once (loads and unloads models for a few minutes), then check `rpipe model show`.
- "Best model" = most parameters that fits, so `ds2-16b-20k` may beat `q3-14b-22k`. Pin with `rpipe model set q3-14b-22k` if wanted.
- Gold set is empty. Optional: `rpipe gold add`, then `rpipe eval run|grade|report`. The eval is the proper way to compare models.
- The user's `o` script (`~/.local/bin/o`) is NOT a dependency. Never call it from the program.

## Rulings worth remembering
- A pinned model needs to fit at 8192 only; an auto-chosen one at 16384 or more.
- `llm_num_ctx` floor is 8192 (the prompt budget is zero at 2048).
- Summary retries: same-day recovery, cap 3 (`MAX_SUMMARY_TRIES`). A failure left to a later day is not retried (documented limitation).
- Hidden-text stripping against prompt injection has documented gaps (see `docs/guide/fulltext.md`).

## Parked (not fixed, no owner decision needed yet)
- Cached `num_ctx` is not re-checked against bounds.
- A failed unload after a probe is swallowed silently.
- No overall time limit on probing (a hung Ollama could stall a run about 30 minutes).
- The 1024-token margin is also applied when a model fits at its full trained context.
- `rpipe model show` can take about 30 s if Ollama hangs.
- Layering inversion: `db.py` imports `summarize`.
- Tests: duplicate `AlwaysSummary`, and test-to-test imports.
- No command to edit a single gold fact.
- No stop after the first LLM timeout in a run.

## Process notes
- Plans were executed subagent-driven (implementer, per-task review, final opus review, one fix wave). The T6 implementer wrote code before tests, and the Phase 2 fix wave was not strictly test-first; both were disclosed.
- Project rules are in `CLAUDE.md`: docs in the same commit as code, and every user setting needs an `rpipe` command.
