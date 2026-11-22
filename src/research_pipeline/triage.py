"""Triage stage: embed new papers, rank them, keep the top N. See docs/guide/triage.md."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from research_pipeline.db import current_votes, list_saved, papers_with_status, set_status
from research_pipeline.embed import Embedder
from research_pipeline.models import Profile, Status
from research_pipeline.rank import (
    build_feedback_vectors,
    build_profile_vectors,
    build_shelf_vectors,
    score_papers,
)
from research_pipeline.shelf import sync_shelf


def _text(row: sqlite3.Row) -> str:
    return f"{row['title']}\n{row['abstract']}" if row["abstract"] else row["title"]


def run_triage(
    conn: sqlite3.Connection,
    embedder: Embedder,
    profile: Profile,
    seed_texts: dict[str, str],
    my_work: Path | None = None,
) -> dict[str, int]:
    """Rank `new` papers; top `profile.top_n` become triaged, the rest skipped.

    With `my_work`, the shelf is synced first, even when there are no new papers (new or edited notes are embedded) and papers near
    your own notes get a boost.
    """
    if my_work is not None:
        sync_shelf(conn, my_work, embedder)  # always, so the index follows your files even on quiet nights
    papers = papers_with_status(conn, Status.new)
    if not papers:
        return {"triaged": 0, "skipped": 0, "embedded": 0}

    model = embedder.model_name
    stored = {
        r["item_id"]: np.frombuffer(r["vector"], dtype=np.float32)
        for r in conn.execute(
            "SELECT item_id, vector FROM embeddings WHERE embedding_model = ?", (model,)
        )
    }
    todo = [p for p in papers if p["id"] not in stored]
    if todo:
        new_vecs = np.asarray(embedder.embed([_text(p) for p in todo]), dtype=np.float32)
        for p, v in zip(todo, new_vecs, strict=True):
            stored[p["id"]] = v
            conn.execute(
                "INSERT OR REPLACE INTO embeddings (item_id, embedding_model, vector, dim)"
                " VALUES (?, ?, ?, ?)",
                (p["id"], model, v.tobytes(), len(v)),
            )

    paper_vecs = np.stack([stored[p["id"]] for p in papers])
    pv = build_profile_vectors(embedder, profile, seed_texts)
    votes, saves = current_votes(conn), list_saved(conn)
    wanted = {r["paper_id"] for r in votes} | {r["paper_id"] for r in saves}
    feedback = build_feedback_vectors(
        votes, saves, {k: v for k, v in stored.items() if k in wanted},
        profile.feedback_halflife_days, datetime.now(UTC),
    )
    shelf = None
    if my_work is not None:
        shelf = build_shelf_vectors(conn, embedder)
    ranked = score_papers(papers, paper_vecs, profile, pv, feedback, shelf)

    for i, s in enumerate(ranked):
        if i < profile.top_n:
            set_status(conn, s.id, Status.triaged, score=s.score, reasons=s.reasons)
        else:
            set_status(conn, s.id, Status.skipped, score=s.score, reasons=s.reasons)
    conn.commit()
    kept = min(profile.top_n, len(ranked))
    return {"triaged": kept, "skipped": len(ranked) - kept, "embedded": len(todo)}
