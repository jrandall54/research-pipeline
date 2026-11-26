# Handoff

Current state and next steps only. Read `ROADMAP.md` for the full list of phases; history is in git, each phase's files and `archive/`.

## State (2026-11-26)

- Phases 1 to 9.3 merged to `main` and pushed. `uv run pytest -q`: 1769 passing, ruff clean.
- The 2026-11-26 nightly run (first on Phase 7-9 code) worked: 15 papers summarized whole, 13 diagrams, 2 requests done, no errors, finished 03:15. Database is at version 4.
- **Diagrams are off** (Phase 9.3, owner ruling): `rpipe model set-diagrams off` is set, so none are drawn, and the app hides the ones already stored. The owner found them pointless (labels in boxes repeating "What they did"). Diagrams worth having would be the paper's own figures, tables and graphics brought into the app; that is a future phase, not now.
- **Summaries are hidden in the app (not fixed yet).** They are all stored and the markdown digest shows them in full, but: the feed opens in Skim, which shows one line per card; since Phase 9 that line is the hook (a single stat) instead of the one-line summary on 12 of 15 papers; Limitations is never shown in the app. The owner saw "no summaries".
- The owner also questioned the summary format itself: one sentence is not an overview of a paper, and Problem/Approach/Results/Limitations + Strengths/Weaknesses was chosen so code can check citations, not from research on how people learn from summaries.

## Next

1. **Summary display fix (small, first).** Default view shows the real summary; the one-line summary comes before the hook, never replaced by it; Limitations shown. Ask the owner before building: drop Skim, or keep it as an opt-in.
2. **Summary redesign (a phase, starts with a design conversation).** What a summary is for (e.g. "after reading it I know what the paper is about and whether to open it"); a short look at what research on summaries and learning supports (structured abstracts are the closest evidence); likely a real paragraph overview first, then the parts. Hooks stay only if they earn a place.
3. **Phase 10: sharing.** Design conversation first.
4. Later: diagrams from the paper's own figures and tables (owner's idea, 2026-11-26).

## Parked (small, not urgent)

- **Phase 9:** hooks can run past 90 characters (the limit is in the prompt only; 2 of 9 in the eval); negative numbers ("-15%") and the second number of a range ("10-20%") are not checked, so a hook resting on them is dropped; node details and arrow labels are not drawn in diagrams; 8 of 10 eval diagrams chose the `loop` shape; a usage limit in the eval's diagram call is reported as "the model call failed"; the nightly health line does not count diagrams drawn for requests; `cli._read_mode` is only used by a test.
- **Phase 8 app:** `/day/<date>` only covers the last 60 digest days; a summarize request marked done without a summary shows "Summarize this"; Tailscale machine names (MagicDNS) are refused as hosts, so use the number address; old-style arXiv ids with "/" can't open the paper page; a 405 under `/api/` returns `{"detail"}` not `{"error"}`; the "paper you liked" reason isn't a link; no link to newer days from an older day; Android may open the home-screen app as a browser tab over plain http.
- **Phase 7:** `requests list` shows a request whose third try was cut short as pending until the next nightly run; `_current_vote` duplicates `current_votes`; search rank order is untested; only `vote` has a CLI test for id spellings.
- **Phase 6.6:** the map-reduce fallback gives no progress note when all notes are unusable; a hung model load can wait up to 600 s.

## Open owner question

- Add "Pipeline code and app code are separate groups; see docs/guide/architecture.md." to `CLAUDE.md`? Not answered yet; don't add without a yes.
