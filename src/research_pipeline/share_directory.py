"""The directory: feeds that people published, found through GitHub's search for the ``rpipe-feed`` topic.

There is no gatekeeper. Anyone who tags a repository ``rpipe-feed`` can show up; junk is filtered
by actually downloading each candidate's ``feed.json`` and keeping only the ones that are valid
rpipe feeds. The result is cached in the ``directory_entries`` table; the cache is replaced only
when the search itself worked, so being offline never empties the list.
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx

from research_pipeline import feed_format
from research_pipeline.db import replace_directory
from research_pipeline.feed_format import FeedError, FeedFile
from research_pipeline.progress import Reporter, stage
from research_pipeline.share_fetch import FetchFailed, get_limited
from research_pipeline.share_publish import feed_url

SEARCH_URL = "https://api.github.com/search/repositories?q=topic:rpipe-feed+fork:false&per_page=100"
SEARCH_MAX_BYTES = 5_000_000
REPO_NAME = "rpipe-feed"
PREVIEW_ITEMS = 3
ONE_LINE_MAX = 160


def _candidates(data: bytes) -> list[str]:
    """Owner logins of the search hits that are named ``rpipe-feed``, each once, in order."""
    try:
        hits = json.loads(data)["items"]
    except (ValueError, KeyError, TypeError) as exc:
        raise FetchFailed("GitHub's answer wasn't the expected search result") from exc
    if not isinstance(hits, list):
        raise FetchFailed("GitHub's answer wasn't the expected search result")
    owners: list[str] = []
    for hit in hits:
        try:
            if hit["name"] != REPO_NAME:
                continue
            login = hit["owner"]["login"]
        except (KeyError, TypeError):
            continue
        if isinstance(login, str) and login and login.lower() not in (o.lower() for o in owners):
            owners.append(login)
    return owners


def _day(text: str):
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _entry(login: str, feed: FeedFile, now: datetime) -> dict:
    """The ``directory_entries`` row for one valid feed."""
    cutoff = (now - timedelta(days=feed_format.RECENT_DAYS)).date()
    dated = [(d, item) for item in feed.items if (d := _day(item.date_published)) is not None]
    recent = sum(1 for d, _ in dated if d >= cutoff)
    newest = sorted(dated, key=lambda p: p[0], reverse=True)[:PREVIEW_ITEMS]
    preview = [
        {"title": item.title,
         "one_line": (item.rpipe.summary.summary.one_line if item.rpipe.summary else item.summary)[:ONE_LINE_MAX]}
        for _, item in newest
    ]
    return {
        "feed_id": f"github:{login.lower()}/{REPO_NAME}", "feed_url": feed_url(login),
        "handle": login, "display_name": feed.rpipe.display_name, "topics": list(feed.rpipe.topics),
        "posts_per_day": round(recent / feed_format.RECENT_DAYS, 1), "preview": preview,
        "checked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def refresh(conn: sqlite3.Connection, http: httpx.Client, reporter: Reporter | None,
            now: datetime | None = None) -> tuple[int, list[str]]:
    """Search GitHub, check every hit's feed, and replace the cached directory.

    Returns ``(number of feeds in the directory, errors)``. A hit that fails in any way is left
    out. If the search itself fails the old list stays and the error is returned.
    """
    now = now or datetime.now(UTC)
    with stage(reporter, "directory", label="Refreshing the directory") as st:
        st.step("Searching GitHub")
        try:
            owners = _candidates(get_limited(http, SEARCH_URL, SEARCH_MAX_BYTES))
        except FetchFailed as exc:
            kept = conn.execute("SELECT COUNT(*) FROM directory_entries").fetchone()[0]
            return kept, [f"directory: the search failed, keeping the old list: {exc}"]
        entries: list[dict] = []
        for n, login in enumerate(owners, 1):
            st.step(f"checking {n} of {len(owners)}: {login}", current=n)
            try:
                feed, _ = feed_format.parse_feed(get_limited(http, feed_url(login), feed_format.FEED_MAX_BYTES))
                entries.append(_entry(login, feed, now))
            except (FetchFailed, FeedError):
                continue
            except Exception:  # noqa: BLE001 - one odd feed must never spoil the directory
                continue
        replace_directory(conn, entries)
        conn.commit()
        return len(entries), []
