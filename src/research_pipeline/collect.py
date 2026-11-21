"""Collect stage: fetch from every source and upsert into the database."""

import sqlite3

from research_pipeline.db import get_paper, upsert_paper
from research_pipeline.sources.base import Source


def run_collect(
    conn: sqlite3.Connection, sources: list[Source], run_id: int | None = None
) -> tuple[dict[str, int], list[str]]:
    """Fetch each source in order and store its papers; return (counts, errors).

    A source that raises is logged as ``"<name>: <message>"`` and skipped; the
    others still run. Each source's papers are committed before the next source
    starts; a source that fails partway is rolled back, so it leaves no rows.
    Sources that offer ``set_known_checker`` get a DB-backed "already stored?" test,
    and a source's non-fatal ``last_warning`` is added to the errors. This function does not call ``finish_run``: the caller aggregates
    counts across stages and finishes the run once. ``run_id`` is optional (the standalone
    ``collect`` command has no run) and is currently unused: reserved for future
    per-run tagging.
    """
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "source_errors": 0}
    errors: list[str] = []
    def is_known(paper_id: str) -> bool:
        return get_paper(conn, paper_id) is not None

    for source in sources:
        if hasattr(source, "set_known_checker"):
            source.set_known_checker(is_known)
        try:
            papers = source.fetch()
            for paper in papers:
                counts[upsert_paper(conn, paper)] += 1
            conn.commit()
            warning = getattr(source, "last_warning", None)
            if warning:
                errors.append(warning)
        except Exception as exc:  # noqa: BLE001 - isolate every source failure
            conn.rollback()
            counts["source_errors"] += 1
            errors.append(f"{source.name}: {exc}")
    return counts, errors
