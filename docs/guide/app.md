# The app (`rpipe app`)

## What it does

A private web app over your paper library: your feed of recent picks, saved papers and search, laid out for a phone (one column, bottom navigation) and for a desktop (sidebar). You open it from the home-screen icon on your phone or at an address in any browser on your tailnet. It reads and writes the same vault database the nightly pipeline uses, so a vote you tap in the morning shapes tonight's ranking.

This page covers the server itself: how it is put together and how it protects itself. The screens (feed, paper, saved, search) are described as each is built; the feed is below; the design is in `docs/superpowers/specs/2026-11-25-app-project1-my-feed-design.md`.

## How it works

A picture: the nightly pipeline is a **night-shift librarian**. While you sleep it fetches, ranks, reads and summarizes, and shelves the results. The app is the **front desk**. It never goes off to read a paper; it hands you what the librarian shelved, and when you ask for something heavy ("summarize this one") it writes a note on the request list for the librarian to do tonight. The two meet only in the vault database.

```
phone / laptop browser ──Tailscale──► app server (rpipe app) ──► library_api ──► vault.sqlite ◄── nightly pipeline
```

Step by step, for one request:

1. The browser asks for a page, for example `GET /`.
2. **The guard** (`webapp/guard.py`, run by a middleware for every request) checks the `Host` header and, for anything that changes data, the `Origin` header. A failed check ends the request here (see Security).
3. The route handler opens a database connection (`deps.get_conn`, one per request, always closed; opened with `check_same_thread=False` because FastAPI may open, use and close it on different worker threads, which is safe as the request uses it one step at a time), calls `library_api` for data objects, and fills a Jinja2 template with them.
4. The template extends `base.html`, which supplies the head (viewport, manifest, icon, stylesheet), the navigation and the page frame. The page goes back to the browser.
5. The CSS, script, fonts and icon come from `/static`, served by the same server. A page makes no request to any other site, so nothing about what you read leaks to a font or script host.

The base layout: under 900 px wide, a single column with a bottom navigation bar (Feed, Saved, Search); at 900 px and wider, a left sidebar with the same three links. Light colours come from mockup look A, the dark palette from look C, picked automatically from the device setting (`prefers-color-scheme`). Eight topic colours (`--topic-0` to `--topic-7`) are defined for light and dark, each at least 4.5:1 contrast on the card background. `env(safe-area-inset-*)` padding and a band behind the status bar keep content clear of the notch and home indicator when launched from the home screen. The manifest (`name` "Research Feed", `display` "standalone") is what lets the phone offer "Add to Home Screen".

Fonts are self-hosted from `webapp/static/fonts/` (Literata 600 for titles, Inter 400/500/600 for text, IBM Plex Mono 500 for ids and tags, latin subset, from the `@fontsource` packages). All three are under the SIL Open Font License 1.1; the notices and licence text are in `static/fonts/OFL.txt`.

## Why it's built this way

- **FastAPI + Jinja2 + uvicorn.** Mainstream, well documented, and Pydantic (already used for the data objects) is native to FastAPI. They are an optional extra (`uv sync --extra app`, in the dev group too), so the nightly run never imports them.
- **Server-built pages, little JavaScript.** Pages work without scripts; JavaScript only enhances (taps without a reload). Rejected: a single-page app (a build step and a second language for a personal tool) and static HTML files written nightly (cannot receive a tap).
- **`create_app` takes the allowed hosts instead of computing them.** The caller (the `rpipe app` command) knows the bind address and builds the set; the server and guard stay simple and testable with a fake host.
- **One connection per request.** SQLite connections are cheap, and this avoids sharing one across threads. The app never takes the pipeline lock; each write is one `library_api` call.
- **Self-hosted fonts instead of Google Fonts.** Works offline on the tailnet and sends no information outward.

## The feed

**What it does.** `GET /` shows the latest digest as cards in ranking order under a heading such as "Wednesday, Nov 25". `GET /day/2026-11-24` shows one earlier day (an unknown or malformed date is a 404 page). At the bottom, "Earlier days" links to the next older digest, one day per page. With no digest at all you see "No papers yet" and "Your first feed appears after the next nightly run."

**How it works.** `routes_feed.py` asks `library_api` for the digest dates, the day's papers and each paper's summarize state, then hands every paper to `present.card`, which turns the data object into a flat `CardData` the template can print without logic:

1. The stored reasons (`topic: agentic workflow (0.80)`, `HF upvotes: 56`) become reason objects (`library_api.plain_reasons`), and `why_sentence` joins them into one sentence: one reason `x.`, two `x and y.`, three or more `x, y, and z.` No reasons, no sentence.
2. The topic label is the first topic reason with its first letter upper-cased. Its colour is `crc32(topic.lower()) % 8`, an index into the eight `--topic-N` colours, so "agentic workflow" is the same colour on every day and device.
3. Each summary field (problem, approach, results) carries a section tag from `section_tag`: the first word of a section title counts as a number when it looks like `5.1`, `IV-B` or `A.1`; titles like "Abstract" are ignored. One number gives `§5.1`, several give `§5.1–5.2`.
4. A digest paper with no summary stays in its ranked place as an abstract card: the first 300 characters of the abstract, cut at a word with "…", and a control that depends on the latest summarize request: none gives "Summarize this", pending or running gives "Summary coming tonight", failed gives "Couldn't summarize this one" and "Try again". The buttons carry `data-action="summarize"` and are wired up in a later task.

Worked example: a paper with reasons `topic: agentic workflow (0.80)` and `HF upvotes: 56` is shown with the label "Agentic workflow" and "Why you're seeing this: you follow agentic workflow and 56 people upvoted it on Hugging Face."

## Skim and Read

Every card always contains the Skim fields (topic, title, one line, why) and, in a `<div class="read-more">`, the Read fields (problem, what they did, what they found, each with its section tag). The page's `<body data-depth="skim|read">` decides which show: CSS hides `.read-more` when it is `skim`. The Skim / Read buttons (`aria-pressed` marks the active one) are handled by `app.js`, which sets the attribute and saves the choice in the browser's `localStorage["depth"]`, wrapped in try/catch because private windows can refuse storage (the buttons then still work for that page). Without JavaScript the page stays in Skim. An unsummarized card has no problem/approach/results, so it shows the abstract excerpt at both depths.

Why this way: the choice lives in the browser, not the database, because it is a per-device taste (a phone may want Skim, a desktop Read), and a CSS switch means no reload and no server round trip. Rejected: two separate pages (loses your place) and fetching the Read text on demand (needless delay for text already on the page).

## Why you're seeing this

Built from the stored reasons; unknown reason formats are skipped, never shown raw, and "disliked similar" is not shown at all (it lowered the score, and showing it would confuse).

| Stored reason | Shown as |
|---|---|
| `topic: agentic workflow (0.80)` | you follow agentic workflow |
| `liked similar: 2611.25905` | it resembles a paper you liked |
| `HF upvotes: 56` | 56 people upvoted it on Hugging Face |
| `followed author: X` | it's by X, whom you follow |
| `keyword: X` | it mentions X |
| `near your shelf: name` | it's close to "name" on your shelf |

## Reading a paper

**What it does.** `GET /paper/{id}` shows one paper: topic, title, authors ("A, B, C and 2 others" when there are more than three), a "Read the paper" button to the original, the one-line summary, the problem / what they did / what they found fields, then Strengths, Weaknesses and "Questions to ask" from the critique, and "Why you're seeing this". Each claim carries its section tag (for example `§4`). A paper with no summary yet shows its full abstract and the same "Summarize this" control as a card. An id with a version (`2611.25005v2`) or an arXiv URL form works; an unknown or malformed id gives the 404 page "That paper isn't in your library." inside the normal app frame (the same page serves any other missing address). Cards on the feed, Saved and Search link to this page from their titles.

**How it works.** `routes_pages.paper` runs the id through `ids.normalise_paper_id`, loads the paper with `library_api.get_paper`, and asks `present.paper_detail` to shape it into a `DetailData` (the card plus authors, critique, questions, "about" lines). Two small clean-ups happen there: each critique question loses a trailing `[sections: ...]` note (the model sometimes appends one; it is noise on a screen), and claims get section tags as on cards. The route also loads that paper's digest day and passes the titles to the template.

**Feed beside reader.** From 1100 px wide, the page is two columns: the paper's digest day as a compact list on the left (the current paper highlighted, every item a link to its own page) and the reader on the right, so a desktop feels like a mail client. Below 1100 px the list is hidden by CSS and the reader fills the width; reading text never exceeds 680 px, which keeps lines comfortable.

Why this way: the list is plain links, so it works without JavaScript and each paper keeps its own address you can bookmark. Rejected: loading the paper into the feed page with JavaScript (breaks back/forward and bookmarks).

## Saved and Search

`GET /saved` lists saved papers as cards, newest save first, or "Nothing saved yet." `GET /search?q=word` searches titles and abstracts (`library_api.search_papers`, which only uses the words you type, so quotes or operators never cause an error) and shows up to 30 results as cards. An empty query shows only the search box; no match shows `No papers match "q".` The query is printed escaped, so typing `<b>` shows the characters rather than making text bold. Both pages reuse the card template and the Skim / Read switch.

## Voting, saving and asking for a summary

**What it does.** Every card and paper page has three buttons: "More like this", "Less like this" and "Save" (it then reads "Saved"). A paper with no summary also shows "Summarize this". Tapping changes things at once; there is no page reload.

**How it works.** The buttons are plain `<button data-action=...>` elements; `static/app.js` sends one `fetch` POST with a JSON body to a small endpoint and updates the button from the answer:

| Tap | Request | Answer |
|---|---|---|
| More / Less like this | `POST /api/paper/{id}/vote` `{"direction": "up" \| "down" \| "clear"}` | `{"vote": 1 \| -1 \| 0}` |
| Save / Saved | `POST /api/paper/{id}/save` `{"saved": true \| false}` | `{"saved": true \| false}` |
| Summarize this | `POST /api/paper/{id}/summarize` | `{"state": "pending" \| "running"}` |

Each endpoint is one `library_api` call (`vote`, `save`, `request`), so the app writes to the library the same way the command line does and never takes the pipeline lock. The id may be written any way the paper page accepts (`2611.25005v2` works). Tapping the choice that is already active ("More like this" while it is lit) sends `clear`, so a second tap takes the vote back; the server just records what it is sent. After "Summarize this" the card text becomes "Summary coming tonight" and the button disappears; the request waits in the request list until the nightly run (or `rpipe requests run`) does it, see [running.md](running.md), "The request list". If the summary failed, "Try again" posts the same endpoint (a repeat while one is waiting returns the same request, so tapping twice never queues two).

Errors are JSON `{"error": "..."}` and the page shows the text in a small line under the buttons: `404` "That paper isn't in your library." for an unknown id, `400` for a body that is not valid (a direction other than up, down or clear; `saved` not true or false), `503` "Your library is busy. Try again in a moment." when the database is locked (for example while the nightly run is writing). Every path under `/api/` answers 404 in JSON too; pages keep the HTML "not found" page. Like every non-GET request these need a same-origin `Origin` header (see Security), which the browser adds by itself.

**Why this way.** JSON over `fetch` rather than HTML forms: the page stays where you are in the feed. The server does not toggle for you ("vote again means clear"): the page knows what it shows, and a server that only records what it is told is simple and cannot get out of step when two tabs are open. Rejected: optimistic updates (changing the button before the answer): a failed save would then look like it worked.

**Check it.** `uv run pytest tests/test_webapp_actions.py -q` (offline).

## About this summary

At the foot of a paper with a summary, a collapsed "About this summary" lists in plain words how it was made: `Read from the full paper` or `Read from the abstract only`; `Read in N parts` for long papers read in pieces; `Some of the paper was too long and was cut`; `Summarized by {model} on {date}` (taken from the stored record `library_api.summary_provenance`, and left out for older summaries that have none); and one `Could not check: "..."` line for each claim whose quoted section could not be verified against the paper (see [summaries.md](summaries.md)). Model names appear only here, so the rest of the app stays free of jargon.

## Security

There is no login. The threat model is the one in [browsing.md](browsing.md): anyone who can reach the address can read and vote, and the address is only your own machine (localhost) or its Tailscale address, checked by the same allowlist as `rpipe serve` (`netguard`). The guard adds two checks that `rpipe serve` lacks:

- **Host check.** The `Host` header (port and IPv6 brackets removed, lower-cased) must be in the allowed set (the bind host; for a loopback bind also `localhost` and `127.0.0.1`), otherwise the answer is `400 Unknown host`. This blocks DNS rebinding: a malicious site you have open could otherwise point its own name at your tailnet address and have your browser read the app, but such a request carries the attacker's name in `Host`.
- **Same-origin check.** A request that is not GET or HEAD must carry an `Origin` header (or, if absent, a `Referer`) with scheme `http` and an allowed host, otherwise `403 Cross-site request refused`. This blocks cross-site request forgery: another site's page cannot make your browser vote or save, because the browser attaches that page's real origin. A request with neither header is refused too. Plain `http` is correct here because the tailnet already encrypts the traffic.

Worked example: you have the app open and also visit `evil.example`, whose page submits a hidden form to `http://100.64.0.5:8002/vote`. Your browser sends `Origin: http://evil.example`; the guard sees that host is not allowed and answers 403, so no vote is recorded.

What it does not defend against: another person on your tailnet. They can use the app, as with `rpipe serve`.

## Setting it up

**What it does.** Two ways to run the app: `rpipe app run` in a terminal (stops when you close it; good for trying it), or `rpipe app install` to keep it running in the background, started at login, so the home-screen icon always works.

**How it works.**

1. Install the app extra once: `uv tool install --editable '.[embed,serve,app]' --force` (from the repo). In the repo itself, `uv sync --extra embed --extra serve --extra app`. Without it, every `rpipe app` command that needs the web libraries stops with this exact instruction and writes nothing.
2. Try it: `rpipe app run`. It checks the extra, opens the vault once (which applies any database upgrade), works out the address, prints `Your feed is at http://100.64.0.5:8002/ (only your own devices can reach it)` and serves until Ctrl+C. It does not take the pipeline lock, so it can run while the nightly pipeline does.
3. Keep it on: `rpipe app install`. It resolves and checks the address *now* (a refused address stops here, before anything is written), asks systemd whether its user manager is reachable, writes `~/.config/systemd/user/research-pipeline-app.service`, reloads systemd and enables the service at once. It prints the address and a reminder of `loginctl enable-linger $USER` (so the service survives logging out; the command never runs it for you).
4. Open the address it printed, with the number, on your phone or laptop. Tailscale machine names (such as `machine.tailnet.ts.net`) are not accepted in this version: the guard allows only the exact address the app listens on. On a phone, use the browser's Add to Home Screen (iPhone: Share, then Add to Home Screen; Android: the menu, then Add to Home screen) to get the icon.
5. `rpipe app status` says `Running at http://...`, `Installed but not running. Check: journalctl --user -u research-pipeline-app`, or `Not installed. Set it up with: rpipe app install`. `rpipe app uninstall` stops the service and deletes the file.

The unit is small: it sets `RESEARCH_VAULT`, runs `rpipe app run --host H --port P`, restarts itself 10 seconds after a crash, and starts after the network and Tailscale are up. `--host` and `--port` (default 8002) can be given to `run` and `install`; the default host is this machine's Tailscale address, and only localhost, loopback and Tailscale addresses are accepted (the `netguard` allowlist). The set of Host names the guard accepts is built from the bind address by `service.allowed_hosts`: the bind address itself, plus `localhost`, `127.0.0.1` and `::1` when you bind to a loopback address.

**Why it's built this way.**

- **A user service, as the nightly timer is.** No root, same tools, same linger rule. Rejected: a system service (needs sudo) and a second always-on process of our own (systemd already restarts, logs and starts at login).
- **The address is fixed in the unit.** It is read back by `status` from the unit file, so status works even when Tailscale is down. If your Tailscale address ever changes, run `rpipe app install` again.
- **Checks before writes.** The extra, the address and systemd are all checked first, so a failed install leaves nothing half-done.
- **Two services now.** The nightly timer (`research-pipeline.service`/`.timer`, see [scheduling.md](scheduling.md)) and this always-on one (`research-pipeline-app.service`) are independent; removing one never touches the other.

**How to run / check it.** `rpipe app run`, then open the printed address. Tests: `uv run pytest tests/test_webapp_service.py tests/test_cli_app.py -q` (offline: fake `systemctl` runner, `uvicorn.run` replaced, no real server or unit file).

**Where the code is.** `src/research_pipeline/webapp/service.py` (`render_service`, `install`, `remove`, `address`, `is_active`, `allowed_hosts`) and the `app` commands in `src/research_pipeline/cli.py` (`app_run`, `app_install`, `app_uninstall`, `app_status`).

## How to run / check it

Tests (offline): `uv run pytest tests/test_webapp_server.py -q` covers static files, a wrong `Host` (400), a cross-site and a header-less POST (403), a same-origin POST reaching the router (404, JSON under `/api/`), IPv6 hosts, the `Referer` fallback and template escaping. The `rpipe app` commands are covered in "Setting it up".

## Where the code is

- `src/research_pipeline/webapp/server.py`: `create_app` and the 404 page handler. `deps.py`: `get_conn` and the shared `templates` (routers import these from `deps`; `server` re-exports them).
- `src/research_pipeline/webapp/guard.py`: `check`, the Host and same-origin checks.
- `src/research_pipeline/webapp/templates/base.html`: the page frame and navigation.
- `src/research_pipeline/webapp/static/`: `app.css` (palette, type, layout), `app.js`, `icon.svg`, `manifest.webmanifest`, `fonts/`.
- `src/research_pipeline/webapp/present.py`: `why_sentence`, `topic_of`, `topic_color`, `section_tag`, `day_heading`, `card`/`CardData` (pure shaping).
- `src/research_pipeline/webapp/routes_feed.py`: the `/` and `/day/{date}` routes.
- `src/research_pipeline/webapp/routes_actions.py`: the `/api/paper/{id}/vote`, `save` and `summarize` endpoints; `templates/_actions.html` is the button row; the click handling is the second block of `static/app.js`.
- `src/research_pipeline/webapp/routes_pages.py`: `/paper/{id}`, `/saved`, `/search`; `present.paper_detail` shapes the paper page.
- `src/research_pipeline/webapp/templates/`: `feed.html`, `_card.html`, `_empty.html`, `paper.html`, `list.html` (Saved and Search), `404.html`.
- `src/research_pipeline/webapp/service.py`: the always-on service (see Setting it up).
- `tests/test_webapp_service.py`, `tests/test_cli_app.py`, `tests/test_webapp_server.py`, `tests/test_webapp_present.py`, `tests/test_webapp_feed.py`, `tests/test_webapp_pages.py`.
