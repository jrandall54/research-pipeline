"""Triage stage: embed new papers, rank them, keep the top N. See docs/guide/triage.md."""

import sqlite3

import numpy as np

from research_pipeline.db import papers_with_status, set_status
from research_pipeline.embed import Embedder
from research_pipeline.models import Profile, Status
from research_pipeline.rank import build_profile_vectors, score_papers


def _text(row: sqlite3.Row) -> str:
    return f"{row['title']}\n{row['abstract']}" if row["abstract"] else row["title"]


def run_triage(
    conn: sqlite3.Connection,
    embedder: Embedder,
    profile: Profile,
    seed_texts: dict[str, str],
) -> dict[str, int]:
    """Rank `new` papers; top `profile.top_n` become triaged, the rest skipped."""
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
    ranked = score_papers(papers, paper_vecs, profile, pv)

    for i, s in enumerate(ranked):
        if i < profile.top_n:
            set_status(conn, s.id, Status.triaged, score=s.score, reasons=s.reasons)
        else:
            set_status(conn, s.id, Status.skipped, score=s.score, reasons=s.reasons)
    conn.commit()
    kept = min(profile.top_n, len(ranked))
    return {"triaged": kept, "skipped": len(ranked) - kept, "embedded": len(todo)}
