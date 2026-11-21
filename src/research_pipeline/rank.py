"""Scoring: how well does each paper match the profile? See docs/guide/triage.md."""

import json
import logging
import math
import sqlite3
from dataclasses import dataclass

import numpy as np

from research_pipeline.embed import Embedder
from research_pipeline.models import Profile

log = logging.getLogger(__name__)

# Weights. Cosine similarity (roughly 0..1) is the main signal; boosts are nudges
# small enough that they reorder close calls but cannot rescue an off-topic paper.
HF_UPVOTE_WEIGHT = 0.05  # x log1p(upvotes): 1 vote ~0.035, 42 votes ~0.19; log stops viral papers dominating
AUTHOR_BOOST = 0.1  # per followed author on the paper: a strong but not decisive signal
KEYWORD_BOOST = 0.05  # per keyword found in title/abstract: weak, keywords are blunt
KEYWORD_BOOST_CAP = 0.2  # many keywords must not swamp similarity
MY_WORK_WEIGHT = 0.0  # TODO(phase4): similarity to the user's own notes in my_work/
VOTE_PENALTY = 0.0  # TODO(phase3): penalty for topics the user has downvoted


@dataclass
class ProfileVectors:
    seed_ids: list[str]
    seed_vecs: np.ndarray  # (len(seed_ids), d), may be empty
    topics: list[str]
    topic_vecs: np.ndarray  # (len(topics), d), may be empty


@dataclass
class ScoredPaper:
    id: str
    score: float
    reasons: list[str]


def build_profile_vectors(
    embedder: Embedder, profile: Profile, seed_texts: dict[str, str]
) -> ProfileVectors:
    """Embed the profile's seeds (those with known text) and topics."""
    seed_ids = [s for s in profile.seeds if s in seed_texts]
    missing = [s for s in profile.seeds if s not in seed_texts]
    if missing:
        log.warning("seed papers with no text, skipped: %s", ", ".join(missing))
    seed_vecs = embedder.embed([seed_texts[s] for s in seed_ids]) if seed_ids else np.zeros((0, 0))
    topic_vecs = embedder.embed(list(profile.topics)) if profile.topics else np.zeros((0, 0))
    return ProfileVectors(seed_ids, seed_vecs, list(profile.topics), topic_vecs)


def _best_match(vec: np.ndarray, pv: ProfileVectors) -> tuple[float, str | None]:
    """Highest cosine to any seed/topic vector, with its reason (unit-length vectors)."""
    best, reason = 0.0, None
    found = False
    for ids, vecs, fmt in (
        (pv.seed_ids, pv.seed_vecs, "closest seed: {} ({:.2f})"),
        (pv.topics, pv.topic_vecs, "topic: {} ({:.2f})"),
    ):
        if len(ids) == 0:
            continue
        sims = vecs @ vec
        i = int(np.argmax(sims))
        if not found or sims[i] > best:
            best, found = float(sims[i]), True
            reason = fmt.format(ids[i], best)
    return best, reason


def score_papers(
    papers: list[sqlite3.Row],
    paper_vecs: np.ndarray,
    profile: Profile,
    profile_vecs: ProfileVectors,
) -> list[ScoredPaper]:
    """Score papers; best first, ties broken by id for determinism."""
    followed = {a.lower(): a for a in profile.followed_authors}
    out: list[ScoredPaper] = []
    for row, vec in zip(papers, paper_vecs, strict=True):
        score, sim_reason = _best_match(vec, profile_vecs)
        reasons = [sim_reason] if sim_reason else []

        if row["hf_upvotes"] > 0:
            score += HF_UPVOTE_WEIGHT * math.log1p(row["hf_upvotes"])
            reasons.append(f"HF upvotes: {row['hf_upvotes']}")

        for author in json.loads(row["authors"]):
            if author.lower() in followed:
                score += AUTHOR_BOOST
                reasons.append(f"followed author: {author}")

        text = f"{row['title']} {row['abstract']}".lower()
        kw_boost = 0.0
        for kw in profile.keywords:
            if kw and kw.lower() in text:
                kw_boost += KEYWORD_BOOST
                reasons.append(f"keyword: {kw}")
        score += min(kw_boost, KEYWORD_BOOST_CAP)

        score += MY_WORK_WEIGHT * 0.0  # TODO(phase4)
        score -= VOTE_PENALTY * 0.0  # TODO(phase3)
        out.append(ScoredPaper(row["id"], score, reasons))

    out.sort(key=lambda s: (-s.score, s.id))
    return out
