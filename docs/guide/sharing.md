# Sharing your feed with other rpipe users (Phase 10)

## What it does

Lets you publish the papers rpipe picked for you, with their short summaries, so other rpipe users can follow your feed, and lets you follow theirs. Publishing is opt-in: nothing leaves your machine unless sharing is turned on. This page is filled in step by step; this first part describes the files that get published. The commands are described under "How to run / check it"; the browsing screens are added as they are built.

## What is published

Three kinds of file, written by `rpipe` and never edited by hand:

- `feed.json`: the last 30 days of posts (newest first, at most 300, at most 5 MB), in the JSON Feed format, plus an `_rpipe` section that says which archives exist.
- `archive/YYYY-MM.json`: everything older, one file per month (at most 2,000 posts and 20 MB each; a bigger month is split into `YYYY-MM-1.json`, `-2.json`, and so on). `feed.json` lists every archive with its post count and a SHA-256 fingerprint of its bytes, so a follower can tell which archives changed.
- `feed.xml`: the same recent posts as Atom, so an ordinary feed reader can follow it (title, link and a one-line summary only).

Plus a `README.md` saying what the repository is.

A trimmed example `feed.json`:

```json
{
  "_rpipe": {
    "active": true,
    "archives": [{"count": 41, "path": "archive/2026-10.json", "sha256": "9f2c…"}],
    "display_name": "Alex M.",
    "format": 1,
    "generated_at": "2026-11-26T03:00:00Z",
    "topics": ["agents", "security"]
  },
  "feed_url": "https://alex.github.io/rpipe-feed/feed.json",
  "items": [
    {
      "_rpipe": {
        "paper": {"abstract": "…", "authors": ["A. Author"], "id": "2611.01234",
                  "published": "2026-11-24", "title": "Agents that test themselves",
                  "url": "https://arxiv.org/abs/2611.01234"},
        "provenance": null,
        "rank": 1,
        "reasons": ["topic: agents (0.80)"],
        "summary": {"…": "the full stored summary, with cited sections"}
      },
      "date_published": "2026-11-25T00:00:00Z",
      "id": "2611.01234",
      "summary": "One plain line saying what the paper found.",
      "title": "Agents that test themselves",
      "url": "https://arxiv.org/abs/2611.01234"
    }
  ],
  "title": "Alex M.'s research feed",
  "version": "https://jsonfeed.org/version/1.1"
}
```

What is never published: your votes, saves, skips, shelf, notes in `my_work/`, run history, config and diagrams. Of the reasons a paper was picked, only these kinds go out: topic, HF upvotes, followed author and keyword. "Liked similar", "disliked similar" and "near your shelf" are dropped, because they would reveal your votes or notes.

Files from other people are never trusted. Reading a feed refuses one that is too big, not JSON, missing `_rpipe`, or marked with a newer `format` (or a `format` that isn't a whole number) than this rpipe knows ("This feed needs a newer rpipe."). Text fields are length-capped (for example display name 60 characters, 30 topics). A single bad post (for example a 600-character title) is dropped and counted; the rest of the feed still loads.

## How it works

### The directory

The directory is a list of published feeds you can browse and follow without knowing anyone's handle. Code: `src/research_pipeline/share_directory.py` (`refresh`, stage `directory`).

**The tag, and no gatekeeper.** When someone turns sharing on, their `rpipe-feed` repository gets the GitHub topic `rpipe-feed` (a topic is a free-text tag on a repository). There is no registry and nobody approves entries: GitHub's own search is the index. `refresh` asks GitHub for `topic:rpipe-feed` repositories that are not forks (up to 100 hits per search).

**How junk is filtered.** Anyone can tag any repository, so a hit proves nothing. For each hit named exactly `rpipe-feed`, rpipe works out the feed address from the owner's login (`https://<owner>.github.io/rpipe-feed/feed.json`), downloads it with the same safe download as following (https only, 5 MB cap, time limit) and runs it through the normal feed validation. A hit that is a 404, is not JSON, is too big or fails validation is simply left out. Nothing the owner wrote in the search result itself is trusted.

**What is stored** (table `directory_entries`, one row per valid feed): handle, display name, topics, `posts_per_day` (items in the last 30 days divided by 30, one decimal; a feed with 15 recent papers shows 0.5) and a `preview` of up to the 3 newest items (title plus the one-line summary, or the start of the abstract when there is none). The feed id is `github:<owner in lowercase>/rpipe-feed`, the same id that following uses.

**Failure rules.** The table is replaced in one go, and only when the search worked. If GitHub is unreachable or rate-limits the search, the old list stays and the error is reported; one broken hit never affects the others. Progress shows `checking 2 of 5: alex` for each feed.

**"Feeds like yours."** `feed_format.shared_topics(theirs, mine)` counts the topics two people have in common, ignoring case and spacing (`"Agents "` matches `"agents"`). The app uses it to rank directory entries by how many topics they share with your profile.

### Following

`rpipe share follow <handle or https address>` adds a feed to the ones you follow, and the nightly run (`fetch_all`, stage `fetch`) keeps them current. Code: `src/research_pipeline/share_fetch.py`.

**What a person types.** `alex` or `@alex` becomes the address `https://alex.github.io/rpipe-feed/feed.json` and the feed id `github:alex/rpipe-feed` (the id never changes, so following twice finds the same row). A full `https://.../feed.json` address also works, for feeds hosted elsewhere; its id is `url:<address>`. Anything else (for example `http://`) is refused with a plain message.

**First fetch takes the whole past.**

1. `follow` refuses your own handle ("That's your own feed.").
2. It downloads `feed.json` first. If that fails (no such feed, not an rpipe feed, too big), it raises "No rpipe feed at <address>: <reason>" and stores nothing, not even a follow row, so a typo leaves no trace.
3. It records the follow and runs `fetch_one`: every archive listed in `feed.json` is downloaded (there is nothing to compare against yet), then the posts of the archives and of `feed.json` are stored in one go and committed once. The feed's display name, topics and `active` flag are refreshed.

**Later fetches.** `feed.json` lists each archive's SHA-256 fingerprint. rpipe keeps the fingerprints it has already downloaded (`follows.archive_hashes`) and fetches only archives whose fingerprint differs. A normal night is therefore one small request for `feed.json` plus perhaps the current month's archive. A post that appears in both `feed.json` and an archive is the same post (same paper id) and is stored once; `feed.json`'s copy wins.

Worked example: Alex has 41 posts in `archive/2026-10.json`, 12 in `archive/2026-11.json` and 8 in `feed.json`, with some overlap. First fetch: 3 downloads, then every distinct post (at most 61) stored. Next night: Alex adds a post; only `feed.json` and `archive/2026-11.json` changed, so `archive/2026-10.json` is not downloaded again.

**Limits.** Every download is https only (a redirect to `http://` fails the fetch), stops reading once it passes its cap (5 MB for `feed.json`, 20 MB per archive), and has a 20 second timeout. At most 500 archives per feed, and an archive path must look like `archive/YYYY-MM.json` (optionally `-N`), so a feed cannot point rpipe at an arbitrary address. Invalid items (for example a 600-character title) are skipped and counted; the rest are stored.

**When a fetch fails.** Nothing new is stored, the reason is kept (`follows.last_error`) and `failures` goes up by one. One feed failing never stops the others: `fetch_all` returns an error line like "fetch Alex M.: the server answered 500" and moves on. A success resets `failures` to 0.

**What "stopped publishing" means.** A feed counts as stopped (`is_stopped`) after 7 failed fetches in a row (`STOPPED_AFTER`), or when its own `_rpipe.active` is false (its owner turned sharing off). Its old posts stay, rpipe keeps trying each night, and a good download makes it active again.

**Progress.** The `fetch` stage reports one step per feed ("fetching 2 of 3: Alex M."), and one per archive on a first fetch.


### Publishing

Publishing turns your database into the three kinds of file above and pushes them to a GitHub repository of yours named `rpipe-feed`, which GitHub Pages then serves at `https://<your handle, lowercase>.github.io/rpipe-feed/feed.json`.

**One-time setup** (the `rpipe share` commands run these steps for you, with prompts; you never type `git` or `gh` yourself):

1. Check that `git` and the GitHub command line tool `gh` are installed; if not, say which and how to install it.
2. Sign in with `gh auth login` if needed, then read your GitHub handle.
3. Look at `<handle>/rpipe-feed`. Not there: create it (public, with a first commit so the `main` branch exists) and clone it into rpipe's data folder. Already an rpipe feed: reuse it. Something else under that name: stop and ask, never overwrite someone's other repository.
4. Turn on GitHub Pages for branch `main`, folder `/`. If it is already on, that is fine.
5. Run `gh auth setup-git` so a plain `git push` uses your GitHub sign-in. Without this the nightly run would wait for a password nobody is there to type.
6. Optionally add the repository topic `rpipe-feed`, which is how the public directory finds feeds. Turning sharing off removes the topic.

**Each night** (the `publish` stage, after the digest is written):

1. `collect_posts` reads every paper that has appeared in a digest, ranked within its day by triage score (ties by paper id), with its summary and provenance. Reasons pass through `public_reasons`, and diagrams are never read.
2. `feed_format.build_files` builds the files. Papers older than 30 days move into month archives.
3. `write_site` writes them into the clone and deletes any `archive/*.json` that is no longer built. It reports whether anything changed.
4. `push` runs `git add -A`; if something is staged it commits ("rpipe feed 2026-11-26") and runs `git push`. If nothing changed, nothing is pushed: because the files are byte-stable, a quiet night makes no commit at all.
5. The first time only, `wait_live` polls the feed address every 10 seconds (up to 5 minutes) until GitHub Pages serves a valid rpipe feed, so setup can say "your feed is live" and not just "pushed".

Worked example: your digest on 2026-11-26 lists 3 papers. `collect_posts` returns 3 posts ranked 1 to 3 (plus all earlier ones). Two of them were picked for "liked similar: 2611.01234"; that reason is dropped, the topic reason stays. The files differ from last night's, so one commit and one push happen, and the progress line reads "Pushing to GitHub".

**Credentials.** rpipe stores no password or token. Pushes use the sign-in that `gh` keeps (set up in step 5). If a push fails (signed out, no network, repository deleted) the stage fails with git's own message, for example "Pushing the feed failed: remote: Permission denied", and the rest of the night's run is unaffected.

**Stopping.** Turning sharing off removes the directory topic. If a push fails, the commit stays local and the next night pushes it. Deleting the feed deletes the `rpipe-feed` repository (`gh` first asks GitHub for the extra `delete_repo` permission); followers then see your feed disappear.

Every GitHub operation goes through a small `Runner` (it runs one command and returns its output). The real one runs `git` and `gh` with `GIT_TERMINAL_PROMPT=0` so nothing ever waits on a hidden prompt; tests pass a `FakeRunner` that records the commands and plays back scripted replies, so no test touches GitHub.
## Why it's built this way

- JSON Feed plus Atom: one open, boring format that other tools can read, with the rpipe extras tucked under `_rpipe` so readers that don't know them ignore them.
- Files are byte-stable (sorted keys, fixed separators, and `generated_at` taken from the newest post rather than the clock): a night with no new posts produces identical bytes, so git has nothing to commit. The one exception is a post aging out of the 30-day window, which changes `feed.json` on purpose.
- Month archives with fingerprints instead of one growing file: `feed.json` stays small and a follower downloads only archives that changed.
- Rejected: publishing everything and filtering on the reader's side. Private reasons must never leave your machine in the first place.
- Fetch the whole feed before storing anything, then store and commit once: a feed that breaks halfway leaves your database as it was, never half a feed. Rejected: storing as each archive arrives.
- Redirects are followed by rpipe itself, one hop at a time, so every hop can be checked as https. Rejected: letting the HTTP library follow them (it would accept a redirect to plain `http`).
- The fingerprint in `feed.json` is only a change marker (is this archive new to me?), not a security check; every download is validated by its own parse.
- Archive addresses are built from the feed's folder and only for paths that match the published pattern, so a hostile feed cannot make rpipe fetch some other address. Private reasons must never leave your machine in the first place.

## How to run / check it

You need `git` and the GitHub command line tool `gh` only to *publish*; following needs neither. Every setting is a command; you never edit a file.

| Command | What it does |
| --- | --- |
| `rpipe share setup` | Guided first-time publishing; run it again to repair things |
| `rpipe share status` | Publishing on/off, your address, last push and its result, then each followed feed |
| `rpipe share publish` | Rebuild and push now (the nightly run does this on its own) |
| `rpipe share set-name NAME` | Change your display name (republishes) |
| `rpipe share stop [--delete]` | Stop publishing and leave the directory; `--delete` also deletes the repository (asks first) |
| `rpipe share browse [SEARCH]` | Refresh the directory and list it, optionally filtered |
| `rpipe share follow HANDLE` | Follow a feed (or an `https://` feed address) and fetch it now |
| `rpipe share unfollow HANDLE` | Stop following; posts you saved or voted on are kept |
| `rpipe share following` | List who you follow |
| `rpipe share fetch` | Fetch every followed feed now |
| `rpipe share mix add HANDLE` / `remove HANDLE` | Include or leave out a followed feed in Mix |

Example session (Alex publishes, Sam follows):

```
$ rpipe share setup
Display name shown to people who follow you [Alex Morgan]: Alex M.
This makes your feed public. Anyone can read it. Copies people have already downloaded can't be pulled back if you stop.
Publish your feed? [y/N]: y
  publish ... pages ... (progress)
Your feed is live: https://alex.github.io/rpipe-feed/feed.json

$ rpipe share browse robotics            # on Sam's machine
Alex M. · @alex · robotics, reinforcement learning · about 8 papers a day · shares 1 of your topics
Follow one with `rpipe share follow HANDLE`.
$ rpipe share follow alex
Following alex: 212 posts downloaded.
$ rpipe share mix remove alex            # keep their feed out of Mix
$ rpipe share stop                       # Alex, later
```

What `setup` does, in order: checks `git` and `gh` (and says how to install a missing one), signs you in with `gh auth login` if needed (it lets go of the vault lock while you do), looks at `<you>/rpipe-feed` (if something that is not an rpipe feed lives there it stops and tells you to rename or delete it), asks for the display name, shows the public warning and asks you to confirm, creates the repository (or, if it exists but this computer has no copy, clones it), runs `gh auth setup-git`, publishes, adds the directory topic, turns on Pages, waits until the address answers, runs a test push with no prompts allowed (`git push --dry-run`), and only then records `share_enabled` in `config.yaml`. A failure at any step leaves sharing off, and running `setup` again carries on. `stop` publishes once more with `active: false` (so followers see "stopped publishing"), removes the topic and turns sharing off; with `--delete` it asks, deletes the repository and removes the local copy under `<vault>/share/rpipe-feed/`.

**Progress.** Every waiting step (publishing, waiting for Pages, fetching, refreshing the directory) shows a progress line; see "Progress" in [running.md](running.md). The nightly run's two sharing stages are described under "The sharing stages" in running.md.

**Decisions.** `follow`, `browse`, `publish`, `fetch`, `setup` and `stop` take the pipeline lock (they write in bulk and use the network); `status`, `following`, `unfollow`, `mix` do not, because they are quick. `following`, `unfollow` and `mix` go through `library_api` like the app does, and map the handle you type (any case, with or without `@`) to the feed. The HTTP client sends `User-Agent: research-pipeline/0.1` because GitHub's API refuses requests without one, and it never follows redirects by itself (`get_limited` does, https only). Rejected: asking the user to run `git` or `gh` themselves, and editing `config.yaml` by hand.

Tests: `uv run pytest tests/test_cli_share.py tests/test_runs.py tests/test_feed_format.py tests/test_share_publish.py tests/test_share_github.py tests/test_share_fetch.py -q`.

## Where the code is

- `src/research_pipeline/cli.py`: the `share` command group (`share_setup`, `share_status`, `share_publish_now`, `share_set_name`, `share_stop`, `share_browse`, `share_follow`, `share_unfollow`, `share_following`, `share_fetch_now`, `share_mix_add`, `share_mix_remove`), the factories `_share_runner` and `_share_http` that tests replace, `_publish_feed`, and `_share_stages` (the nightly stages).
- `tests/test_cli_share.py`: the commands.
- `src/research_pipeline/feed_format.py`: the file shapes, `build_files`, `parse_feed`, `parse_archive`, `parse_reason`, `public_reasons` and `shared_topics`. It is in the shared group (see [architecture.md](architecture.md)), so both the pipeline and the app can use it. It touches no database and no network.
- `src/research_pipeline/share_publish.py`: `collect_posts`, `write_site`, `push`, `publish`, `wait_live`, `feed_url`. Pipeline group.
- `src/research_pipeline/share_github.py`: the `Runner` protocol, `SubprocessRunner`, and the setup operations (`check_tools`, `login`, `repo_status`, `create_repo`, `set_topic`, `enable_pages`, `setup_git_credentials`, `delete_repo`). Pipeline group.
- `src/research_pipeline/share_fetch.py`: `resolve`, `get_limited`, `fetch_one`, `follow`, `fetch_all`, `is_stopped`, `STOPPED_AFTER` (re-exported from `feed_format`). Pipeline group.
- `tests/test_share_publish.py`, `tests/test_share_github.py`, `tests/test_share_fetch.py`, and `FakeRunner` in `tests/fakes.py`.
