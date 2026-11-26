# Handoff

Current state and next steps only. Read `ROADMAP.md` for the full list of phases; history is in git, each phase's files and `archive/`.

## State (2026-11-26)

- Phases 1 to 9.3 merged to `main` and pushed. `uv run pytest -q`: 1769 passing, ruff clean.
- The 2026-11-26 nightly run (first on Phase 7-9 code) worked: 15 papers summarized whole, 13 diagrams, 2 requests done, no errors, finished 03:15. Database is at version 4.
- **Diagrams are off** (Phase 9.3, owner ruling): `rpipe model set-diagrams off` is set, so none are drawn, and the app hides the ones already stored. The owner found them pointless (labels in boxes repeating "What they did"). Diagrams worth having would be the paper's own figures, tables and graphics brought into the app; that is a future phase, not now.
- **Summaries and the app's look stay as they are** (owner ruling 2026-11-26, next session): turning diagrams off was enough. The summary display fix and the summary redesign listed last session are dropped; don't bring them back unless the owner asks.

## Next

1. **Phase 10: sharing.** Design conversation first (started 2026-11-26).
2. Later: diagrams from the paper's own figures and tables (owner's idea, 2026-11-26).

## Parked (small, not urgent)

- **Phase 9:** hooks can run past 90 characters (the limit is in the prompt only; 2 of 9 in the eval); negative numbers ("-15%") and the second number of a range ("10-20%") are not checked, so a hook resting on them is dropped; node details and arrow labels are not drawn in diagrams; 8 of 10 eval diagrams chose the `loop` shape; a usage limit in the eval's diagram call is reported as "the model call failed"; the nightly health line does not count diagrams drawn for requests; `cli._read_mode` is only used by a test.
- **Phase 8 app:** `/day/<date>` only covers the last 60 digest days; a summarize request marked done without a summary shows "Summarize this"; Tailscale machine names (MagicDNS) are refused as hosts, so use the number address; old-style arXiv ids with "/" can't open the paper page; a 405 under `/api/` returns `{"detail"}` not `{"error"}`; the "paper you liked" reason isn't a link; no link to newer days from an older day; Android may open the home-screen app as a browser tab over plain http.
- **Phase 7:** `requests list` shows a request whose third try was cut short as pending until the next nightly run; `_current_vote` duplicates `current_votes`; search rank order is untested; only `vote` has a CLI test for id spellings.
- **Phase 6.6:** the map-reduce fallback gives no progress note when all notes are unusable; a hung model load can wait up to 600 s.

## Open owner question

- Add "Pipeline code and app code are separate groups; see docs/guide/architecture.md." to `CLAUDE.md`? Not answered yet; don't add without a yes.
