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

(Filled in by later tasks: publishing, fetching, the directory.)

## Why it's built this way

- JSON Feed plus Atom: one open, boring format that other tools can read, with the rpipe extras tucked under `_rpipe` so readers that don't know them ignore them.
- Files are byte-stable (sorted keys, fixed separators, and `generated_at` taken from the newest post rather than the clock): a night with no new posts produces identical bytes, so git has nothing to commit. The one exception is a post aging out of the 30-day window, which changes `feed.json` on purpose.
- Month archives with fingerprints instead of one growing file: `feed.json` stays small and a follower downloads only archives that changed.
- Rejected: publishing everything and filtering on the reader's side. Private reasons must never leave your machine in the first place.

## How to run / check it

(Added with the `rpipe share` commands.) Tests: `uv run pytest tests/test_feed_format.py -q`.

## Where the code is

- `src/research_pipeline/feed_format.py`: the file shapes, `build_files`, `parse_feed`, `parse_archive`, `parse_reason`, `public_reasons` and `shared_topics`. It is in the shared group (see [architecture.md](architecture.md)), so both the pipeline and the app can use it. It touches no database and no network.
