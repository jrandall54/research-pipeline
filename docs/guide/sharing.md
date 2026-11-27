# Sharing your feed with other rpipe users (Phase 10)

## What it does

Lets you publish the papers rpipe picked for you, with their short summaries, so other rpipe users can follow your feed, and lets you follow theirs. Publishing is opt-in: nothing leaves your machine unless sharing is turned on. This page covers the files that get published, how the directory finds feeds, following, publishing, the reasons behind the design, and the commands. The People page in the app is described in app.md.

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

**Limits.** Every download is https only (a redirect to `http://` fails the fetch), stops reading once it passes its cap (5 MB for `feed.json`, 20 MB per archive), and has two time limits: 20 seconds for any single network step, and 60 seconds for the whole file, measured while it streams in (a server that trickles bytes cannot hold a download for hours). At most 500 archives per feed, and an archive path must look like `archive/YYYY-MM.json` (optionally `-N`), so a feed cannot point rpipe at an arbitrary address. Invalid items (for example a 600-character title) are skipped and counted; the rest are stored.

**The night's budget.** `fetch_all` starts no new feed once 10 minutes have passed; the feeds it did not reach are listed as "fetch Alex M.: skipped, out of time" and are fetched the next time (this is not counted as a failure of theirs). The directory refresh has its own 5-minute budget and keeps the old list if it runs out. This keeps a slow or hostile host from delaying the digest.

**Unfollow and follow again.** Unfollowing deletes the feed's posts (except ones you saved or voted on) and also forgets which archives were downloaded and the failure count, so following again downloads the whole past again, exactly like the first time.

**Text from other people.** Display names, topics and titles are stripped of control characters (including ANSI escape codes, which could move the cursor or hide text in your terminal) when they are stored, and again before they are printed by `rpipe share` commands, progress lines and the digest's health line (`feed_format.clean_text`). The app already shows everything escaped.

**When a fetch fails.** Nothing new is stored, the reason is kept (`follows.last_error`) and `failures` goes up by one. One feed failing never stops the others: `fetch_all` returns an error line like "fetch Alex M.: the server answered 500" and moves on. A success resets `failures` to 0.

**What "stopped publishing" means.** A feed counts as stopped (`is_stopped`) after 7 failed fetches in a row (`STOPPED_AFTER`), or when its own `_rpipe.active` is false (its owner turned sharing off). Its old posts stay, rpipe keeps trying each night, and a good download makes it active again. The message says only "stopped publishing": it does not show the date, because rpipe does not record when a feed stopped (a deliberate simplification of the design).

**Progress.** The `fetch` stage reports one step per feed ("fetching 2 of 3: Alex M."), and one per archive on a first fetch.


### Publishing

Publishing turns your database into the three kinds of file above and pushes them to a GitHub repository of yours named `rpipe-feed`, which GitHub Pages then serves at `https://<your handle, lowercase>.github.io/rpipe-feed/feed.json`.

**One-time setup** (the `rpipe share` commands run these steps for you, with prompts; you never type `git` or `gh` yourself):

1. Check that `git` and the GitHub command line tool `gh` are installed; if not, say which and how to install it.
2. Sign in with `gh auth login` if needed, then read your GitHub handle.
3. Look at `<handle>/rpipe-feed`. Not there: create it (public, with a first commit so the `main` branch exists) and clone it into rpipe's data folder. Already an rpipe feed: reuse it. Something else under that name: stop and ask, never overwrite someone's other repository.
4. Run `gh auth setup-git` so a plain `git push` uses your GitHub sign-in. Without this the nightly run would wait for a password nobody is there to type.
5. Publish once (see "Each night"), then add the repository topic `rpipe-feed`, which is how the public directory finds feeds. Turning sharing off removes the topic.
6. Turn on GitHub Pages for branch `main`, folder `/`. If it is already on, that is fine.

Then setup waits until the address answers and runs a test push before it records that sharing is on.

**When you change your topics** (Phase 10.3). `rpipe profile add-topic`, `remove-topic` and `suggest` (when it added topics) republish your feed straight away if sharing is on, with the usual progress lines, and print "Published your feed with the new topics." Followers and the directory then see the change the next time they look, not after tonight's run. Without this a removed topic (the owner's typo "sexurity") stayed in the public `feed.json` and on the People page until the night. The topic edit is saved first; if the push fails, or another rpipe command holds the vault lock, you get a warning ("Your topics are saved, but publishing failed (tonight's run will retry): ...") and the command still succeeds. It then restarts the background app, if installed, so People shows the new topics. `rpipe init` does not republish: sharing is set up after it. Code: `cli._topics_changed`.

**Each night** (the `publish` stage, after the digest is written):

1. `collect_posts` reads every paper that has appeared in a digest, ranked within its day by triage score (ties by paper id), with its summary and provenance. Reasons pass through `public_reasons`, and diagrams are never read.
2. `feed_format.build_files` builds the files. Papers older than 30 days move into month archives.
3. `write_site` writes them into the clone and deletes any `archive/*.json` that is no longer built. It reports whether anything changed.
4. `push` runs `git add -A`; if something is staged it commits ("rpipe feed 2026-11-26") and runs `git push`. If nothing changed, nothing is pushed: because the files are byte-stable, a quiet night makes no commit at all.
5. The first time only, `wait_live` polls the feed address every 10 seconds (up to 5 minutes, following https redirects, so a custom Pages domain works) until GitHub Pages serves a valid rpipe feed, so setup can say "your feed is live" and not just "pushed".

Worked example: your digest on 2026-11-26 lists 3 papers. `collect_posts` returns 3 posts ranked 1 to 3 (plus all earlier ones). Two of them were picked for "liked similar: 2611.01234"; that reason is dropped, the topic reason stays. The files differ from last night's, so one commit and one push happen, and the progress line reads "Pushing to GitHub".

**Your email is never published.** Every commit in your `rpipe-feed` repository is public, and git normally stamps commits with your own name and email. So rpipe sets a git identity inside that clone only: the name is your display name and the email is `<handle>@users.noreply.github.com`, GitHub's private address. It is set before every publish (so clones made earlier are fixed too), and it also means a computer with no git identity configured can still publish. Your global git settings are not touched.

**Time limits.** Every `git` and `gh` command rpipe runs for sharing stops after 120 seconds with a plain message ("`git push` took longer than 120 seconds and was stopped..."), so a hung network or a locked keyring can never hold the nightly run, and its lock, all night.

**Credentials.** rpipe stores no password or token. Pushes use the sign-in that `gh` keeps (set up in step 4). `gh` keeps its token in your system keyring; if the keyring is locked when the nightly run starts (for example before you log in) the push fails and is retried the next night. If a push fails (signed out, no network, repository deleted) the stage fails with git's own message, for example "Pushing the feed failed: remote: Permission denied", and the rest of the night's run is unaffected.

**The app after a sharing change.** `setup`, `set-name` and `stop` finish by restarting the background app (`research-pipeline-app`, see app.md) if it is installed and running, because the People page reads your sharing state when the app starts. It prints "Restarted the app so it shows the change."; if the app is not installed nothing happens, and a failed restart never fails the command.

**Stopping.** Turning sharing off removes the directory topic. If a push fails, the commit stays local and the next night pushes it. Deleting the feed deletes the `rpipe-feed` repository; followers then see your feed disappear. Deleting needs an extra GitHub permission (`delete_repo`): `rpipe share stop --delete` checks whether your sign-in already has it and, if not, says so and runs `gh auth refresh` on your terminal (it shows a one-time code and may open a browser). While it waits for you the command lets go of the vault lock (like every question rpipe asks), so a nightly run is never held up by an unanswered prompt.


**What GitHub can see.** Commits carry timestamps, so anyone looking at your public `rpipe-feed` history can tell roughly when your nightly run happens and which time zone you are in. This comes with publishing through git and cannot be hidden; it is one more reason publishing is opt-in.

Every GitHub operation goes through a small `Runner` (it runs one command and returns its output). The real one runs `git` and `gh` with `GIT_TERMINAL_PROMPT=0` so nothing ever waits on a hidden prompt; tests pass a `FakeRunner` that records the commands and plays back scripted replies, so no test touches GitHub.
## Why it's built this way

- JSON Feed plus Atom: one open, boring format that other tools can read, with the rpipe extras tucked under `_rpipe` so readers that don't know them ignore them.
- Files are byte-stable (sorted keys, fixed separators, and `generated_at` taken from the newest post rather than the clock): a night with no new posts produces identical bytes, so git has nothing to commit. The one exception is a post aging out of the 30-day window, which changes `feed.json` on purpose.
- Month archives with fingerprints instead of one growing file: `feed.json` stays small and a follower downloads only archives that changed.
- Rejected: publishing everything and filtering on the reader's side. Private reasons must never leave your machine in the first place.
- Fetch the whole feed before storing anything, then store and commit once: a feed that breaks halfway leaves your database as it was, never half a feed. Rejected: storing as each archive arrives.
- Redirects are followed by rpipe itself, one hop at a time, so every hop can be checked as https. Rejected: letting the HTTP library follow them (it would accept a redirect to plain `http`).
- The fingerprint in `feed.json` is only a change marker (is this archive new to me?), not a security check; every download is validated by its own parse.
- Archive addresses are built from the feed's folder and only for paths that match the published pattern, so a hostile feed cannot make rpipe fetch some other address.

**How feeds could have travelled between people** (the choice was made in the design conversation). Chosen: static feed files on free GitHub Pages. Each night your rpipe pushes its files to your own public repository and followers' rpipe downloads them. It costs nothing, needs no server, exposes nothing on your computer and still works while your computer is off. The price: you need a free GitHub account to publish (not to follow), and GitHub can see the addresses of readers. This is the same idea as podcasts: a stable address per feed, with the files behind it updated nightly, so there is no central database.

- Rejected: a shared sync folder (Syncthing, Drive, Dropbox). Fine inside one club, but it is not public, everyone gets every file, and Syncthing needs both computers on. It stays a possible fallback for a group that wants privacy; not built.
- Rejected: one shared cloud storage bucket. Smooth, but someone has to own and pay for it and hand out keys, which makes them a gatekeeper.
- Rejected: Tailscale Funnel or node sharing. Little to build, but your home computer becomes the server and must stay on; the most intrusive option.
- Rejected: open networks (Nostr, IPFS, the AT Protocol). No company in the middle, but they are young, relays drop old posts, and they are close to social-media infrastructure.
- Rejected: sending a file by hand. It works today but is not following: nothing updates.

**How people find each other.** Chosen: a GitHub tag. Each feed repository carries the topic `rpipe-feed`, browsing searches GitHub for it, and only repositories that really serve a valid rpipe feed are listed. Rejected: a shared list in one repository where joining is a pull request that someone approves. That is a gatekeeper, and the owner ruled out any gatekeeper.

## How to run / check it

You need `git` and the GitHub command line tool `gh` only to *publish*; following needs neither. Every setting is a command; you never edit a file.

| Command | What it does |
| --- | --- |
| `rpipe share setup` | Guided first-time publishing; run it again to repair things |
| `rpipe share status` | Publishing on/off, your address, last push and its result, then each followed feed |
| `rpipe share publish` | Rebuild and push now (the nightly run does this on its own; changing your topics also does it, see below) |
| `rpipe share set-name NAME` | Change your display name (republishes) |
| `rpipe share stop [--delete]` | Stop publishing and leave the directory; `--delete` also deletes the repository (asks first) |
| `rpipe share browse [SEARCH]` | Refresh the directory and list it, optionally filtered. Each person is a short block: name and handle, papers a day and topics in common, then at most 3 topics (the ones you share first, the rest counted as "+N more"); SEARCH still matches every topic |
| `rpipe share follow HANDLE` | Follow a feed (or an `https://` feed address) and fetch it now |
| `rpipe share unfollow HANDLE` | Stop following; posts you saved or voted on are kept |
| `rpipe share following` | List who you follow |
| `rpipe share fetch` | Fetch every followed feed now |
| `rpipe share mix add HANDLE` / `remove HANDLE` | Include or leave out a followed feed in Mix |

`rpipe share status` looks like this (with colour on a terminal, plain in a pipe):

```
Publishing: on as 'Alex M.'
  Address            https://alex.github.io/rpipe-feed/feed.json
  Last local commit  2026-11-26 03:00:05
  Last night's push  ok
Following 1:
  • Bo (@bo) · 212 posts, fetched 2026-11-26 · in Mix
```

In `browse`, the name is bold and the two lines under it are dimmed; the name, handle and topics are cleaned with `clean_text` before they are styled, so a hostile display name cannot inject terminal codes. The look is described in [terminal.md](terminal.md).

Example session (Alex publishes, Sam follows):

```
$ rpipe share setup
› Display name shown to people who follow you [Alex Morgan]: Alex M.
! This makes your feed public. Anyone can read it. Copies people have already downloaded can't be pulled back if you stop.
› Publish your feed? [y/N]: y
  publish ... pages ... (progress)
✓ Your feed is live: https://alex.github.io/rpipe-feed/feed.json
  It updates every night. Others can follow it with `rpipe share follow alex`.

$ rpipe share browse robotics            # on Sam's machine
Alex M.  @alex
  about 8 papers a day · 1 topic in common
  robotics, reinforcement learning, compilers, +4 more

  Follow one with `rpipe share follow HANDLE`.
$ rpipe share follow alex
✓ Following alex: 212 posts downloaded.
$ rpipe share mix remove alex            # keep their feed out of Mix
$ rpipe share stop                       # Alex, later
```

What `setup` does, in order: checks `git` and `gh` (and says how to install a missing one), signs you in with `gh auth login` if needed (it lets go of the vault lock while you do), looks at `<you>/rpipe-feed` (if something that is not an rpipe feed lives there it stops and tells you to rename or delete it), asks for the display name, shows the public warning and asks you to confirm, creates the repository (or, if it exists but this computer has no copy, clones it), runs `gh auth setup-git`, publishes, adds the directory topic, turns on Pages, waits until the address answers, runs a test push with no prompts allowed (`git push --dry-run`), and only then records `share_enabled` in `config.yaml`. A failure at any step leaves sharing off, and running `setup` again carries on. `stop` publishes once more with `active: false` (so followers see "stopped publishing"), removes the topic and turns sharing off; with `--delete` it asks, deletes the repository and removes the local copy under `<vault>/share/rpipe-feed/`.

**Progress.** Every waiting step (publishing, waiting for Pages, fetching, refreshing the directory) shows a progress line; see "Progress" in [running.md](running.md). The nightly run's two sharing stages (fetch before the digest, publish after it) are described under "The sharing stages" in running.md.

**Decisions.** `follow`, `browse`, `publish`, `fetch`, `setup` and `stop` take the pipeline lock (they write in bulk and use the network); `status`, `following`, `unfollow`, `mix` do not, because they are quick. `following`, `unfollow` and `mix` go through `library_api` like the app does, and map the handle you type (any case, with or without `@`) to the feed. The HTTP client sends `User-Agent: research-pipeline/0.1` because GitHub's API refuses requests without one, and it never follows redirects by itself (`get_limited` does, https only). Rejected: asking the user to run `git` or `gh` themselves, and editing `config.yaml` by hand.

Tests: `uv run pytest tests/test_cli_share.py tests/test_runs.py tests/test_feed_format.py tests/test_share_publish.py tests/test_share_github.py tests/test_share_fetch.py -q`.

## Where the code is

- `src/research_pipeline/cli.py`: the `share` command group (`share_setup`, `share_status`, `share_publish_now`, `share_set_name`, `share_stop`, `share_browse`, `share_follow`, `share_unfollow`, `share_following`, `share_fetch_now`, `share_mix_add`, `share_mix_remove`), the factories `_share_runner` and `_share_http` that tests replace, `_publish_feed`, and `_share_fetch_stage` (before the digest) and `_share_publish_stage` (after it), the nightly stages.
- `tests/test_cli_share.py`: the commands.
- `src/research_pipeline/feed_format.py`: the file shapes, `build_files`, `parse_feed`, `parse_archive`, `parse_reason`, `public_reasons` and `shared_topics` and `clean_text`. It is in the shared group (see [architecture.md](architecture.md)), so both the pipeline and the app can use it. It touches no database and no network.
- `src/research_pipeline/share_publish.py`: `collect_posts`, `write_site`, `push`, `publish`, `wait_live`. Pipeline group.
- `src/research_pipeline/share_github.py`: the `Runner` protocol, `SubprocessRunner`, and the setup operations (`check_tools`, `login`, `repo_status`, `create_repo`, `set_topic`, `enable_pages`, `set_identity`, `setup_git_credentials`, `has_delete_scope`, `delete_repo`, `feed_url`). Pipeline group.
- `src/research_pipeline/share_fetch.py`: `resolve`, `get_limited`, `fetch_one`, `follow`, `fetch_all`, `is_stopped`, `STOPPED_AFTER`, `FILE_DEADLINE_S`, `FETCH_BUDGET_S` (re-exported from `feed_format`). Pipeline group.
- `tests/test_share_publish.py`, `tests/test_share_github.py`, `tests/test_share_fetch.py`, and `FakeRunner` in `tests/fakes.py`.
