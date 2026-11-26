# Phase 10 — Sharing: publish your feed, find feeds, follow people (design)

> **Phase 10** · Status: built (awaiting merge) · Plan: [phase-10-sharing.md](../plans/phase-10-sharing.md) · Roadmap: [ROADMAP.md](../ROADMAP.md)

Date: 2026-11-26. Vision and earlier decisions: [phase-08-15-app-roadmap-design.md](phase-08-15-app-roadmap-design.md). Screens: the "Research Feed — App Design" canvas (https://claude.ai/artifact/JnycnAXpyjtqyybVmv9xMK): `Main` (feed switcher, People tab), `Directory` (People screen), `Desktop` ("Showing" list, name labels on cards).

## Intent

Everyone runs their own pipeline. This phase lets people **see what other people's pipelines are finding**: you publish your feed, others follow it, and from then on they keep getting it, past and future. In the owner's words, the aim is to get people's feeds to each other "in the least intrusive way". It is a research and interest-sharing tool, **not social media**.

What it lets you do, before and after:

- **Before:** your feed exists only on your computer and your Tailscale devices. Nobody else can see it, and you can't see anyone else's.
- **After:** `rpipe share setup` publishes your feed to a public address. Anyone with rpipe can find it in the directory (the **People** tab or `rpipe share browse`) and follow it. Their nightly run downloads your new posts, and the app shows them under **Following** or mixed into their own feed (**Mix**). A new follower also gets everything you published before.

## Goals

- **Follow a person's whole feed**, not a topic or a tag (owner). Once you follow someone you see every post they publish from then on, plus their whole past (owner: "whole past makes more sense").
- **Publish** with one guided command. After that it runs every night with no further action.
- **A directory** of everyone who publishes, browsable by name and topics, with **no gatekeeper and no approval** (owner).
- **Identity is a display name only** (owner). The unique id is the GitHub account and repository, which rpipe handles. No email, bio, photo or sign-up anywhere else.
- **The app stays local** (owner's concern). Followed posts are downloaded into your own `vault.sqlite`. The app reads only that file and makes no internet requests while you browse.
- Every setting has an `rpipe` command (and, where the mockups show it, an app control). Nobody edits files.

## Non-goals

- Following by topic or tag. That could be a later idea, not this phase (owner).
- Narrated videos and where to store them (Phase 12). GitHub Pages' 1 GB limit fits text for decades, not video.
- A public web page version of the feed for people without rpipe. A standard Atom feed file is published, so any feed reader works, but there's no designed HTML site.
- Changes to summaries or to the look of existing screens (owner ruling 2026-11-26). The **Mine** view is exactly today's feed. The only new screen parts are the sharing parts already in the Phase 8 mockups.
- Hosts other than GitHub for publishing. The feed format and fetch code take any `https` address, so another host can be added later.

## Anti-goals (a failure even if it works)

- **Anything social:** comments, likes, replies, messages, follower counts, "who follows you". Publishers can't see who follows them, and nothing in the files would tell them.
- **A central server or database** that someone has to run, pay for or moderate.
- **Your computer becoming reachable from the internet.** Publishing pushes files out. Nothing listens for incoming connections.
- **Leaking private data.** Votes, saves, skips, notes, the shelf and your own work never leave your computer, and nor do ranking reasons that point at them ("resembles a paper you liked", "close to *name* on your shelf").
- **A junk or hostile feed breaking someone's app.** Everything downloaded is untrusted: it's checked strictly, limited in size, and shown escaped.

## Constraints

- **Architecture groups** (`docs/guide/architecture.md`, `tests/test_architecture.py`). Network work (pushing, fetching, searching GitHub) is pipeline work, run by `rpipe` commands and the nightly run. The app group (`webapp`, `library_api`) does no network calls. It reads and writes the database only through `library_api`.
- Pipeline jobs take the run lock. App actions are short single transactions and never take the lock.
- Tests run offline: fake `git`/`gh` runner, `respx` for HTTP, fixture vaults.
- Every waiting step reports through `progress.py`: push, fetch, directory refresh, and waiting for GitHub Pages to go live.
- Schema change: `SCHEMA_VERSION` 4 → 5, new tables only (no change to existing ones).
- Docs: new `docs/guide/sharing.md`, plus updates to `app.md`, `architecture.md`, `data-model.md`, `running.md`, `glossary.md` and `README.md`, in the same commits as the code.

## Approaches (chosen in conversation)

How feeds travel between people:

| | Approach | Trade-off | Verdict |
|---|---|---|---|
| **1** | **Feed files on free static hosting (GitHub Pages).** Each night your rpipe pushes your feed files to your own public repository. Followers' rpipe downloads them during their nightly run. | Free, no server, nothing exposed on your machine, works with your computer off. Needs a free GitHub account to *publish* (not to follow). The host sees readers' addresses; publishers don't. | **Chosen** (Claude's pick; owner: "we can run this through GitHub") |
| 2 | Shared sync folder (Syncthing, Drive, Dropbox) | Simple inside a club, but not public. Everyone gets every file. Syncthing needs both machines on. | Fallback if a group wants to stay private. Not built. |
| 3 | One shared cloud storage bucket | Smooth, but someone owns it, pays for it and hands out keys: a gatekeeper. | Rejected |
| 4 | Tailscale Funnel / node sharing | Little to build, but your home computer becomes the server and has to stay on. The most intrusive. | Rejected |
| 5 | Open networks (Nostr, IPFS, AT Protocol) | No company in the middle, but immature, relays drop old posts, and it's close to social-media infrastructure. | Rejected |
| 6 | Send a file by hand | Works today, but it isn't following: nothing updates. | Rejected |

Why there's no central database (the owner's concern): live following only needs a **stable address per feed**, the way podcasts work. Each feed's address stays the same while the files behind it are updated every night, and followers check it every night. The only shared thing is the directory, and that's GitHub's own search.

How the directory works:

| | Approach | Verdict |
|---|---|---|
| A | A shared list in one repository. Joining is a pull request that someone approves. | Rejected: needs a gatekeeper (owner: "definitely no gatekeeper") |
| **B** | **A GitHub tag.** Each feed repository carries the topic `rpipe-feed`. Browsing searches GitHub for it, and rpipe lists only repositories that serve a valid rpipe feed. | **Chosen** |

## How it works

```
 PUBLISHER (nightly, after the digest)                    PUBLIC, STATIC
 rpipe run ─► share publish ──git push──►  github.com/alex/rpipe-feed  (tag: rpipe-feed)
                                           served as  https://alex.github.io/rpipe-feed/
                                                feed.json · feed.xml · archive/2026-11.json …
                                                                ▲            ▲
 FOLLOWER (nightly, and on demand)                              │            │
 rpipe run ─► share fetch ── download each followed feed ───────┘            │
           └► share directory refresh ── GitHub search "topic:rpipe-feed" ───┘
                 │
                 ▼
          follower's vault.sqlite  (follows, followed_posts, directory_entries)
                 │  library_api (no network)
                 ▼
          rpipe app: Mine | Following | Mix, People tab
```

### Worked example

1. **Alex publishes.** Alex runs `rpipe share setup`. It checks that `git` and the GitHub CLI (`gh`) are installed (and says how to install them if not). It signs Alex in to GitHub in the browser (`gh auth login`) if needed, then asks for a display name ("Alex M.", defaulting to the GitHub profile name). It creates the public repository `alex/rpipe-feed`, tags it `rpipe-feed`, turns on GitHub Pages and pushes the first files: every post from Alex's whole digest history. It waits, with progress, until `https://alex.github.io/rpipe-feed/feed.json` answers, then prints the address. Before anything is created it says plainly: *"This makes your feed public. Anyone can read it. Copies people have already downloaded can't be pulled back if you stop."* Alex has to confirm.
2. **Every night after that,** `rpipe run` writes the digest and then publishes: it rebuilds the feed files from the database and pushes only if something changed. A summary added later by a request updates that post in place.
3. **Sam finds Alex.** Sam opens **People** in the app (or runs `rpipe share browse`). The list comes from the last directory refresh: "Alex M. · @alex · robotics, reinforcement learning · about 8 papers a day · shares 1 of your topics". Sam taps **Follow** (or runs `rpipe share follow alex`).
4. **Sam gets Alex's feed.** The follow is recorded at once. Posts arrive at Sam's next nightly run, or right away with `rpipe share fetch`. The CLI `follow` command fetches straight away. The first fetch downloads the current file and every monthly archive (the whole past). Each later fetch downloads the current file and, only when it has changed, that month's archive.
5. **Sam reads.** The feed opens on **Mine** (unchanged). **Following** shows the posts of everyone Sam follows, newest day first. **Mix** shows Sam's own papers plus the feeds ticked in the "Showing" list, each card labelled with whose feed it came from ("Alex M. · Robotics").
6. **Alex stops.** `rpipe share stop` stops the nightly push and removes the `rpipe-feed` tag, so Alex leaves the directory. It asks whether to also delete the repository: deleting stops followers getting anything new, keeping it leaves the old address working. Sam keeps every post already downloaded either way. Sam's app shows "Alex M. stopped publishing on 2026-12-03" after a few failed nights, or once the feed reports that it has stopped.

### What is published

Files in the repository root (all built from the database every time, so they are always complete and consistent):

- **`feed.json`**: a [JSON Feed 1.1](https://jsonfeed.org/version/1.1) document with the last 30 days of posts. A top-level `_rpipe` object holds `format: 1`, `display_name`, `topics` (from your profile's topic names), `generated_at`, `active: true|false` (false after `share stop` without delete), and `archives`, the list of monthly archive files (`archive/2026-11.json`, …), each with its post count and a content hash so followers know which ones changed.
- **`archive/YYYY-MM.json`**: the same item format, holding every post whose digest date falls in that month. Together they make up the whole past.
- **`feed.xml`**: an Atom feed of the last 30 days (title, link to the paper, one-line summary, date), so anyone can follow in an ordinary feed reader.
- **`README.md`**: one paragraph saying what this repository is, with a link to rpipe.

**A post** is one paper that appeared in your digest on a given day, summarized or not, the same set the Mine view shows. Each JSON Feed item has the standard fields (`id` = paper id, `url` = paper link, `title`, `summary` = one-line summary or the start of the abstract, `date_published` = digest date). Its `_rpipe` object has:

- `paper`: id, title, abstract, authors, url, published date.
- `rank`: the paper's position in that day's digest.
- `summary`: the stored `PaperSummary` (summary, critique, basis, checks) exactly as stored, or null. The publisher's model name and date go along as provenance, which the "About this summary" corner already shows.
- `reasons`: the plain ranking reasons, **only** the kinds `topic`, `upvotes`, `author` and `keyword`. Never `liked`, `disliked` or `shelf`, because those would reveal your votes and the names of your own files.

Never published: votes, saves, skips, the shelf, `my_work/`, notes, run logs, config, the profile beyond topic names, and your diagrams (diagrams are off; the field is left out).

### What is stored on the follower's side (schema v5, new tables)

- **`follows`**: one row per feed you follow. Feed id (`github:alex/rpipe-feed`, lower case), feed address, GitHub handle, display name and topics (refreshed from each fetch), `in_mix` (shown in Mix, default on), followed at, last fetched at, last error, consecutive failure count, `active` as last reported, and the archive hashes already downloaded.
- **`followed_posts`**: one row per (feed id, paper id). Digest date, rank, title, abstract, authors, url, published, summary JSON, reasons JSON, provenance, fetched at. A post that changes upstream (a summary added later) is replaced.
- **`directory_entries`**: the last directory refresh. Feed id, address, handle, display name, topics, posts per day (from the feed's last 30 days), up to 3 recent titles with one-liners for the preview, checked at.

`papers` and the other existing tables are not changed. Followed posts are **not** your papers: they never enter triage, the digest or your published feed. (Republishing what others found would turn feeds into a chain of reposts.)

### Fetching safely

- Only `https` addresses. A GitHub handle becomes `https://<handle>.github.io/rpipe-feed/feed.json`. `rpipe share follow` also accepts a full `https://…/feed.json` address for feeds hosted elsewhere later.
- Limits: 5 MB for `feed.json` and 20 MB per archive file, 20 s timeout, redirects only to `https`, at most 500 archive files per feed.
- Every file is parsed into strict Pydantic models with length caps (title 500 characters, abstract 10 000, one claim 2 000; at most 300 items in `feed.json` and 2 000 per archive file). Unknown fields are ignored. Items that fail are skipped and counted, not stored. A file that fails entirely leaves the stored posts as they are and records the error.
- Links shown in the app go through the existing `present.safe_url` (http/https only). All text is escaped by the templates as today.
- A feed's `_rpipe.format` higher than this rpipe understands means "update rpipe" for that feed, not a crash.

### The directory refresh

`share directory refresh` (in the nightly run, and when you run `rpipe share browse`) searches GitHub for repositories tagged `rpipe-feed` through the public search API (no sign-in needed, well within the unsigned rate limit for one search per night). For each hit (at most 100) it downloads `feed.json` with the same checks as fetching. Hits without a valid feed are left out. Entries are stored in `directory_entries`, replacing the previous refresh. The app's People screen reads only that table, so it opens instantly and offline.

"Shares N of your topics" compares the entry's topics with your profile's topic names, ignoring case and spaces. The list is sorted by that number, then by name, so "Feeds like yours" comes first, as in the mockup.

## Commands

All in a new `rpipe share` group. Guided prompts validate what you type, and nothing requires editing a file.

| Command | What it does |
|---|---|
| `rpipe share setup` | Guided first-time publishing (see the worked example). Running it again repairs things: re-tags, re-enables Pages, re-pushes. |
| `rpipe share status` | Publishing on or off, your address, last push and its result. Then each followed feed with its last fetch, number of posts, whether it's in Mix, and any error. |
| `rpipe share publish` | Rebuild and push now (the nightly run does this on its own). |
| `rpipe share set-name NAME` | Change your display name (republishes). |
| `rpipe share stop [--delete]` | Stop publishing and leave the directory. Asks before deleting the repository. |
| `rpipe share browse [SEARCH]` | Refresh the directory and list it, optionally filtered by name or topic. |
| `rpipe share follow HANDLE-or-URL` | Follow a whole feed and fetch it now (current file and all archives). |
| `rpipe share unfollow HANDLE` | Stop following. That feed's stored posts are removed, except ones you saved (see below). |
| `rpipe share following` | List who you follow. |
| `rpipe share fetch` | Fetch every followed feed now (the nightly run does this on its own). |
| `rpipe share mix add/remove HANDLE` | Include or leave out a followed feed in Mix (the same setting as the app's "Showing" checkboxes). |

Publishing settings (`share.enabled`, `share.display_name`, `share.repo`) go in `config.yaml`, written by these commands.

## The nightly run

After the digest is written, two new stages run, each in its own `try` so a network problem never affects the digest:

1. **publish** (only when publishing is on): build the files, commit and push if anything changed. A failed push (offline, expired sign-in) is noted in the run's errors and on the health line ("sharing: push failed, will retry tomorrow: …"). Nothing is lost, because the next night's push holds everything.
2. **fetch and directory refresh**: fetch every followed feed, then refresh the directory. Counts go on the health line ("followed: 3 feeds, 24 new posts").

Both report progress. Followed feeds are fetched one after another (`fetching 2 of 3: Alex M.`).

Pushing at 3 a.m. uses whatever credentials `git` already has for GitHub: `gh` sets them up with `gh auth setup-git` during `share setup`. `setup` finishes by running a test push without any prompt, so a setup that would fail at night fails now, with the reason and the fix.

## The app (from the Phase 8 mockups; existing screens otherwise unchanged)

- **Feed header:** the **Mine / Following / Mix** switch next to Skim/Read, as in `Main`. Mine is the default and exactly today's feed. The choice is remembered per device, like Skim/Read. The switch is hidden while you follow nobody, so nothing changes until you use sharing.
- **Following:** followed posts, newest day first. Within a day, by feed (in the order you followed them), then by the publisher's rank. Same cards as today, plus one line above the title: "Alex M. · Robotics" (display name, then the topic from that post's reasons), as in `Desktop`.
- **Mix:** your own papers plus the posts of feeds with `in_mix` on, newest day first. Within a day your own papers come first in score order, then followed posts. A paper that appears in several feeds is one card, labelled "You · Alex M." or "Alex M. · Dana". On desktop, the "Showing" checkboxes in the sidebar set `in_mix` (one `library_api` write per tick). On the phone, the People screen has the same ticks.
- **Followed post cards:** same fields, Skim/Read and paper screen as your own. "Why you're seeing this" becomes "Alex M.'s pipeline picked it because …", using the published reasons. **Save** and **More / Less like this** work: the first time you use one, the paper's metadata is copied into your `papers` table (source `feed`, status `skipped`, so triage leaves it alone and it never shows in Mine), and the vote or save is recorded as usual. Your vote then counts in your own ranking like any other vote. **Summarize this** is not offered on followed posts: the summary shown is the publisher's.
- **People** (bottom tab on the phone, sidebar link on desktop, as in `Directory`): the "Your feed" box (private, or published with your address; it shows the `rpipe share setup` command to run when you haven't set up, and Stop/Resume once you have), "Feeds like yours" from `directory_entries` with **Follow / Following** buttons, and a preview of the selected feed's recent posts. Tapping **Follow** records the follow at once. The posts arrive at the next nightly run, and the button reads "Following · posts arrive tonight" until then.
- **The app makes no network calls.** Every list is read from the database.

## Changes to existing code (by area)

- **`db.py`:** schema v5 with the three tables, plus their read/write helpers.
- **New shared module for the file format** (working name `feed_format`): the Pydantic models for feed and archive files, plus building and parsing. Used by publish, fetch and the directory, and by tests.
- **New pipeline modules** (working names `share_publish`, `share_fetch`, `share_directory`): building and pushing files (through a small runner wrapping `git`/`gh`, which tests can fake), fetching followed feeds, refreshing the directory.
- **`library_api`:** reads for Following, Mix and the directory, and writes for follow (record only), unfollow, `in_mix`, and vote/save on a followed post (copying the paper in). Still no network, no `llm`/`backends`.
- **`webapp`:** the switch, the labelled cards, the "Showing" list and the People page, using the existing templates, styles and card macro.
- **`cli.py`:** the `share` group and the two new nightly stages.
- **Triage:** must give a copied-in followed paper an embedding when you vote on it, so the vote counts for "resembles a paper you liked". It must not otherwise touch `source = feed` rows.

## Decided by Claude (owner may override)

- Publishing through `git` + `gh` (GitHub's own command-line tool) rather than rpipe holding its own GitHub token. `gh` handles the browser sign-in and keeps the credentials.
- Repository name fixed as `rpipe-feed` (one feed per GitHub account), so a handle is enough to follow.
- The whole digest history is published at setup, not only posts from that day on. This matches "whole past" for followers.
- Published reasons limited to topic, upvotes, author and keyword (privacy).
- Followed posts never enter your own feed, digest or published feed.
- Unfollow removes that feed's posts. Ones you saved stay, because saving copied the paper into your library.
- Following from the app records the follow, and the posts arrive overnight. The app does no network work.
- A feed that has failed 7 nights in a row, or reports `active: false`, is shown as "stopped publishing". It stays followed, and its posts stay.
- The Mine/Following/Mix switch is hidden until you follow someone.
- Atom file included for ordinary feed readers. No HTML site.

## Settled after the builder check (Claude's calls)

- **A followed paper you already have.** Voting on or saving a followed post whose paper id is already in your `papers` table copies nothing. The existing row and its status stay, and the vote or save applies to it.
- **Your own pipeline later finds a copied-in paper.** `upsert_paper` gets one special case: a row with `source = 'feed'` is taken over by the collector. Its source becomes the collector's and its status becomes `new`, so triage treats it like any newly collected paper and it can reach Mine. Your vote and save stay. Apart from this, and the embedding for votes, triage ignores `source = 'feed'` rows.
- **The summary of a copied-in paper.** Nothing is written to your `summaries` table (that would make your pipeline think it had summarized the paper, and would misreport provenance). The paper screen, Saved and the cards read the publisher's summary and reasons from `followed_posts`. **Unfollow** therefore keeps the `follows` row (it marks `unfollowed_at` and drops the feed from Following and Mix) and keeps the `followed_posts` rows of papers you saved or voted on. It deletes the rest. Votes and saves are never deleted.
- **Which posts are published:** every paper with a `digest_date`, whatever its status, the same set `library_api.digest_papers` returns for each day. A paper has one `digest_date` in a vault, so it is one post. `rank` is derived the way the feed sorts (`triage_score DESC, id ASC` within the day).
- **The privacy filter fails closed.** Published reasons go through `library_api.plain_reasons` (which already parses the stored strings into kinds) and keep only `topic`, `upvotes`, `author` and `keyword`. Anything it doesn't recognise is dropped. A test checks that `liked similar: …`, `disliked similar: …` and `near your shelf: …` never appear in any published file.
- **Card label without a topic:** just the display name ("Alex M."). In Mix, a paper from several sources is labelled with every source (you first, then feeds in follow order), with the topic taken from the first source that has one. It sorts under its earliest date among those sources.
- **The same paper on a new date in one feed** (the publisher's paper got a new version and a new digest date): the fetch replaces the post, so the latest date wins.
- **Module groups:** `feed_format` is **shared**: pure Pydantic models plus building and parsing, imports only `summary_models` and the standard library. `share_publish`, `share_fetch` and `share_directory` are **pipeline**. `library_api` (app) reads and writes the new tables through `db` helpers. `cli.py` wires them together. All four are added to `tests/test_architecture.py` and the table in `architecture.md`.
- **Locking:** the nightly stages run inside `rpipe run`'s lock. `rpipe share setup`, `publish`, `fetch`, `follow`, `browse` and `stop` take the lock (they write the vault's share clone or fetched rows in bulk). `status`, `following`, `set-name` (writes config, then publishes under the lock), `unfollow` and `mix` are quick and don't take it.
- **GitHub edge cases.** If `gh` lacks a needed scope, `setup` runs `gh auth refresh` for it, explaining why first (`delete_repo` is asked for only by `stop --delete`). If `alex/rpipe-feed` already exists and isn't an rpipe feed (no `feed.json` carrying `_rpipe`), `setup` stops and says so, and doesn't touch it. Only personal accounts are supported (not organisations). A custom Pages domain works because the fetch follows `https` redirects.
- **File sizes:** `feed.json` is capped at 300 items and 5 MB. Archive files at 2 000 items and 20 MB. A month that would go over either limit is split into `archive/YYYY-MM-1.json`, `-2`, … (all listed in `archives`). Followers skip nothing because of size, since the publisher never writes past the caps. At about 15 papers a day with full summaries, a month is roughly 2–3 MB.
- **Mine/Following/Mix remembered per device** in `localStorage`, like Skim/Read (`static/app.js`). The server reads it from a query parameter (`?view=following`), and the switch links set it.

## Left to the builder

Module, table-column and route names. How `git` is driven (a working clone under `<vault>/share/`). The exact GitHub API calls for Pages and topics. Progress wording. Test layout. How the People page shows the selected feed's preview on the phone (expanding the row in place is fine). How to delete feeds' posts in bulk efficiently.
