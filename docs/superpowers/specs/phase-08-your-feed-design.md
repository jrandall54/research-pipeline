# Phase 8 — Your own feed (design)

> **Phase 8** · Status: approved, built · Plan: [phase-08-your-feed.md](../plans/phase-08-your-feed.md) · Roadmap: [ROADMAP.md](../ROADMAP.md)

Date: 2026-11-25. Status: for owner approval (builder check done).
Roadmap: `phase-08-15-app-roadmap-design.md` (project 1 of 8). Mockups: the "Research Feed — App Design" canvas (https://claude.ai/artifact/JnycnAXpyjtqyybVmv9xMK), look A screens.

## Intent

The pipeline works unseen every night. Today you read its output through a text digest, terminal commands (`rpipe show`, `review`, `vote`) and `rpipe serve`, a bare database browser where you type paper ids into forms. This project builds the first real app: **a private website that runs on your own computer and shows your feed**, readable from your phone and your desktop, where one tap votes, saves or asks for a summary.

In the owner's words, it should work "with as much focus as the user wants", "look nice and not be clunky or technical", with "the technical details abstracted from the user-facing app". It is built for the owner first ("build it right for me and then go from there") while keeping the long-horizon goals in view (shared feeds, explanatory visuals, study tools: see the roadmap).

## Goals

- Read your feed on phone and desktop in one app that adapts to the screen.
- Two depths per paper in the feed (**Skim**: title, one line, why it was picked; **Read**: plus problem, what they did, what they found), and a full paper screen (adds strong points, weak points, questions to ask, a link to the paper).
- One-tap actions: **More like this** / **Less like this** (your existing up/down votes), **Save**, and **Summarize this** for papers that were collected but not summarized.
- **Saved** and **Search** views.
- "Why you're seeing this" in plain words, built from the ranking reasons the pipeline already stores.
- Plain language everywhere. Technical facts (model, read mode, body cut, section checks) live only in a collapsed "About this summary" corner.
- Light and dark themes that follow the device setting.
- Set up with `rpipe` commands only; nobody edits files.

## Non-goals (later projects, see roadmap)

Diagrams and generated hooks/key numbers (project 2); publishing, the directory, following, Mix (project 3); notes and link capture (project 4); video (project 5); study material (6); thesis help (7); "point at anything" import (8). The mockups show some of these; project 1 leaves their places out cleanly rather than showing empty placeholders.

## Anti-goals

- **Clickbait or engagement tricks.** It is a research and learning tool, not social media: no streaks, counts of unread, infinite autoplay or attention bait.
- **Anything shown that was not checked against the paper.** Project 1 shows only text the pipeline already stores and verifies (summary, critique, section tags). The rule for later generated text: it must point to the part of the paper it came from and pass a check against it, or it is not shown (owner, 2026-11-25).
- **Technical noise in the user-facing app.**
- **Reachable by anyone outside your own devices.**
- **The app running the AI model.** Heavy work goes through the request list.

## Constraints

- The app is an **app-group** module set (see `docs/guide/architecture.md`): it reaches data only through `library_api` and never imports pipeline modules; `tests/test_architecture.py` enforces this. `library_api` stays unable to reach `llm`/`backends`.
- App actions never take the pipeline lock; each write is one short transaction (Phase 7). The app keeps working during the nightly run.
- Every user setting has an `rpipe` command; every change ships with its `docs/guide/` page; tests run offline.
- Works on the hardware of club members: the server uses no GPU and little memory.
- The library stays local: no third-party requests from the pages (fonts and scripts are served by the app itself).

## Approaches

| | Approach | Trade-off | Verdict |
|---|---|---|---|
| **1** | Python web server that sends finished pages (server-rendered HTML, a little plain JavaScript for toggles and buttons), over `library_api`. | One language (Python) for the owner to understand and explain; no build step; looks exactly like the mockups since it is the same HTML/CSS. Opening a paper loads a new page. | **Chosen** (Claude's pick; owner accepted after the walkthrough). |
| 2 | Python data server plus a separate JavaScript app (React or similar). | Most app-like; easier path to a native phone app. Adds Node, npm and a build step: about double what the owner must understand; one more install for club members. | Not now. Approach 1 keeps it possible: the server speaks through `library_api`, so a JS front end can sit on the same layer later. |
| 3 | Native desktop/phone apps. | Separate install per device, far more work; the phone browser already reaches the computer. | Dropped. |

A static-files version (pipeline writes HTML each night, no server) was considered: it can show the feed but cannot receive a tap (vote, save, summarize), so it does not meet the goals.

## How it works

```
phone / laptop browser ──Tailscale──► app server (rpipe app) ──► library_api ──► vault.sqlite ◄── nightly pipeline
```

- **The app server** is a small Python web program that stays running on the owner's computer. It receives "show me today's feed", reads through `library_api`, fills a page template and sends the page back. Taps (vote, save, summarize) are small requests that call one `library_api` function each.
- **Reaching it:** like `rpipe serve`, it listens only on the machine's Tailscale address (or localhost), checked against the same allowlist (`serve.check_host`). There is no login; the threat model is the one `browsing.md` documents (anyone on your own tailnet can use it).
- **Always on:** `rpipe app install` writes a systemd user service (same pattern as `rpipe schedule install`) so the server starts with the computer and the phone works any time; `rpipe app uninstall` removes it; `rpipe app status` says whether it runs and prints the address; `rpipe app run` runs it in the foreground for trying it out. (Always-on is Claude's proposal; the owner did not object.)
- **Home-screen icon:** the pages carry a web app manifest and icon so the phone can "Add to Home Screen" and open it like an app.

### A day with it (worked example)

1. 03:00 the nightly run collects, ranks, summarizes the top picks and drains the request list, as today.
2. Morning, phone: tap the icon → today's feed in Skim. Tap **Read** to open every card one level deeper, or open one paper fully.
3. Tap **More like this** on the robot-agents paper → `library_api.vote(conn, "2611.31205", "up")`; the button shows it is on. Tomorrow's ranking leans that way (existing feedback loop).
4. Search "context compaction" → a collected but unsummarized paper → **Summarize this** → `library_api.request(conn, "summarize", {...})`; the card now says it will be summarized tonight. Next morning it has a summary.
5. Desktop browser, same address: wide layout (feed beside reader). The paper saved on the phone is already in Saved.

## Screens (from the mockups, look A, with B's topic colours)

- **Feed** (phone: one column with bottom navigation Feed / Saved / Search; desktop: navigation sidebar, feed column, reader pane). Shows the latest digest's papers in ranking order, with a date heading and a Skim / Read switch that remembers your choice on that device. Older digests: a "Earlier days" link at the bottom of the feed, one day per page.
- **Card (Skim):** topic label, paper title, one-line summary, "Why you're seeing this", actions (More like this, Less like this, Save, Open). **Read** adds problem / what they did / what they found, each with its section tag (§5.1).
- **Paper screen:** title, authors, arXiv id; one-line summary; the paper in brief with section tags; "How solid is it?" (strong points, weak points, questions to ask, from the critique); why you're seeing this; actions; "Read the paper" link (arxiv.org); collapsed "About this summary" (where it was read from, whole or in parts, model, date; any claims the check could not verify, listed plainly).
- **Not yet summarized** papers (in search, or when a summary failed): title, abstract, and **Summarize this** (or "Summary coming tonight" if already requested).
- **Saved** and **Search**: lists of cards in Skim form. Search uses the existing full-text index.
- **Look:** look A's structure and type; topic colours from look B (each of your profile topics gets a stable colour); look C's palette as the dark theme, chosen automatically from the device setting.

### "Why you're seeing this" in plain words

Built from the stored `triage_reasons`:

| Stored reason | Shown as |
|---|---|
| `topic: agentic workflow (0.80)` | you follow agentic workflow |
| `liked similar: 2611.25905` | it resembles a paper you liked (linked) |
| `HF upvotes: 56` | 56 people upvoted it this week |
| `followed author: X` | it's by X, whom you follow |
| `keyword: X` | it mentions X |
| `near your shelf: name` | it's close to "name" on your shelf |
| `disliked similar: ...` | not shown (it lowered the score; showing it confuses) |

Joined into one sentence. Unknown reason formats are skipped, never shown raw.

## Changes to existing code

- **`library_api`** gains what the screens need and lacks today: digest dates for "Earlier days"; plain-words reasons (as data: a list of reason objects with kind, label and optional linked paper id, so the page decides the wording); open summarize requests per paper (so cards can say "coming tonight"). Exact names are the builder's.
- **New app modules** (app group): the web server, page templates, static files (CSS, small JS, self-hosted fonts, icons, manifest). New CLI group `rpipe app run|install|uninstall|status`.
- **New optional dependency extra** `app` (web framework + template engine + server), like `serve` today; the nightly run never needs it.
- **Unchanged:** the pipeline, the schedule, the database layout (no schema change expected).
- **`rpipe serve`** stays until the app covers browsing; retiring it is a later decision.

## Docs

New guide page `docs/guide/app.md` (what it is, the front-desk/night-shift picture, setup, screens, security, where the code is), glossary entries (web server, template, manifest), updates to `architecture.md` (the app server in the app group), `browsing.md` (pointer to the app), `README.md` index.

## Decided by Claude (owner may override)

- Web framework, template engine and server library: the builder picks mainstream, well-documented ones; FastAPI/Starlette + Jinja2 + uvicorn is the expected choice (Pydantic is already used).
- Self-hosted fonts (open licences) instead of Google Fonts, so no page makes outside requests.
- Votes labelled "More like this / Less like this"; tapping the active one again clears it.
- Skim/Read choice remembered per device (browser storage), not in the database.
- Hook failure rule (for project 2): a failed check falls back quietly to the real title; the failure is listed under "About this summary".

## Settled after the builder check (Claude's calls unless marked)

- **Digest papers without a summary** (failed or not yet done) stay in the feed in their ranked place as an abstract card: title, abstract's first lines, why you're seeing this, and the summarize state below. They are not hidden.
- **Papers with no ranking reasons** (found by search, never ranked) simply leave out "Why you're seeing this".
- **Summarize states**, per paper: *none* → **Summarize this**; a `pending` or `running` request → "Summary coming tonight"; the latest request `failed` (after its 3 tries) → "Couldn't summarize this one" with **Try again** (adds a new request); a summary exists → the normal card. `library_api` gets a function returning the latest request status per paper id (the current `open_request_ids` returns ids only).
- **Section tags exist per field:** each summary field (`problem`, `approach`, `results`, `limitations`) and each critique strength/weakness stores its own `sections` list (checked on the 2026-11-25 rows). The tag shows the first section number (`§5.1`) or a range when they are consecutive; a field with no sections shows no tag.
- **Drive-by protection:** every request is rejected unless its `Host` header is the address the server listens on (blocks DNS rebinding), and every tap that changes something is rejected unless its `Origin` (or, if absent, `Referer`) is the app itself (blocks another web page making your browser vote). This goes beyond `rpipe serve`, which has neither.
- **Listen address and port:** default port 8002 (8001 is `rpipe serve`). `rpipe app run|install --host H --port P` set them; `install` writes them into the service unit, and `rpipe app status` prints them, so no file edits. The host allowlist check moves from `serve.py` into a small shared module both use, since `rpipe serve` is due to be retired.
- **Starting at boot:** the service unit starts after the network and Tailscale (`After=network-online.target tailscaled.service`, `Wants=network-online.target`) and restarts on failure every 10 s, so a slow Tailscale start just delays it. `install` refuses (with the reason) if the address cannot be found or checked right now, the same way `serve` does. A new small unit renderer is fine; reuse `schedule.py`'s quoting and install helpers where they fit.
- **Topic label and colour:** from the paper's first `topic: ...` reason (its name, cleaned up for display). Colour: a fixed palette of 8 accessible colours in both themes, picked by a stable hash of the topic name, so each topic keeps its colour across days and devices. No topic reason → no label. No profile reading needed, so `library_api` stays as it is on that front.
- **Architecture test:** the app server is a new package in the **app** group (e.g. `research_pipeline/webapp/`); `tests/test_architecture.py` lists its modules like the other groups. Templates and static files are not Python and are not checked by it.
- **Missing extra:** `rpipe app` without the `app` extra installed prints how to install it, following `rpipe serve`'s existing message pattern.

## Left to the builder

Module and template names, URL layout, how taps are sent (form posts or small fetch calls, as long as pages work without waiting on a build step), test strategy for pages (expected: the framework's test client against a fixture vault, offline), service unit details (reuse `schedule.py` helpers where they fit).
