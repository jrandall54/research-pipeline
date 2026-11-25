"""The app's door into the database.

Commands that belong to the person using rpipe (as opposed to the nightly pipeline) go
through the functions here. This module must stay light: it imports only ``db``,
``library`` and ``ids`` from the package, never the language-model, profile-editing or
pipeline-stage code, so the app can run without them.
"""

import sqlite3

from research_pipeline import db, ids
from research_pipeline.db import REQUEST_KINDS, UnknownPaperError


def request(conn: sqlite3.Connection, kind: str, payload: dict) -> int:
    """Ask the pipeline to do some work later; return the request's id. Commits.

    Only kinds in ``db.REQUEST_KINDS`` are accepted (ValueError otherwise). For
    ``summarize`` the payload must hold a string ``paper_id``; it is normalised
    (``arXiv:2611.25005v2`` -> ``2611.25005``) and must name a paper already in the
    database, else UnknownPaperError. Asking again while an identical request is still
    pending or running returns the existing id.
    """
    if kind not in REQUEST_KINDS:
        raise ValueError(f"Unknown request kind {kind!r}; expected one of {', '.join(REQUEST_KINDS)}.")
    if kind == "summarize":
        raw = payload.get("paper_id")
        if not isinstance(raw, str):
            raise ValueError("A summarize request needs a paper_id (text, like 2611.25005).")
        try:
            paper_id = ids.normalise_paper_id(raw)
        except ValueError as exc:
            raise UnknownPaperError(f"{raw!r} is not a paper id (expected something like 2611.25005).") from exc
        if db.get_paper(conn, paper_id) is None:
            raise UnknownPaperError(f"No paper with id {paper_id!r} in the database.")
        payload = {**payload, "paper_id": paper_id}
    return db.insert_request(conn, kind, payload)
