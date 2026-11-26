# The app (`rpipe app`)

## What it does

A private web app over your paper library: your feed of recent picks, saved papers and search, laid out for a phone (one column, bottom navigation) and for a desktop (sidebar). You open it from the home-screen icon on your phone or at an address in any browser on your tailnet. It reads and writes the same vault database the nightly pipeline uses, so a vote you tap in the morning shapes tonight's ranking.

This page covers the server itself: how it is put together and how it protects itself. The screens (feed, paper, saved, search) are described as each is built; the design is in `docs/superpowers/specs/2026-11-25-app-project1-my-feed-design.md`.

## How it works

A picture: the nightly pipeline is a **night-shift librarian**. While you sleep it fetches, ranks, reads and summarizes, and shelves the results. The app is the **front desk**. It never goes off to read a paper; it hands you what the librarian shelved, and when you ask for something heavy ("summarize this one") it writes a note on the request list for the librarian to do tonight. The two meet only in the vault database.

```
phone / laptop browser ──Tailscale──► app server (rpipe app) ──► library_api ──► vault.sqlite ◄── nightly pipeline
```

Step by step, for one request:

1. The browser asks for a page, for example `GET /`.
2. **The guard** (`webapp/guard.py`, run by a middleware for every request) checks the `Host` header and, for anything that changes data, the `Origin` header. A failed check ends the request here (see Security).
3. The route handler opens a database connection (`server.get_conn`, one per request, always closed), calls `library_api` for data objects, and fills a Jinja2 template with them.
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

## Security

There is no login. The threat model is the one in [browsing.md](browsing.md): anyone who can reach the address can read and vote, and the address is only your own machine (localhost) or its Tailscale address, checked by the same allowlist as `rpipe serve` (`netguard`). The guard adds two checks that `rpipe serve` lacks:

- **Host check.** The `Host` header (port and IPv6 brackets removed, lower-cased) must be in the allowed set (the bind host; for a loopback bind also `localhost` and `127.0.0.1`), otherwise the answer is `400 Unknown host`. This blocks DNS rebinding: a malicious site you have open could otherwise point its own name at your tailnet address and have your browser read the app, but such a request carries the attacker's name in `Host`.
- **Same-origin check.** A request that is not GET or HEAD must carry an `Origin` header (or, if absent, a `Referer`) with scheme `http` and an allowed host, otherwise `403 Cross-site request refused`. This blocks cross-site request forgery: another site's page cannot make your browser vote or save, because the browser attaches that page's real origin. A request with neither header is refused too. Plain `http` is correct here because the tailnet already encrypts the traffic.

Worked example: you have the app open and also visit `evil.example`, whose page submits a hidden form to `http://100.64.0.5:8002/vote`. Your browser sends `Origin: http://evil.example`; the guard sees that host is not allowed and answers 403, so no vote is recorded.

What it does not defend against: another person on your tailnet. They can use the app, as with `rpipe serve`.

## How to run / check it

Tests (offline): `uv run pytest tests/test_webapp_server.py -q` covers static files, a wrong `Host` (400), a cross-site and a header-less POST (403), a same-origin POST reaching the router (404), IPv6 hosts, the `Referer` fallback and template escaping. The `rpipe app` commands that start the server come in a later task.

## Where the code is

- `src/research_pipeline/webapp/server.py`: `create_app`, `get_conn`, the shared `templates`.
- `src/research_pipeline/webapp/guard.py`: `check`, the Host and same-origin checks.
- `src/research_pipeline/webapp/templates/base.html`: the page frame and navigation.
- `src/research_pipeline/webapp/static/`: `app.css` (palette, type, layout), `app.js`, `icon.svg`, `manifest.webmanifest`, `fonts/`.
- `tests/test_webapp_server.py`.
