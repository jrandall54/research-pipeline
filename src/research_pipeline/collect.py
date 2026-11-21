"""Collect stage: fetch from every source and upsert into the database."""

import sqlite3

from research_pipeline.db import upsert_paper
from research_pipeline.sources.base import Source


def run_collect(
    conn: sqlite3.Connection, sources: list[Source], run_id: int | None = None
) -> tuple[dict[str, int], list[str]]:
    """Fetch each source in order and store its papers; return (counts, errors).

    A source that raises is logged as ``"<name>: <message>"`` and skipped; the
    others still run. Each source's papers are committed before the next source
    starts. This function does not call ``finish_run``: the caller aggregates
    counts across stages and finishes the run once. ``run_id`` is optional (the standalone
    ``collect`` command has no run) and is currently unused: reserved for future
    per-run tagging.
    """
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "source_errors": 0}
    errors: list[str] = []
    for source in sources:
        try:
            papers = source.fetch()
            for paper in papers:
                counts[upsert_paper(conn, paper)] += 1
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - isolate every source failure
            conn.rollback()
            counts["source_errors"] += 1
            errors.append(f"{source.name}: {exc}")
    return counts, errors
