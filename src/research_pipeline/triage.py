"""Triage stage: embed new papers, rank them, keep the top N. See docs/guide/triage.md."""

import sqlite3
from datetime import UTC, datetime

import numpy as np

from research_pipeline.db import papers_with_status, set_status
from research_pipeline.embed import Embedder
from research_pipeline.library import current_votes, list_saved
from research_pipeline.models import Profile, Status
from research_pipeline.progress import Reporter, stage
from research_pipeline.rank import (
    build_feedback_vectors,
    build_profile_vectors,
    build_shelf_vectors,
    score_papers,
)


def _text(row: sqlite3.Row) -> str:
    return f"{row['title']}\n{row['abstract']}" if row["abstract"] else row["title"]


def run_triage(
    conn: sqlite3.Connection,
    embedder: Embedder,
    profile: Profile,
    seed_texts: dict[str, str],
    *,
    use_shelf: bool = False,
    reporter: Reporter | None = None,
) -> dict[str, int]:
    """Rank `new` papers; top `profile.top_n` become triaged, the rest skipped.

    With ``use_shelf=True`` papers near your own notes (the shelf rows already in the
    database) get a boost. Triage only reads the shelf: bringing it in line with your
    ``my_work/`` files is the caller's job (`rpipe run` and `rpipe triage` sync it just before).
    """
    papers = papers_with_status(conn, Status.new)
    if not papers:
        with stage(reporter, "triage"):  # nothing to do: start and done only
            pass
        return {"triaged": 0, "skipped": 0, "embedded": 0}

    model = embedder.model_name
    stored = {
        r["item_id"]: np.frombuffer(r["vector"], dtype=np.float32)
        for r in conn.execute(
            "SELECT item_id, vector FROM embeddings WHERE embedding_model = ?", (model,)
        )
    }
    todo = [p for p in papers if p["id"] not in stored]
    with stage(reporter, "triage", total=None) as handle:
        if todo:
            handle.note(f"embedding {len(todo)} papers")  # one embed call, so no per-paper steps
            new_vecs = np.asarray(embedder.embed([_text(p) for p in todo]), dtype=np.float32)
            for p, v in zip(todo, new_vecs, strict=True):
                stored[p["id"]] = v
                conn.execute(
                    "INSERT OR REPLACE INTO embeddings (item_id, embedding_model, vector, dim)"
                    " VALUES (?, ?, ?, ?)",
                    (p["id"], model, v.tobytes(), len(v)),
                )
        return _rank_and_store(conn, embedder, profile, seed_texts, use_shelf, papers, stored, len(todo))


def _rank_and_store(conn, embedder, profile, seed_texts, use_shelf, papers, stored, n_embedded):
    paper_vecs = np.stack([stored[p["id"]] for p in papers])
    pv = build_profile_vectors(embedder, profile, seed_texts)
    votes, saves = current_votes(conn), list_saved(conn)
    wanted = {r["paper_id"] for r in votes} | {r["paper_id"] for r in saves}
    feedback = build_feedback_vectors(
        votes, saves, {k: v for k, v in stored.items() if k in wanted},
        profile.feedback_halflife_days, datetime.now(UTC),
    )
    shelf = build_shelf_vectors(conn, embedder) if use_shelf else None
    ranked = score_papers(papers, paper_vecs, profile, pv, feedback, shelf)

    for i, s in enumerate(ranked):
        if i < profile.top_n:
            set_status(conn, s.id, Status.triaged, score=s.score, reasons=s.reasons)
        else:
            set_status(conn, s.id, Status.skipped, score=s.score, reasons=s.reasons)
    conn.commit()
    kept = min(profile.top_n, len(ranked))
    return {"triaged": kept, "skipped": len(ranked) - kept, "embedded": n_embedded}
