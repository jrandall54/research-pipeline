# Handoff

Current state and next steps only. Read `ROADMAP.md` for the full list of phases; history is in git, each phase's files and `archive/`.

## State (2026-11-25)

- Phases 1 to 8 merged to `main` and pushed. `uv run pytest -q`: 1636 passing, ruff clean.
- The app (Phase 8) is installed on the owner's machine as an always-on service (`rpipe app status`), with the pipe-and-paper icon.
- The nightly run (03:00) uses whatever is checked out in the main repo folder (editable install). Never check out a pre-Phase-7 commit there: the vault moves to database version 4 on the 2026-11-26 run, and older code refuses it.
- Plans and designs now follow one naming scheme (see `ROADMAP.md`, "How this is organized"). The Claude Docs master plan is retired.

## Next

1. **Check the 2026-11-26 nightly run.** It is the first on Phase 7 and 8 code.
   - Finished, with a digest: `rpipe runs`, or simply open the app and see today's feed.
   - Papers read whole, not in parts: `journalctl --user -u research-pipeline --since today | grep "reading mode"` should say `whole`.
   - Database at version 4: `uv run python -c "import sqlite3,os; print(sqlite3.connect(os.path.expanduser('~/research-vault/vault.sqlite')).execute('PRAGMA user_version').fetchone())"` prints `(4,)`.
2. **Phase 9: diagrams and checked hooks.** Start with the design conversation (what it's for), then the design file, plan and build.

## Parked (small, not urgent)

- **Phase 8 app:** `/day/<date>` only covers the last 60 digest days; a summarize request marked done without a summary shows "Summarize this"; Tailscale machine names (MagicDNS) are refused as hosts, so use the number address; old-style arXiv ids with "/" can't open the paper page; a 405 under `/api/` returns `{"detail"}` not `{"error"}`; the "paper you liked" reason isn't a link; no link to newer days from an older day; Android may open the home-screen app as a browser tab over plain http.
- **Phase 7:** `requests list` shows a request whose third try was cut short as pending until the next nightly run; `_current_vote` duplicates `current_votes`; search rank order is untested; only `vote` has a CLI test for id spellings.
- **Phase 6.6:** the map-reduce fallback gives no progress note when all notes are unusable; a hung model load can wait up to 600 s.

## Open owner question

- Add "Pipeline code and app code are separate groups; see docs/guide/architecture.md." to `CLAUDE.md`? Not answered yet; don't add without a yes.
