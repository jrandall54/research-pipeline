# Handoff

Current state and next steps only. Read `ROADMAP.md` for the full list of phases; history is in git, each phase's files and `archive/`.

## State (2026-11-25)

- Phases 1 to 9.2 merged to `main` and pushed. `uv run pytest -q`: 1765 passing, ruff clean. GitHub's test run passes again (it had failed on every push since Phase 8: a test needed uvicorn, which GitHub's setup does not install).
- **Phase 9 (hooks, key numbers, plain words, diagrams):** every new summary gets a hook line under the title, up to 3 key numbers and plain-words fields, each checked against the paper text; papers whose method has a shape also get a diagram drawn in the app. Prompts are `2026-11-p9b`. Phase 9.1 made diagrams stricter (no diagram by default, generic loops dropped, boxes must cite method sections). The model still draws one for nearly every paper (reviews get their review process), so Phase 9.2 shows diagrams only on the paper screen, not in the feed (owner's choice). Gold-set eval passed (facts 11 → 11, contradicted 2 → 1); Claude read the 9 hooks shown and found all true. Results are in `docs/guide/summaries.md`, "Measured".
- Diagrams: setting `auto` (on here, since the model is fully in GPU memory; off on CPU-only machines). `rpipe model set-diagrams on|off|auto`.
- Old papers keep their one-line summary and long fields; only papers summarized from now on (nightly or "Summarize this") get the new parts.
- The app service was restarted on the merged code.
- The nightly run (03:00) uses whatever is checked out in the main repo folder (editable install). Never check out a pre-Phase-7 commit there: the vault moves to database version 4 on the 2026-11-26 run, and older code refuses it.

## Next

1. **Check the 2026-11-26 nightly run.** It is the first on Phase 7, 8 and 9 code.
   - Finished, with a digest: `rpipe runs`, or simply open the app and see today's feed.
   - Hooks and diagrams: today's cards show a hook line and key-number chips under the title; opening a paper shows a "How it works" diagram on most papers (diagrams are on the paper screen only since Phase 9.2). `journalctl --user -u research-pipeline --since today | grep -E "reading mode|diagram"` shows `diagrams on` and `drawing a diagram for ...` lines.
   - Papers read whole, not in parts: the same journal shows `whole` for the reading mode.
   - Database at version 4: `uv run python -c "import sqlite3,os; print(sqlite3.connect(os.path.expanduser('~/research-vault/vault.sqlite')).execute('PRAGMA user_version').fetchone())"` prints `(4,)`.
   - Night length: the diagram call adds about 12 s per paper on this machine; check the run still finishes well before morning.
2. **Phase 10: sharing.** Start with the design conversation (what it's for), then the design file, plan and build.

## Parked (small, not urgent)

- **Phase 9:** hooks can run past 90 characters (the limit is in the prompt only; 2 of 9 in the eval); negative numbers ("-15%") and the second number of a range ("10-20%") are not checked, so a hook resting on them is dropped; node details and arrow labels are not drawn in diagrams; 8 of 10 eval diagrams chose the `loop` shape; a usage limit in the eval's diagram call is reported as "the model call failed"; the nightly health line does not count diagrams drawn for requests; `cli._read_mode` is only used by a test.
- **Phase 8 app:** `/day/<date>` only covers the last 60 digest days; a summarize request marked done without a summary shows "Summarize this"; Tailscale machine names (MagicDNS) are refused as hosts, so use the number address; old-style arXiv ids with "/" can't open the paper page; a 405 under `/api/` returns `{"detail"}` not `{"error"}`; the "paper you liked" reason isn't a link; no link to newer days from an older day; Android may open the home-screen app as a browser tab over plain http.
- **Phase 7:** `requests list` shows a request whose third try was cut short as pending until the next nightly run; `_current_vote` duplicates `current_votes`; search rank order is untested; only `vote` has a CLI test for id spellings.
- **Phase 6.6:** the map-reduce fallback gives no progress note when all notes are unusable; a hung model load can wait up to 600 s.

## Open owner question

- Add "Pipeline code and app code are separate groups; see docs/guide/architecture.md." to `CLAUDE.md`? Not answered yet; don't add without a yes.
