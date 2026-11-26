# The app (`rpipe app`)

## What it does

A private web app over your paper library: your feed of recent picks, saved papers and search, laid out for a phone (one column, bottom navigation) and for a desktop (sidebar). You open it from the home-screen icon on your phone or at an address in any browser on your tailnet. It reads and writes the same vault database the nightly pipeline uses, so a vote you tap in the morning shapes tonight's ranking.

This page covers the server itself: how it is put together and how it protects itself. The screens (feed, paper, saved, search) and the buttons are described in the sections below; the design is in `docs/superpowers/specs/phase-08-your-feed-design.md`.

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
5. The CSS, script, fonts and icons come from `/static`, served by the same server. A page makes no request to any other site, so nothing about what you read leaks to a font or script host.

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
4. A digest paper with no summary stays in its ranked place as an abstract card: the first 300 characters of the abstract, cut at a word with "…", and a control that depends on the latest summarize request: none gives "Summarize this", pending or running gives "Summary coming tonight", failed gives "Couldn't summarize this one" and "Try again". The buttons carry `data-action="summarize"`; `static/app.js` posts the request (see "Voting, saving and asking for a summary").

Worked example: a paper with reasons `topic: agentic workflow (0.80)` and `HF upvotes: 56` is shown with the label "Agentic workflow" and "Why you're seeing this: you follow agentic workflow and 56 people upvoted it on Hugging Face."

## Skim and Read

Every card always contains the Skim fields (topic, title, hook or one line, key numbers, why) and, in a `<div class="read-more">`, the Read fields (problem, what they did, what they found, each with its section tag; diagrams are shown only on the paper screen, see "Hook, key numbers and the diagram"). The page's `<body data-depth="skim|read">` decides which show: CSS hides `.read-more` when it is `skim`. The Skim / Read buttons (`aria-pressed` marks the active one) are handled by `app.js`, which sets the attribute and saves the choice in the browser's `localStorage["depth"]`, wrapped in try/catch because private windows can refuse storage (the buttons then still work for that page). Without JavaScript the page stays in Skim. An unsummarized card has no problem/approach/results, so it shows the abstract excerpt at both depths.

### How a summary field is laid out

**What it does.** Each field (The problem, What they did, What they found) is its own block: the label on a line of its own, then the text. A field of two or more sentences is shown as short points, one sentence each, with the section tag after the last point. On a card the Read fields sit below a thin line, apart from the Skim part. (Phase 8.1: the owner found the fields hard to read as single dense blocks.)

**How it works.** The template filter `sentences` (`present.sentences`, registered in `deps.py`) splits the field's text; the macro in `templates/_field.html` draws one field and is shared by the card and the paper screen. A sentence break is a `.`, `!` or `?` (perhaps inside a closing bracket or quote) followed by a space and a capital letter, digit, bracket or quote. A break right after a common abbreviation (`e.g.`, `i.e.`, `et al.`, `vs.`, `Fig.`, `Eq.`, `Sec.` and a few more) or a single initial (`J.`) is undone. Example:

```
"Across 25 tasks it took 28% less time. In 8 team tasks it won every time."  §5.1
→ • Across 25 tasks it took 28% less time.
  • In 8 team tasks it won every time. §5.1
```

`1.37x` and `13.8` never split, because no space follows their full stop.

**Why this way.** The splitter is careful on purpose: a missed split leaves one block, as before, while a wrong split would cut a sentence in two, so it splits only when it is sure. On the 58 summaries in the owner's vault (2026-11-25) it turned 151 fields into two points and left 23 single sentences, with no sentence cut. Rejected: asking the model for bullet points (that changes the prompts and needs a gold-set check, so it belongs with Phase 9's plain-words rewrite) and a "show more" fold per field (one more tap for text already on screen; Read mode is already the fold). The wording itself is still the model's, often long and full of the paper's jargon; Phase 9 rewrites it in plain words.

**How to check it.** `tests/test_webapp_present.py` (`test_sentences_*`: sentence ends, abbreviations, initials, decimals, empty text) and `tests/test_webapp_pages.py` (`test_long_field_shows_as_points_with_tag_on_last`).

Why this way: the choice lives in the browser, not the database, because it is a per-device taste (a phone may want Skim, a desktop Read), and a CSS switch means no reload and no server round trip. Rejected: two separate pages (loses your place) and fetching the Read text on demand (needless delay for text already on the page).

## Mine, Following and Mix (Phase 10)

**What it does.** Once you follow someone's feed (`rpipe share follow`, see the sharing guide), a three-way switch appears at the top of the feed: **Mine** (your own digest, exactly as before), **Following** (only the posts of the feeds you follow) and **Mix** (your papers plus the feeds you have in your mix, one card per paper). With no follows the switch is not drawn and every page is identical to before. A card from a followed feed has one label line above the title: the names of whose pick it is, then the topic, for example `Alex M. · Robotics`. In Mix, your own paper reads `You · Security`, or `You · Alex M. · Security` when Alex posted it too. Mine never has the label line. On a followed card the "why" reads `Alex M.'s pipeline picked it because they follow robotics.` (their reasons, worded about them, not "you follow"), and it shows the publisher's summary and no "Summarize this" button, because only the night run summarizes and a followed paper already arrives summarized.

**How it works.**

1. `GET /?view=following` (or `mix`; `/day/{day}?view=...` for an older day). `routes_feed._view_of` accepts `mine`, `following` or `mix`; anything else, or any choice while you follow nobody, means `mine`.
2. The day list and the cards come from `library_api.following_days` / `following_posts` or `mix_days` / `mix_posts`; `present.followed_card` turns each post into the same `CardData` as a normal card and adds `sources` (the names) and `followed`.
3. `_card.html` prints the label line from `sources` and the topic, and skips the Summarize control when `followed` is true. "Earlier days" and the paper page's Back link carry the `?view=` choice (Mine keeps the plain addresses).
4. `app.js` remembers the choice in `localStorage["view"]` (this device only) and, when you open `/` with no `?view=`, jumps once to the remembered view.
5. `/paper/{id}` shows your own paper when it has your own summary. Otherwise, if a followed feed posted it (`library_api.followed_post`), it shows the publisher's summary under the label line with the publisher wording. A paper you have never copied in can still be opened this way.
6. The vote and save buttons on a followed card use the same `/api/paper/{id}/vote` and `save` routes; `library_api` copies the paper into your library on first use (a `source='feed'` row), so the vote counts for your own ranking. Such papers then appear in Saved with the publisher's summary.

**Why it's built this way.** The switch is links (`?view=`), so it works without JavaScript and each view has an address you can bookmark; the remembered choice is only a convenience. Followed posts reuse the card template instead of a second one so the look stays the same. Rejected: a separate Following screen (two look-alike pages to keep in step) and a Summarize button on followed posts (it would queue work for a paper that is not in your library yet).

**How to check it.** `uv run pytest tests/test_webapp_share.py -q` (switch hidden without follows, labels, escaping of titles, unsafe links, paper page, no Summarize button, voting on a followed post).

**Where the code is.** `webapp/routes_feed.py` (`_view_of`, `_page`), `webapp/present.py` (`followed_card`, `why_sentence(..., you=False)`), `webapp/routes_pages.py` (`paper`, `_with_publisher_summary`), `templates/_view_switch.html`, `_card.html`, `paper.html`, `static/app.js`.

## People (Phase 10)

**What it does.** The **People** page (the new link after Search) is where you manage sharing from the app. **Your feed** says whether you publish: if not, "Your feed is private" with the command `rpipe share setup`; if so, your display name, the address others follow, and the command `rpipe share stop`. **Feeds like yours** lists the directory (most shared topics first): an initial, the name, their topics, "About N papers a day · shares N of your topics", and a Follow button. Tap a row to open "Recently in <Name>'s feed" (their latest titles with one line each). A **Following** section lists the feeds you follow, each with an "In Mix" tick, and "<Name> stopped publishing" when the feed has gone quiet or away. On a desktop-width screen the Mix view also shows a **Showing** row: You, plus one tick per followed feed (ticking one reloads Mix without that feed).

**Follow from the app means posts arrive tonight.** The button records the follow; it does not download anything. The nightly run fetches the feed, so the button then reads "Following · posts arrive tonight" and stays that way on later visits until the first fetch has happened. Following with `rpipe share follow` behaves the same.

**How it works.**

1. `rpipe app run` (and the service unit, which runs it) reads your profile topics and your sharing settings and hands them to `create_app(..., my_topics=..., share=...)`. The app never loads the profile or config itself. They are read at start-up, so `rpipe share setup`, `rpipe share set-name` and `rpipe share stop` restart the background app themselves when it is installed and running (they print "Restarted the app so it shows the change."; with no installed app they do nothing). A run started by hand with `rpipe app run` reads them again when you restart it. A missing or broken `profile.yaml` only means no topic counts on this page; the app still starts.
2. `GET /people` (`routes_people.py`) calls `library_api.directory(conn, my_topics)` and `library_api.follows(conn)` and renders `people.html`.
3. The Follow button posts to `POST /api/feed/{handle}/follow` (`library_api.follow_request`); an unknown handle answers 404 `{"error": ...}`.
4. A Mix tick posts `{"on": true|false}` to `POST /api/feed/{handle}/mix` and the route finds your follow with that handle (`library_api.set_in_mix`). The route takes the handle because a feed id such as `github:alex/rpipe-feed` contains `:` and `/`, which do not fit a URL path. A follow added by URL with no handle therefore has no tick.
5. Both routes go through the same-origin guard like the vote and save routes; `app.js` shows an error under the row when a call fails and puts the tick back.

**Why it's built this way.** The page reuses the card, button and muted-text styles; only a few rules were added. The settings stay in the CLI (`rpipe share ...`); the page shows the exact command rather than running GitHub sign-in from a browser. Rejected: the app reading config itself (it would couple the web layer to the profile and config loaders), and a feed-id URL for the mix route (it needs escaping on both ends).

**How to check it.** `uv run pytest tests/test_webapp_people.py -q`.

**Where the code is.** `webapp/routes_people.py`, `templates/people.html`, `templates/_showing.html`, `server.py` (`create_app`), `cli.py` (`app_run`), the People block at the end of `static/app.js` and `static/app.css`.

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

**What it does.** `GET /paper/{id}` shows one paper: topic, title, authors ("A, B, C and 2 others" when there are more than three), a "Read the paper" button to the original (shown only when the paper's address starts with `http://` or `https://`; anything else, such as a `javascript:` address, is left out so a bad record cannot run code when tapped), the hook (or the one-line summary when there is no hook) with key-number chips, a "How it works" diagram when there is one, the problem / what they did / what they found fields (laid out as described in "How a summary field is laid out"), then the critique under the heading "How solid is it?" (subheadings "Strong points", "Weak points" and "Questions to ask"), and "Why you're seeing this". Each claim carries its section tag (for example `§4`). A paper with no summary yet shows its full abstract and the same "Summarize this" control as a card. An id with a version (`2611.25005v2`) or an arXiv URL form works; an unknown or malformed id gives the 404 page "That paper isn't in your library." inside the normal app frame (the same page serves any other missing address). Cards on the feed, Saved and Search link to this page from their titles.

**How it works.** `routes_pages.paper` runs the id through `ids.normalise_paper_id`, loads the paper with `library_api.get_paper`, and asks `present.paper_detail` to shape it into a `DetailData` (the card plus authors, critique, questions, "about" lines). Two small clean-ups happen there: each critique question loses a trailing `[sections: ...]` note (the model sometimes appends one; it is noise on a screen), and claims get section tags as on cards. The route also loads that paper's digest day and passes the titles to the template.

**Feed beside reader.** From 1100 px wide, the page is two columns: the paper's digest day as a compact list on the left (the current paper highlighted, every item a link to its own page) and the reader on the right, so a desktop feels like a mail client. Below 1100 px the list is hidden by CSS and the reader fills the width; reading text never exceeds 680 px, which keeps lines comfortable.

Why this way: the list is plain links, so it works without JavaScript and each paper keeps its own address you can bookmark. Rejected: loading the paper into the feed page with JavaScript (breaks back/forward and bookmarks).

## Hook, key numbers and the diagram (Phase 9)

**What it does.** A summarized paper now opens with its main finding, not just a topic sentence: the **hook** (one plain line such as "Robots finish 28% sooner", with its section tag), up to three **key numbers** as small chips under it (`28% less time`), and, on the paper screen only, a small **diagram** of how the method fits together when there is one. On a card the hook takes the place of the one-line summary; with no hook (an older summary, or the hook failed its checks) the one-line summary shows as before. The paper screen does the same under the title and adds a "How it works" section with the diagram before the brief.

```
Card, Skim                               Card, Read adds
AGENT-BASED DEVELOPMENT                  THE PROBLEM / WHAT THEY DID / WHAT THEY FOUND
ACAP: Asynchronous Coding Agents         ...
One robot, many agents: finishes 28%     [  Coordinator  ]      (accent box)
sooner  §5.1                                  |
( 28% less time ) ( 25 tasks )           [Plan] [Code] [Test]
Why you're seeing this: ...              One coordinator hands work to three agents
```

**How it works.** `present.card` copies `hook` and `key_numbers` from the summary and `diagram` from `library_api`'s paper view into `CardData`; nothing is computed there. The templates print them through one macro, `headline` in `_field.html` (hook or one-liner, then the chips), so a card and the paper screen cannot drift apart. The chips sit outside `.read-more`, so they show in Skim too. The diagram is drawn only on the paper screen, under "How it works" (Phase 9.2: the model draws a diagram for nearly every paper, including literature reviews and theory papers, where it shows how the work was done rather than an idea; kept off the feed so a dull one never takes space there). The diagram is drawn by `webapp/diagram_svg.py`, registered in `deps.py` as the template filter `diagram_svg`:

1. The stored diagram is data: a shape (`parts`, `steps` or `loop`), 2 to 8 nodes (a short label each), the arrows between them and a caption.
2. The shape picks a layout, all inside a 320-unit-wide `viewBox` so the picture scales to the card:
   - `parts`: the node with the most outgoing arrows (the first on ties) is the coordinating box on top, filled with the accent colour; the other nodes sit in a row below (two rows above four), joined by grey connector lines.
   - `steps`: boxes top to bottom with a down arrow between each, in the order the arrows lead; nodes no arrow reaches are added at the end.
   - `loop`: chips top to bottom with arrows, and one return arrow from the last back to the first.
3. A label wraps to at most two lines by word (a still-too-long line ends in "…"), so text stays about 12 units high and legible at phone width.
4. The result is one `<figure class="diagram">` holding an inline `<svg role="img" aria-label="caption">` and a `<figcaption>`. Every piece of text is escaped, so a label such as `<b>` shows as text.

Worked example: `parts` with nodes Coordinator, Plan, Code, Test and arrows from Coordinator to the other three. Coordinator has three outgoing arrows, so it goes on top in the accent style; Plan, Code and Test share one row of three boxes (96 units wide each), with a stem down from Coordinator, a bar across, and a short drop into each box.

**Why it's built this way.**
- **Our own SVG, no JavaScript library.** A charting or Mermaid library would add a large script to every page and the pages promise no new scripts and no third-party requests. About 150 lines of Python give the exact look wanted.
- **Checkable data, not a drawing.** The model answers boxes and arrows; code checks every box and edge (see `summaries.md`) before it is stored. The drawer is also built so that no valid graph can crash it: it follows arrows with a "seen" set (a cycle cannot loop), draws isolated nodes, and `parts` ignores the arrows' exact targets and connects the top box to the rest.
- **Look A.** Rounded boxes, the coordinating box in the accent colour, grey lines, small muted caption: the style chosen from the mockups.
- **Light and dark from CSS variables.** The SVG carries classes (`d-node`, `d-top`, `d-line`, ...) and `app.css` colours them with `--accent`, `--surface`, `--ink` and `--muted`, so the same markup is right in both themes (text on the accent box uses `--surface`, which flips with the theme).
- Rejected: drawing as an image file (cannot follow the theme, extra request); showing the diagram in Skim (cards would get tall); showing it in Read mode on cards (Phase 9 did; dropped in 9.2 because many diagrams for reviews and theory papers say little, owner's choice).

**Hidden when the setting is off (Phase 9.3).** The paper route (`_diagrams_off` in `routes_pages.py`) reads `diagrams` from the vault's `config.yaml` on every request; when it is `off`, the paper's diagram is dropped before the page is built, so "How it works" does not show. Worked example: `rpipe model set-diagrams off`, reload a paper page: the diagram is gone; `rpipe model set-diagrams auto`, reload: it is back. Why: the owner found the diagrams pointless (labels in boxes that repeat the summary) and wanted them off, not deleted, because diagrams taken from the paper's own figures may come later. Reading the file per request means the switch works without restarting the app; a missing or unreadable `config.yaml` counts as not off, so a settings problem never breaks the paper page. Rejected: deleting stored diagrams (cannot be undone) and checking only at startup (the app would need a restart after every change).

**How to check.** `uv run pytest tests/test_webapp_diagram_svg.py tests/test_webapp_feed.py tests/test_webapp_pages.py -q`: the three shapes render with every label, labels are escaped, cycles, isolated nodes, self-arrows and dense graphs at 2 to 8 nodes all draw; the card shows the hook instead of the one-liner and falls back; chips are outside `read-more`; cards never carry a diagram; the paper page has "How it works" only when there is a diagram and the `diagrams` setting is not `off`. Then open a summarized paper in the app after a night with diagrams on.

**Where the code is.** `src/research_pipeline/webapp/diagram_svg.py` (`render`), `templates/_field.html` (`headline`), `_card.html`, `paper.html`, the "Hook, key numbers and diagram" block at the end of `static/app.css`, `present.py` (`CardData.hook`, `key_numbers`, `diagram`), `routes_pages.py` (`_diagrams_off`).

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

- **Host check.** The `Host` header (port and IPv6 brackets removed, lower-cased) must be in the allowed set (the bind host; for a loopback bind also `localhost`, `127.0.0.1` and `::1`), otherwise the answer is `400` with a small friendly page saying this address is not the one the app listens on and, when the server knows it, "Open it at http://HOST:PORT/" (`rpipe app run` and the service pass it in; a machine name like `name.tailnet.ts.net` lands here, because only the numeric address is allowed). This blocks DNS rebinding: a malicious site you have open could otherwise point its own name at your tailnet address and have your browser read the app, but such a request carries the attacker's name in `Host`.
- **Same-origin check.** A request that is not GET or HEAD must carry an `Origin` header (or, if absent, a `Referer`) with scheme `http` and an allowed host, otherwise `403 Cross-site request refused`. This blocks cross-site request forgery: another site's page cannot make your browser vote or save, because the browser attaches that page's real origin. A request with neither header is refused too. Plain `http` is correct here because the tailnet already encrypts the traffic.

Worked example: you have the app open and also visit `evil.example`, whose page submits a hidden form to `http://100.64.0.5:8002/api/paper/2611.25005/vote`. Your browser sends `Origin: http://evil.example`; the guard sees that host is not allowed and answers 403, so no vote is recorded. (The Origin check compares the host only, not the port: another program on the same machine or tailnet host could pass it. That is acceptable under this threat model, because anyone who can run code there or reach the address could already read and vote directly; the check exists to stop *other websites* in your browser.)

What it does not defend against: another person on your tailnet. They can use the app, as with `rpipe serve`.

## Setting it up

**What it does.** Two ways to run the app: `rpipe app run` in a terminal (stops when you close it; good for trying it), or `rpipe app install` to keep it running in the background, started at login, so the home-screen icon always works.

**How it works.**

1. Install the app extra once: `uv tool install --editable '.[embed,serve,app]' --force` (from the repo). In the repo itself, `uv sync --extra embed --extra serve --extra app`. Without it, every `rpipe app` command that needs the web libraries stops with this exact instruction and writes nothing.
2. Try it: `rpipe app run`. It checks the extra, opens the vault once (which applies any database upgrade), works out the address, prints `Your feed is at http://100.64.0.5:8002/ (only your own devices can reach it)` and serves until Ctrl+C. It does not take the pipeline lock, so it can run while the nightly pipeline does.
3. Keep it on: `rpipe app install`. It resolves and checks the address *now* (a refused address stops here, before anything is written), asks systemd whether its user manager is reachable, writes `~/.config/systemd/user/research-pipeline-app.service`, reloads systemd, enables the service and then restarts it (`enable --now` alone leaves an already-running service on its old address, so a reinstall with a new `--host` or `--port` would not take effect). It prints the address and a reminder of `loginctl enable-linger $USER` (so the service survives logging out; the command never runs it for you).
4. Open the address it printed, with the number, on your phone or laptop. Tailscale machine names (such as `machine.tailnet.ts.net`) are not accepted in this version: the guard allows only the exact address the app listens on. On a phone, use the browser's Add to Home Screen (iPhone: Share, then Add to Home Screen; Android: the menu, then Add to Home screen) to get the icon. The icon is a copper pipe dropping today's page onto yesterday's (r**pipe**). Its source is `webapp/static/icon.svg`; the PNG sizes phones ask for (`icon-180.png` for iPhone, `icon-192.png` and `icon-512.png` for the manifest) are rendered from it, for example with `uv run --no-project --with cairosvg cairosvg icon.svg -o icon-180.png --output-width 180 --output-height 180`. An iPhone keeps the old picture until you delete the home-screen icon and add it again.
5. `rpipe app status` says `Running at http://...`, `Installed but not running. Check: journalctl --user -u research-pipeline-app`, or `Not installed. Set it up with: rpipe app install`. `rpipe app uninstall` stops the service and deletes the file.

The unit is small: it sets `RESEARCH_VAULT`, runs `rpipe app run --host H --port P`, restarts itself 10 seconds after a crash, and starts after the network and Tailscale are up. `--host` and `--port` (default 8002) can be given to `run` and `install`; the default host is this machine's Tailscale address, and only localhost, loopback and Tailscale addresses are accepted (the `netguard` allowlist). The set of Host names the guard accepts is built from the bind address by `service.allowed_hosts`: the bind address itself, plus `localhost`, `127.0.0.1` and `::1` when you bind to a loopback address.

**Why it's built this way.**

- **A user service, as the nightly timer is.** No root, same tools, same linger rule. Rejected: a system service (needs sudo) and a second always-on process of our own (systemd already restarts, logs and starts at login).
- **The address is fixed in the unit.** It is read back by `status` from the unit file, so status works even when Tailscale is down. If your Tailscale address ever changes, run `rpipe app install` again.
- **Checks before writes.** The extra, the address and systemd are all checked first, so a failed install leaves nothing half-done.
- **Two services now.** The nightly timer (`research-pipeline.service`/`.timer`, see [scheduling.md](scheduling.md)) and this always-on one (`research-pipeline-app.service`) are independent; removing one never touches the other.

**How to run / check it.** `rpipe app run`, then open the printed address exactly as shown. `--port` must be 1 to 65535 (checked before anything is written). Tests: `uv run pytest tests/test_webapp_service.py tests/test_cli_app.py -q` (offline: fake `systemctl` runner, `uvicorn.run` replaced, no real server or unit file).

**Where the code is.** `src/research_pipeline/webapp/service.py` (`render_service`, `install`, `remove`, `address`, `is_active`, `allowed_hosts`) and the `app` commands in `src/research_pipeline/cli.py` (`app_run`, `app_install`, `app_uninstall`, `app_status`).

## What to check after installing

Once `rpipe app install` has printed the address and you have opened it on your phone:

1. The feed shows today's date and your papers; the layout fits the phone width with no sideways scrolling.
2. Switch the phone to dark mode; the pages follow it.
3. Try Skim and Read, and open a paper from a card.
4. Tap "More like this" on a paper, then run `rpipe feedback` on the computer: the vote is listed there.
5. Save a paper; it appears under Saved.
6. Use "Add to Home Screen" so it opens like an app (iPhone uses the 180 px PNG icon, Android the 192 and 512 px ones).

## How to run / check it

Tests (offline): `uv run pytest tests/test_webapp_server.py -q` covers static files and the PNG icons, a wrong `Host` (the friendly 400 page, with the address escaped), the "Your library is busy. Try again in a moment." page (503; when the database stays locked past its 5-second wait, pages show it and `/api/` answers JSON), a cross-site and a header-less POST (403), a same-origin POST reaching the router (404, JSON under `/api/`), IPv6 hosts, the `Referer` fallback and template escaping. The `rpipe app` commands are covered in "Setting it up".

## Where the code is

- `src/research_pipeline/webapp/server.py`: `create_app(vault_db, allowed_hosts, address=None, my_topics=None, share=None)` (the optional `address` is what the wrong-host page offers; `my_topics` and `share` feed the People page), the 404 page handler and the "library busy" handler. `deps.py`: `get_conn` and the shared `templates` (routers import these from `deps`; `server` re-exports them).
- `src/research_pipeline/webapp/guard.py`: `check`, the Host and same-origin checks.
- `src/research_pipeline/webapp/templates/base.html`: the page frame and navigation.
- `src/research_pipeline/webapp/static/`: `app.css` (palette, type, layout), `app.js`, `icon.svg` and its PNG copies `icon-180.png` (iPhone home screen), `icon-192.png` and `icon-512.png` (Android, listed in the manifest) rendered from the SVG, `manifest.webmanifest`, `fonts/`.
- `src/research_pipeline/webapp/present.py`: `why_sentence`, `topic_of`, `topic_color`, `section_tag`, `day_heading`, `card`/`CardData` (pure shaping).
- `src/research_pipeline/webapp/diagram_svg.py`: draws a stored diagram as inline SVG (see "Hook, key numbers and the diagram").
- `src/research_pipeline/webapp/routes_feed.py`: the `/` and `/day/{date}` routes (with the `?view=` choice, see "Mine, Following and Mix").
- `src/research_pipeline/webapp/routes_people.py`: the `/people` page and the follow and Mix-tick endpoints (see "People").
- `src/research_pipeline/webapp/routes_actions.py`: the `/api/paper/{id}/vote`, `save` and `summarize` endpoints; `templates/_actions.html` is the button row; the click handling is the second block of `static/app.js`.
- `src/research_pipeline/webapp/routes_pages.py`: `/paper/{id}`, `/saved`, `/search`; `present.paper_detail` shapes the paper page.
- `src/research_pipeline/webapp/templates/`: `feed.html`, `_card.html`, `_empty.html`, `paper.html`, `list.html` (Saved and Search), `404.html`.
- `src/research_pipeline/webapp/service.py`: the always-on service (see Setting it up).
- Tests: `tests/test_webapp_server.py` (guard, static files, error pages), `tests/test_webapp_feed.py` (feed and Skim/Read), `tests/test_webapp_pages.py` (paper, Saved, Search), `tests/test_webapp_actions.py` (vote, save, summarize), `tests/test_webapp_share.py` (Mine / Following / Mix), `tests/test_webapp_people.py` (People page, Follow, Mix ticks), `tests/test_webapp_present.py` (card shaping, safe links), `tests/test_webapp_diagram_svg.py` (the drawing), `tests/test_webapp_service.py` (the systemd unit), `tests/test_cli_app.py` (the `rpipe app` commands, port range, allowed hosts).
