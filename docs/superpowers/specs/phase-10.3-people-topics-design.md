# Phase 10.3 — Tidy topics on the People page (design)

> **Phase 10.3** · Status: merged · Plan: [phase-10.3-people-topics.md](../plans/phase-10.3-people-topics.md) · Roadmap: [ROADMAP.md](../ROADMAP.md)

Date: 2026-11-26. Owner request (with a phone screenshot of People): "It just sort of laundry lists the users (my) topics with not real style or formatting and it's also showing deleted topics like 'sexcurity' which was a typo that has been removed. Can we make it a little more succinct?"

## What was wrong

- Each directory card printed every topic as one " · "-joined line: 19 topics ran to about 15 lines on a phone.
- The card's topics come from the directory, which is a copy of each person's published `feed.json`. Editing topics with `rpipe profile` did not republish, so the owner's removed typo stayed public and on their own card until the next nightly push.

## Goals and non-goals

- Goal: short cards: topics as tags, at most 4 on a closed card, then "+N more"; opening the card lists all.
- Goal: topics you share with a person come first and are highlighted.
- Goal: your own card shows your profile topics now, and drops "shares N of your topics" (you vs. you).
- Goal: changing topics republishes your feed right away when sharing is on, so followers see it too.
- Non-goal: any other change to the app's look (owner ruling 2026-11-26 keeps it as it is).
- Anti-goal: a failed push must not undo or fail the topic edit.

## Approaches

1. **Chosen:** live topics on your own card, republish on topic change, tags with "+N more" opening the existing `<details>` card (no JavaScript).
2. Only republish (no live topics): your own card would still be stale until the directory refreshes at night.
3. Only show shared topics: your own card would be empty and strangers' cards would hide what they are about.

The owner approved approach 1 with the sketch below.

```
 (J) James Randall                         [You]
     [machine learning] [LLM] [java]
     [agentic workflow]  +10 more
     ~2.5 papers a day
```

## Decisions

- 4 tags on a closed card in the app (the terminal `rpipe share browse` keeps 3: a terminal line is narrower).
- Hooked commands: `profile add-topic`, `remove-topic`, `suggest` (only when topics changed). `rpipe init` is not hooked: sharing is set up after it.
- If another command holds the vault lock, the edit is saved and a warning says tonight's run (or `rpipe share publish`) will publish.
- After the push the background app is restarted (existing `_restart_app_if_installed`), because it reads topics at start-up.
