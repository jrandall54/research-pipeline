"""Resolve profile seed ids to text. See docs/guide/triage.md and docs/guide/running.md."""

import json
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path

from research_pipeline.models import PaperMeta

log = logging.getLogger(__name__)


class SeedFetchError(Exception):
    """Fetching missing seeds failed; `texts` holds the seeds that were resolved anyway."""

    def __init__(self, message: str, texts: dict[str, str]):
        super().__init__(message)
        self.texts = texts


def _text(title: str, abstract: str) -> str:
    return f"{title}\n{abstract}"


def _read_cache(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_seed_texts(
    conn: sqlite3.Connection,
    cache_path: Path,
    seed_ids: list[str],
    fetch: Callable[[list[str]], list[PaperMeta]],
) -> dict[str, str]:
    """Return `{seed_id: "title\\nabstract"}` for every seed we can find text for.

    Lookup order: the `papers` table, then the JSON cache at `cache_path`, then one
    `fetch` call for whatever is still missing (results are written to the cache).
    Seeds are never inserted into `papers`. If the fetch raises, `SeedFetchError`
    is raised with the seeds resolved so far in `.texts`; the caller carries on.
    """
    texts: dict[str, str] = {}
    for sid in seed_ids:
        row = conn.execute("SELECT title, abstract FROM papers WHERE id = ?", (sid,)).fetchone()
        if row is not None:
            texts[sid] = _text(row["title"], row["abstract"])

    cache = _read_cache(cache_path)
    for sid in seed_ids:
        if sid not in texts and isinstance(cache.get(sid), str):
            texts[sid] = cache[sid]

    missing = [sid for sid in seed_ids if sid not in texts]
    if not missing:
        return texts
    try:
        fetched = fetch(missing)
    except Exception as exc:  # noqa: BLE001 - any failure must not stop the run
        log.warning("could not fetch seed papers %s: %s", missing, exc)
        raise SeedFetchError(f"seeds: could not fetch {', '.join(missing)}: {exc}", texts) from exc

    new = {p.id: _text(p.title, p.abstract) for p in fetched if p.id in missing}
    if new:
        cache.update(new)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        texts.update(new)
    return texts
