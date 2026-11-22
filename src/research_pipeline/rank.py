"""Scoring: how well does each paper match the profile? See docs/guide/triage.md."""

import json
import logging
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

from research_pipeline.embed import Embedder
from research_pipeline.models import Profile

log = logging.getLogger(__name__)

# Weights. Cosine similarity is the main signal, but real embedding models squeeze it into a
# narrow band: with bge-small on a night of ~340 papers it ran 0.57-0.85 with a standard
# deviation of ~0.045 (smoke test, 2026-11-21). Boosts must be sized against that spread, not
# against 0..1, or a popular off-topic paper outranks every on-topic one. They are nudges that
# reorder close calls.
HF_UPVOTE_WEIGHT = 0.01  # x log1p(upvotes): 1 vote ~0.007, 42 votes ~0.038 (about one std of similarity)
AUTHOR_BOOST = 0.02  # per followed author on the paper: a clear nudge, about half a std
KEYWORD_BOOST = 0.01  # per keyword found in title/abstract: weak, keywords are blunt
KEYWORD_BOOST_CAP = 0.04  # many keywords must not swamp similarity
MY_WORK_WEIGHT = 0.0  # TODO(phase4): similarity to the user's own notes in my_work/
# Implicit profile (votes and saves). The term is FEEDBACK x max over your liked papers of
# (decay weight x cosine): the NEAREST neighbour only, so 20 diverse likes do not dilute the
# pull of the one that matches. A paper identical to one fresh like gains the full 0.20; one at
# similarity 0.6 (typical "unrelated") gains 0.12. The gap between a near neighbour and an
# average paper is 0.2 x ~0.2 = ~0.04, about one standard deviation of similarity: enough to
# reorder close calls, not enough to override a clear topical gap. Dislikes are slightly weaker
# (0.15) because a downvote is often "not today" rather than "never".
FEEDBACK_BOOST = 0.20
FEEDBACK_PENALTY = 0.15
# A "liked/disliked similar" reason is only written when the RAW cosine of the neighbour (before decay) reaches this floor:
# about two similarity standard deviations (0.045) above the 0.6 typical for unrelated papers.
FEEDBACK_REASON_FLOOR = 0.70


@dataclass
class ProfileVectors:
    seed_ids: list[str]
    seed_vecs: np.ndarray  # (len(seed_ids), d), may be empty
    topics: list[str]
    topic_vecs: np.ndarray  # (len(topics), d), may be empty


@dataclass
class FeedbackVectors:
    """Papers you voted on or saved: (paper id, embedding, decayed weight)."""

    liked: list[tuple[str, np.ndarray, float]] = field(default_factory=list)
    disliked: list[tuple[str, np.ndarray, float]] = field(default_factory=list)


@dataclass
class ScoredPaper:
    id: str
    score: float
    reasons: list[str]


def _age_days(stamp: str, now: datetime) -> float:
    """Days between a stored timestamp and `now`; accepts 'T'/space and 'Z'/offset/naive forms."""
    t = datetime.fromisoformat(str(stamp).strip().replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return max(0.0, (now - t).total_seconds() / 86400)


def build_feedback_vectors(
    votes,
    saves,
    embeddings_by_id: dict[str, np.ndarray],
    halflife_days: float,
    now: datetime,
) -> FeedbackVectors:
    """Turn current votes and saves into weighted vectors; weight = 0.5 ** (age / half-life).

    An up vote and a save both have base weight 1.0; a paper that is both counts once, at the
    fresher (larger) weight. A down vote wins over a save of the same paper. Papers with no
    embedding are skipped.
    """
    liked: dict[str, float] = {}
    disliked: dict[str, float] = {}

    def weight(stamp: str) -> float:
        return 0.5 ** (_age_days(stamp, now) / halflife_days)

    for r in votes:
        pid = r["paper_id"]
        if pid not in embeddings_by_id:
            continue
        (liked if r["vote"] > 0 else disliked)[pid] = weight(r["created_at"])
    for r in saves:
        pid = r["paper_id"]
        if pid in embeddings_by_id and pid not in disliked:
            liked[pid] = max(liked.get(pid, 0.0), weight(r["created_at"]))
    return FeedbackVectors(
        [(p, embeddings_by_id[p], w) for p, w in liked.items()],
        [(p, embeddings_by_id[p], w) for p, w in disliked.items()],
    )


def _feedback_term(
    vec: np.ndarray, group: list[tuple[str, np.ndarray, float]]
) -> tuple[float, str | None, float]:
    """(largest weight x cosine, id that gives it, that paper's raw cosine); empty -> (0, None, 0)."""
    if not group:
        return 0.0, None, 0.0
    best, best_id, raw = max((w * (c := float(v @ vec)), pid, c) for pid, v, w in group)
    return best, best_id, raw


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
    feedback: FeedbackVectors | None = None,
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
        if feedback:
            liked, liked_id, liked_raw = _feedback_term(vec, feedback.liked)
            disliked, disliked_id, disliked_raw = _feedback_term(vec, feedback.disliked)
            score += FEEDBACK_BOOST * liked - FEEDBACK_PENALTY * disliked
            if liked_id and liked_raw >= FEEDBACK_REASON_FLOOR:
                reasons.append(f"liked similar: {liked_id}")
            if disliked_id and disliked_raw >= FEEDBACK_REASON_FLOOR:
                reasons.append(f"disliked similar: {disliked_id}")
        out.append(ScoredPaper(row["id"], score, reasons))

    out.sort(key=lambda s: (-s.score, s.id))
    return out
