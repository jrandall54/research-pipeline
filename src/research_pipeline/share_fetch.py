"""Follow other people's feeds and download them into your database, safely.

Everything fetched here is written by strangers. It is checked before it is stored: https only,
a byte cap on every download, a time limit, redirects only to https, archive paths that match
the published rule, and ``feed_format`` drops any item that is invalid. A feed that fails
stores nothing new; the failure is counted so a feed that stays broken is shown as stopped.
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin, urlparse

import httpx

from research_pipeline import feed_format
from research_pipeline.db import get_follow, list_follows, record_fetch, replace_followed_posts, upsert_follow
from research_pipeline.feed_format import FeedError, Item
from research_pipeline.progress import Reporter, StageHandle, stage
from research_pipeline.share_publish import feed_url

STOPPED_AFTER = 7  # consecutive failed fetches before a feed counts as "stopped publishing"
TIMEOUT_S = 20
MAX_REDIRECTS = 5
_HANDLE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


class FetchFailed(Exception):
    """A download that did not give usable bytes; the message is shown to the user."""


@dataclass
class FetchResult:
    new_posts: int
    skipped_items: int
    error: str | None


def resolve(target: str) -> tuple[str, str, str | None]:
    """Turn what the user typed into ``(feed_id, feed_url, handle)``.

    ``alex`` or ``@alex`` is a GitHub handle; ``https://...`` is a full feed address (handle None).
    """
    target = target.strip()
    if target.lower().startswith("https://"):
        if not urlparse(target).hostname:
            raise ValueError(f"'{target}' isn't a valid address.")
        return f"url:{target}", target, None
    if "://" in target:
        raise ValueError("Only https:// addresses can be followed.")
    handle = target.removeprefix("@")
    if not _HANDLE.match(handle):
        raise ValueError(f"'{target}' isn't a GitHub handle or an https:// feed address.")
    handle = handle.lower()
    return f"github:{handle}/rpipe-feed", feed_url(handle), handle


def get_limited(http: httpx.Client, url: str, max_bytes: int) -> bytes:
    """Download ``url`` (https only, redirects too), stopping as soon as it passes ``max_bytes``."""
    for _ in range(MAX_REDIRECTS + 1):
        if not url.lower().startswith("https://"):
            raise FetchFailed("it redirected to an address that isn't https")
        try:
            with http.stream("GET", url, follow_redirects=False, timeout=TIMEOUT_S) as r:
                if r.is_redirect:
                    location = r.headers.get("location")
                    if not location:
                        raise FetchFailed("it redirected without saying where")
                    url = urljoin(url, location)  # a malformed Location raises ValueError, caught below
                    continue
                if r.status_code != 200:
                    raise FetchFailed(f"the server answered {r.status_code}")
                declared = r.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise FetchFailed(f"it is too big (the limit is {max_bytes:,} bytes)")
                data = bytearray()
                for chunk in r.iter_bytes():
                    data += chunk
                    if len(data) > max_bytes:
                        raise FetchFailed(f"it is too big (the limit is {max_bytes:,} bytes)")
                return bytes(data)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
            raise FetchFailed(f"couldn't read it ({type(exc).__name__})") from exc
    raise FetchFailed("it redirected too many times")


def _post(item: Item) -> dict | None:
    """The ``followed_posts`` row for an item, or None when its date isn't a real date."""
    day = item.date_published[:10]
    try:
        date.fromisoformat(day)
    except ValueError:
        return None
    meta, paper = item.rpipe, item.rpipe.paper
    return {
        "paper_id": paper.id, "digest_date": day, "rank": meta.rank, "title": paper.title,
        "abstract": paper.abstract, "authors": list(paper.authors), "url": paper.url,
        "published": paper.published,
        "summary": meta.summary.model_dump(mode="json") if meta.summary else None,
        "reasons": list(meta.reasons), "provenance": meta.provenance,
    }


def _download(row: sqlite3.Row, http: httpx.Client, st: StageHandle):
    """Download and parse one feed. Returns (feed, posts, skipped, archive_hashes). Raises FetchFailed."""
    try:
        feed, skipped = feed_format.parse_feed(get_limited(http, row["feed_url"], feed_format.FEED_MAX_BYTES))
        have = json.loads(row["archive_hashes"] or "{}")
        hashes = dict(have)
        by_id: dict[str, dict] = {}
        refs = [a for a in feed.rpipe.archives if have.get(a.path) != a.sha256]
        for n, ref in enumerate(refs, 1):
            st.step(f"{row['display_name']}: archive {n} of {len(refs)}")
            data = get_limited(http, urljoin(row["feed_url"], ref.path), feed_format.ARCHIVE_MAX_BYTES)
            items, dropped = feed_format.parse_archive(data)
            skipped += dropped
            for item in items:
                post = _post(item)
                if post is None:
                    skipped += 1
                else:
                    by_id[post["paper_id"]] = post
            hashes[ref.path] = ref.sha256
        for item in feed.items:  # feed.json is newest, so it wins over an archive's older copy
            post = _post(item)
            if post is None:
                skipped += 1
            else:
                by_id[post["paper_id"]] = post
    except FeedError as exc:
        raise FetchFailed(str(exc)) from exc
    return feed, list(by_id.values()), skipped, hashes


def _failed(conn: sqlite3.Connection, feed_id: str, error: str) -> FetchResult:
    record_fetch(conn, feed_id, ok=False, error=error)
    conn.commit()
    return FetchResult(0, 0, error)


def _fetch(conn: sqlite3.Connection, feed_id: str, http: httpx.Client, st: StageHandle) -> FetchResult:
    row = get_follow(conn, feed_id)
    if row is None:
        raise ValueError(f"You don't follow {feed_id}.")
    try:
        feed, posts, skipped, hashes = _download(row, http, st)
    except FetchFailed as exc:
        return _failed(conn, feed_id, str(exc))
    except Exception as exc:  # noqa: BLE001 - one odd feed must never stop the others
        return _failed(conn, feed_id, f"unexpected error ({type(exc).__name__})")
    new = replace_followed_posts(conn, feed_id, posts)
    record_fetch(conn, feed_id, ok=True, error=None, display_name=feed.rpipe.display_name,
                 topics=feed.rpipe.topics, active=feed.rpipe.active, archive_hashes=hashes)
    conn.commit()
    return FetchResult(new, skipped, None)


def fetch_one(conn: sqlite3.Connection, feed_id: str, http: httpx.Client,
              reporter: Reporter | None = None) -> FetchResult:
    """Download one followed feed (changed archives only) into the database. Never raises on a bad feed."""
    with stage(reporter, "fetch", label="Fetching a followed feed") as st:
        return _fetch(conn, feed_id, http, st)


def follow(conn: sqlite3.Connection, target: str, http: httpx.Client, *,
           own_handle: str | None, reporter: Reporter | None) -> FetchResult:
    """Follow a feed and fetch its whole past. Stores nothing if the feed can't be read."""
    feed_id, url, handle = resolve(target)
    if own_handle and handle and handle == own_handle.removeprefix("@").lower():
        raise ValueError("That's your own feed.")
    with stage(reporter, "fetch", label=f"Following {target.strip()}") as st:
        st.step("Checking the feed")
        try:
            feed, _ = feed_format.parse_feed(get_limited(http, url, feed_format.FEED_MAX_BYTES))
        except (FetchFailed, FeedError) as exc:
            raise ValueError(f"No rpipe feed at {url}: {exc}") from exc
        upsert_follow(conn, feed_id, url, handle or urlparse(url).hostname or url,
                      feed.rpipe.display_name, feed.rpipe.topics)
        conn.commit()
        return _fetch(conn, feed_id, http, st)


def is_stopped(row: sqlite3.Row) -> bool:
    return row["failures"] >= STOPPED_AFTER or not row["active"]


def fetch_all(conn: sqlite3.Connection, http: httpx.Client,
              reporter: Reporter | None) -> tuple[dict[str, int], list[str]]:
    """Fetch every followed feed. One feed failing never stops the rest."""
    rows = list_follows(conn)
    counts = {"followed_feeds": len(rows), "followed_new": 0}
    errors: list[str] = []
    with stage(reporter, "fetch", total=len(rows), label="Fetching followed feeds") as st:
        for n, row in enumerate(rows, 1):
            name = row["display_name"]
            st.step(f"fetching {n} of {len(rows)}: {name}", current=n)
            result = _fetch(conn, row["feed_id"], http, st)
            counts["followed_new"] += result.new_posts
            if result.error:
                errors.append(f"fetch {name}: {result.error}")
    return counts, errors
