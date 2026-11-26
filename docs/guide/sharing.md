# Sharing your feed with other rpipe users (Phase 10)

## What it does

Lets you publish the papers rpipe picked for you, with their short summaries, so other rpipe users can follow your feed, and lets you follow theirs. Publishing is opt-in: nothing leaves your machine unless sharing is turned on. This page is filled in step by step; this first part describes the files that get published. The commands, following and the browsing screens are added as they are built.

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

(Fetching and the directory are filled in by later tasks.)

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

**Stopping.** Turning sharing off removes the directory topic. Deleting the feed deletes the `rpipe-feed` repository (`gh` first asks GitHub for the extra `delete_repo` permission); followers then see your feed disappear.

Every GitHub operation goes through a small `Runner` (it runs one command and returns its output). The real one runs `git` and `gh` with `GIT_TERMINAL_PROMPT=0` so nothing ever waits on a hidden prompt; tests pass a `FakeRunner` that records the commands and plays back scripted replies, so no test touches GitHub.
## Why it's built this way

- JSON Feed plus Atom: one open, boring format that other tools can read, with the rpipe extras tucked under `_rpipe` so readers that don't know them ignore them.
- Files are byte-stable (sorted keys, fixed separators, and `generated_at` taken from the newest post rather than the clock): a night with no new posts produces identical bytes, so git has nothing to commit. The one exception is a post aging out of the 30-day window, which changes `feed.json` on purpose.
- Month archives with fingerprints instead of one growing file: `feed.json` stays small and a follower downloads only archives that changed.
- Rejected: publishing everything and filtering on the reader's side. Private reasons must never leave your machine in the first place.

## How to run / check it

(Added with the `rpipe share` commands.) Tests: `uv run pytest tests/test_feed_format.py -q`.

## Where the code is

- `src/research_pipeline/feed_format.py`: the file shapes, `build_files`, `parse_feed`, `parse_archive`, `parse_reason`, `public_reasons` and `shared_topics`. It is in the shared group (see [architecture.md](architecture.md)), so both the pipeline and the app can use it. It touches no database and no network.
- `src/research_pipeline/share_publish.py`: `collect_posts`, `write_site`, `push`, `publish`, `wait_live`, `feed_url`. Pipeline group.
- `src/research_pipeline/share_github.py`: the `Runner` protocol, `SubprocessRunner`, and the setup operations (`check_tools`, `login`, `repo_status`, `create_repo`, `set_topic`, `enable_pages`, `setup_git_credentials`, `delete_repo`). Pipeline group.
- `tests/test_share_publish.py`, `tests/test_share_github.py`, and `FakeRunner` in `tests/fakes.py`.
