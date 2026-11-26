# Handoff

Current state and next steps only. Read `ROADMAP.md` for the full list of phases; history is in git, each phase's files and `archive/`.

## State (2026-11-26)

- Phases 1 to 10.2 merged to `main` and pushed (Phase 10 = sharing; 10.1 = every `rpipe init` step explained, categories as a numbered menu; 10.2 = one terminal look for every command via `term.py`; all 2026-11-26). `uv run pytest -q`: 1963 passing, ruff clean.
- Phase 10 lets the owner publish their picked papers and summaries as a public feed (`rpipe share setup`), browse and follow other people's feeds (`rpipe share browse`, `follow`, or the People page in the app), and see Mine, Following and Mix in the feed. Nothing leaves the machine until sharing is turned on; with no follows the feed looks as before apart from a People link.
- Diagrams are off (Phase 9.3 owner ruling: `rpipe model set-diagrams off`). Summaries and the app's look stay as they are (owner ruling 2026-11-26); don't bring the old redesign back unless asked.

## Next

1. Check the first nightly run with sharing (owner ran `rpipe share setup` on 2026-11-26: publishing as "James Randall" at https://jrandall54.github.io/rpipe-feed/; topics trimmed from 18 to 14 the same day) (the fetch stage before the digest, the publish stage after; `rpipe share status`).
2. AI club members can follow the owner's feed with `rpipe share follow HANDLE`.
3. Owner setup still open: seed papers (none yet).
4. Later: diagrams from the paper's own figures and tables (owner's idea, 2026-11-26).

## Parked (small, not urgent)

- **Phase 9:** hooks can run past 90 characters (the limit is in the prompt only; 2 of 9 in the eval); negative numbers ("-15%") and the second number of a range ("10-20%") are not checked, so a hook resting on them is dropped; node details and arrow labels are not drawn in diagrams; 8 of 10 eval diagrams chose the `loop` shape; a usage limit in the eval's diagram call is reported as "the model call failed"; the nightly health line does not count diagrams drawn for requests; `cli._read_mode` is only used by a test.
- **Phase 8 app:** `/day/<date>` only covers the last 60 digest days; a summarize request marked done without a summary shows "Summarize this"; Tailscale machine names (MagicDNS) are refused as hosts, so use the number address; old-style arXiv ids with "/" can't open the paper page; a 405 under `/api/` returns `{"detail"}` not `{"error"}`; the "paper you liked" reason isn't a link; no link to newer days from an older day; Android may open the home-screen app as a browser tab over plain http.
- **Phase 7:** `requests list` shows a request whose third try was cut short as pending until the next nightly run; `_current_vote` duplicates `current_votes`; search rank order is untested; only `vote` has a CLI test for id spellings.
- **Phase 6.6:** the map-reduce fallback gives no progress note when all notes are unusable; a hung model load can wait up to 600 s.
- **Phase 10 sharing:** `set_topic` replaces other repository topics; `follow()` downloads `feed.json` twice; no block on https redirects to private or loopback hosts; dropped archive items are not retried; `_load_profile` runs before the lock in setup, publish and stop; `browse` has no pagination; followed posts show no publisher provenance; the feed content flashes before the saved-view redirect; Back on your own paper loses Mix; no test for an unknown `?view=` while following; `git clone` shares the 120 s limit (re-run setup if it times out); the cleaner drops zero-width joiners (emoji in names may split); the app cannot block self-follow when you are not publishing (harmless); URL-form follows have no Mix tick in the app; the People nav link shows even with no follows (owner to confirm).

## Open owner question

- Add "Pipeline code and app code are separate groups; see docs/guide/architecture.md." to `CLAUDE.md`? Not answered yet; don't add without a yes.
