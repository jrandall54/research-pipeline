"""Build the published site from the database and push it to the ``rpipe-feed`` repository.

``collect_posts`` reads papers, ``feed_format.build_files`` makes the bytes, ``write_site`` puts
them in the local clone, ``push`` sends them. Only public data goes in: reasons are filtered
through ``public_reasons`` and diagrams are never read.
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Literal

import httpx

from research_pipeline import feed_format
from research_pipeline.db import get_provenance, get_summary
from research_pipeline.feed_format import PaperBlock, Post
from research_pipeline.progress import Reporter, stage
from research_pipeline.share_fetch import FetchFailed, get_limited
from research_pipeline.share_github import GithubError, Runner, feed_url


def collect_posts(conn: sqlite3.Connection) -> list[Post]:
    """Every paper that was in a digest, ranked within its day by score (then id)."""
    rows = conn.execute(
        "SELECT * FROM papers WHERE digest_date IS NOT NULL"
        " ORDER BY digest_date, triage_score DESC, id ASC"
    ).fetchall()
    posts: list[Post] = []
    day, rank = None, 0
    for r in rows:
        rank = rank + 1 if r["digest_date"] == day else 1
        day = r["digest_date"]
        posts.append(Post(
            paper=PaperBlock(id=r["id"], title=r["title"], abstract=r["abstract"],
                             authors=json.loads(r["authors"]), url=r["url"], published=r["published"]),
            digest_date=day, rank=rank,
            summary=get_summary(conn, r["id"]),
            reasons=feed_format.public_reasons(json.loads(r["triage_reasons"] or "[]")),
            provenance=get_provenance(conn, r["id"]),
        ))
    return posts


def write_site(clone_dir: Path, files: dict[str, bytes]) -> bool:
    """Write the files into the clone and delete archives no longer built. True if anything changed."""
    changed = False
    for path, data in files.items():
        target = clone_dir / path
        if not target.exists() or target.read_bytes() != data:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            changed = True
    archive = clone_dir / "archive"
    if archive.is_dir():
        for old in archive.glob("*.json"):
            if f"archive/{old.name}" not in files:
                old.unlink()
                changed = True
    return changed


def _git(runner: Runner, clone_dir: Path, args: list[str], what: str):
    r = runner.run(["git", *args], cwd=clone_dir)
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip()
        raise GithubError(f"{what} failed: {detail}" if detail else f"{what} failed.")
    return r


def _unpushed(runner: Runner, clone_dir: Path) -> bool:
    """True when local commits haven't reached GitHub (for example an earlier push failed)."""
    r = runner.run(["git", "rev-list", "--count", "@{u}..HEAD"], cwd=clone_dir)
    return r.returncode == 0 and r.stdout.strip().isdigit() and int(r.stdout.strip()) > 0


def push(runner: Runner, clone_dir: Path, message: str) -> bool:
    """Stage everything, commit if something changed, push. Returns whether a push happened.

    Also pushes commits left over from an earlier failed push, so a bad night is retried.
    """
    _git(runner, clone_dir, ["add", "-A"], "Staging the feed files")
    staged = runner.run(["git", "diff", "--cached", "--quiet"], cwd=clone_dir).returncode != 0
    if not staged and not _unpushed(runner, clone_dir):
        return False
    if staged:
        _git(runner, clone_dir, ["commit", "-m", message], "Committing the feed")
    _git(runner, clone_dir, ["push"], "Pushing the feed")
    return True


def publish(conn: sqlite3.Connection, clone_dir: Path, runner: Runner, *, display_name: str,
            topics: list[str], active: bool, now: str, handle: str,
            reporter: Reporter | None = None) -> Literal["pushed", "unchanged"]:
    """Rebuild the feed files from the database and push them if they changed."""
    with stage(reporter, "publish", label="Publishing your feed") as st:
        posts = collect_posts(conn)
        st.step(f"Building {len(posts)} posts")
        base = feed_url(handle).removesuffix("feed.json")
        files = feed_format.build_files(posts, display_name=display_name, topics=topics,
                                        active=active, now=now, base_url=base)
        write_site(clone_dir, files)
        st.step("Pushing to GitHub")
        pushed = push(runner, clone_dir, f"rpipe feed {now[:10]}")
        return "pushed" if pushed else "unchanged"


def wait_live(http: httpx.Client, url: str, reporter: Reporter | None = None, *,
              timeout_s: int = 300, sleep=time.sleep) -> bool:
    """Poll every 10 s until the feed URL serves a valid rpipe feed; False after ``timeout_s``."""
    waited = 0
    with stage(reporter, "pages", label="Waiting for GitHub Pages to go live") as st:
        while True:
            try:  # get_limited follows https redirects, so a custom Pages domain works
                feed_format.parse_feed(get_limited(http, url, feed_format.FEED_MAX_BYTES))
                return True
            except (FetchFailed, feed_format.FeedError):
                pass
            if waited >= timeout_s:
                return False
            st.step(f"Not live yet ({waited}s)")
            sleep(10)
            waited += 10
