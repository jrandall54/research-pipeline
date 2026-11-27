# Phase 10.3 — Tidy topics on the People page: Implementation Plan

> **Phase 10.3** · Status: merged · Design: [phase-10.3-people-topics-design.md](../specs/phase-10.3-people-topics-design.md) · Roadmap: [ROADMAP.md](../ROADMAP.md)

**Goal:** short topic tags on People cards, your own card from your live profile, and an immediate republish when topics change.

## Tasks

1. `feed_format.topics_shared_first(theirs, mine)`: (topic, shared) pairs, shared first, order otherwise kept.
2. `library_api.DirectoryEntryView.topic_tags`; `directory()` uses `my_topics` for your own entry (no highlighting) and `topics_shared_first` for others.
3. `people.html`: first `TOPICS_SHOWN` (4, in `routes_people.py`) tags plus "+N more" in the summary; "All topics" in the opened card; no "shares N" on your own card. CSS: `.chip`, `.chip-shared`, `.chip-more`.
4. `cli._topics_changed(ctx, locked=False)`: when sharing is on, take the vault lock (unless held), publish with progress, warn on lock busy or push failure; then restart the app. Called from `add-topic`, `remove-topic` and `suggest`.
5. Tests: `test_webapp_people.py` (tags, order, more count, own card), `test_library_api_share.py` (topic_tags, live topics), `test_cli_share.py` (republish on add/remove, none when private, push failure keeps the edit).
6. Docs: `docs/guide/app.md` (People), `sharing.md` ("When you change your topics", command table), `profile.md` (command table); ROADMAP row; handoff.

Verified: `uv run pytest -q` 1973 passing, ruff clean.
