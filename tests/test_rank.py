import builtins
import logging
from datetime import UTC, datetime

import numpy as np
import pytest

from research_pipeline.db import get_paper, open_db, upsert_paper
from research_pipeline.embed import SentenceTransformerEmbedder
from research_pipeline.library import current_votes, record_vote
from research_pipeline.models import PaperMeta, Profile
from research_pipeline.rank import (
    FEEDBACK_BOOST,
    FEEDBACK_PENALTY,
    FEEDBACK_REASON_FLOOR,
    SHELF_BOOST,
    SHELF_REASON_FLOOR,
    FeedbackVectors,
    ProfileVectors,
    ShelfVectors,
    build_feedback_vectors,
    build_profile_vectors,
    score_papers,
)


def make_rows(tmp_path, papers):
    conn = open_db(tmp_path / "r.db")
    for p in papers:
        upsert_paper(conn, p)
    return [get_paper(conn, p.id) for p in papers]


def meta(pid, title, abstract="", **kw):
    return PaperMeta(id=pid, title=title, abstract=abstract, source="arxiv",
                     url=f"https://arxiv.org/abs/{pid}", **kw)


def score(tmp_path, embedder, papers, profile, seed_texts=None):
    rows = make_rows(tmp_path, papers)
    vecs = embedder.embed([f"{p.title} {p.abstract}" for p in papers])
    pv = build_profile_vectors(embedder, profile, seed_texts or {})
    return score_papers(rows, vecs, profile, pv)


def test_on_topic_paper_outranks_off_topic(tmp_path, embedder):
    profile = Profile(topics=[], seeds=["s1"])
    seeds = {"s1": "adversarial robustness of neural networks"}
    on = meta("2501.00001", "Adversarial robustness of neural networks")
    off = meta("2501.00002", "Cooking pasta with tomatoes")
    ranked = score(tmp_path, embedder, [off, on], profile, seeds)
    assert [s.id for s in ranked] == ["2501.00001", "2501.00002"]
    assert ranked[0].score > ranked[1].score


def test_reasons_name_the_driving_seed(tmp_path, embedder):
    profile = Profile(topics=["cooking"], seeds=["s1", "s2"])
    seeds = {"s1": "adversarial robustness", "s2": "kernel scheduling"}
    p = meta("2501.00001", "Adversarial robustness")
    (s,) = score(tmp_path, embedder, [p], profile, seeds)
    assert s.reasons[0].startswith("closest seed: s1 (1.00)")


def test_reasons_name_topic_when_topic_is_best(tmp_path, embedder):
    profile = Profile(topics=["adversarial robustness"], seeds=[])
    (s,) = score(tmp_path, embedder, [meta("2501.00001", "Adversarial robustness")], profile)
    assert s.reasons[0] == "topic: adversarial robustness (1.00)"


def test_hf_upvotes_boost_breaks_tie(tmp_path, embedder):
    profile = Profile(topics=["robustness"], seeds=[])
    a = meta("2501.00001", "Robustness study", hf_upvotes=0)
    b = meta("2501.00002", "Robustness study", hf_upvotes=42)
    ranked = score(tmp_path, embedder, [a, b], profile)
    assert ranked[0].id == "2501.00002"
    assert "HF upvotes: 42" in ranked[0].reasons
    assert ranked[0].score - ranked[1].score == pytest.approx(0.01 * np.log1p(42))


def test_followed_author_boost(tmp_path, embedder):
    profile = Profile(topics=["robustness"], seeds=[], followed_authors=["Ada Lovelace"])
    a = meta("2501.00001", "Robustness study", authors=["Ada Lovelace", "Bob"])
    b = meta("2501.00002", "Robustness study", authors=["Ada Lovelace Jr"])
    ranked = score(tmp_path, embedder, [b, a], profile)
    assert ranked[0].id == "2501.00001"
    assert "followed author: Ada Lovelace" in ranked[0].reasons
    assert ranked[0].score - ranked[1].score == pytest.approx(0.02)


def test_followed_author_match_is_case_insensitive(tmp_path, embedder):
    profile = Profile(topics=[], seeds=[], followed_authors=["ada lovelace"])
    (s,) = score(tmp_path, embedder, [meta("2501.00001", "X", authors=["Ada Lovelace"])], profile)
    assert s.score == pytest.approx(0.02)


def test_keyword_boost_is_capped_and_counted_once(tmp_path, embedder):
    profile = Profile(topics=[], seeds=[], keywords=["alpha", "beta", "gamma", "delta", "eps"])
    p = meta("2501.00001", "Alpha alpha", "beta gamma delta eps")
    (s,) = score(tmp_path, embedder, [p], profile)
    assert s.score == pytest.approx(0.04)
    assert sum(r.startswith("keyword:") for r in s.reasons) == 5


def test_keyword_single_hit(tmp_path, embedder):
    profile = Profile(topics=[], seeds=[], keywords=["Fuzzing"])
    (s,) = score(tmp_path, embedder, [meta("2501.00001", "On fuzzing kernels")], profile)
    assert s.score == pytest.approx(0.01)
    assert s.reasons == ["keyword: Fuzzing"]


def test_zero_seeds_uses_topics_only(tmp_path, embedder):
    profile = Profile(topics=["adversarial robustness"], seeds=[])
    on = meta("2501.00001", "Adversarial robustness")
    off = meta("2501.00002", "Cooking pasta")
    ranked = score(tmp_path, embedder, [off, on], profile)
    assert ranked[0].id == "2501.00001"


def test_zero_seeds_and_zero_topics_scores_by_boosts_only_without_error(tmp_path, embedder):
    profile = Profile(topics=[], seeds=[], keywords=["kernel"])
    a = meta("2501.00001", "Kernel things")
    b = meta("2501.00002", "Other things")
    ranked = score(tmp_path, embedder, [b, a], profile)
    assert [s.id for s in ranked] == ["2501.00001", "2501.00002"]
    assert ranked[1].score == 0.0
    assert ranked[1].reasons == []


def test_ties_break_by_id_ascending(tmp_path, embedder):
    profile = Profile(topics=[], seeds=[])
    papers = [meta("2501.00003", "a"), meta("2501.00001", "b"), meta("2501.00002", "c")]
    ranked = score(tmp_path, embedder, papers, profile)
    assert [s.id for s in ranked] == ["2501.00001", "2501.00002", "2501.00003"]


def test_missing_seed_text_is_skipped_with_warning(embedder, caplog):
    profile = Profile(topics=[], seeds=["s1", "ghost"])
    with caplog.at_level(logging.WARNING):
        pv = build_profile_vectors(embedder, profile, {"s1": "text"})
    assert pv.seed_ids == ["s1"]
    assert "ghost" in caplog.text


def test_empty_profile_vectors_allowed(embedder):
    pv = build_profile_vectors(embedder, Profile(topics=[], seeds=[]), {})
    assert isinstance(pv, ProfileVectors)
    assert len(pv.seed_ids) == 0 and len(pv.topics) == 0


def test_missing_sentence_transformers_gives_install_hint(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name.startswith("sentence_transformers"):
            raise ImportError("nope")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="uv sync --extra embed"):
        SentenceTransformerEmbedder()


def test_popular_paper_cannot_beat_a_clearly_closer_one_at_realistic_similarity_spread(tmp_path):
    """Real models bunch cosine similarity in ~0.6-0.85; a 42-upvote boost must not bridge 0.1."""
    topic = np.array([1.0, 0.0], dtype=np.float32)

    def vec(cos):
        return np.array([cos, np.sqrt(1 - cos**2)], dtype=np.float32)

    profile = Profile(topics=["t"], seeds=[])
    pv = ProfileVectors([], np.zeros((0, 0)), ["t"], topic[None, :])
    rows = make_rows(tmp_path, [meta("2501.00001", "close"), meta("2501.00002", "viral", hf_upvotes=42)])
    ranked = score_papers(rows, np.stack([vec(0.85), vec(0.75)]), profile, pv)
    assert ranked[0].id == "2501.00001"


# ---------------------------------------------------------------- implicit profile (feedback)

NOW = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)
EMPTY_PV = ProfileVectors([], np.zeros((0, 0)), [], np.zeros((0, 0)))
E1, E2 = np.array([1.0, 0.0]), np.array([0.0, 1.0])


def fb_score(tmp_path, feedback, vecs=(E1, E2)):
    sub = tmp_path / f"db{len(list(tmp_path.iterdir()))}"
    sub.mkdir()
    rows = make_rows(sub, [meta("2501.00001", "Alpha"), meta("2501.00002", "Beta")])
    return {s.id: s for s in score_papers(rows, np.array(vecs), Profile(topics=[], seeds=[]),
                                          EMPTY_PV, feedback)}


def row(pid, vote, created="2026-11-21T12:00:00+00:00"):
    return {"paper_id": pid, "vote": vote, "created_at": created}


def test_liked_neighbour_scores_higher(tmp_path):
    fv = build_feedback_vectors([row("L", 1)], [], {"L": E1}, 60.0, NOW)
    got = fb_score(tmp_path, fv)
    assert got["2501.00001"].score > got["2501.00002"].score
    assert "liked similar: L" in got["2501.00001"].reasons


def test_disliked_neighbour_scores_lower(tmp_path):
    fv = build_feedback_vectors([row("D", -1)], [], {"D": E1}, 60.0, NOW)
    got = fb_score(tmp_path, fv)
    assert got["2501.00001"].score < got["2501.00002"].score
    assert "disliked similar: D" in got["2501.00001"].reasons


def test_old_vote_decays(tmp_path):
    base = fb_score(tmp_path, None)["2501.00001"].score
    fresh = build_feedback_vectors([row("L", 1)], [], {"L": E1}, 60.0, NOW)
    old = build_feedback_vectors([row("L", 1, "2026-09-22T12:00:00+00:00")], [], {"L": E1}, 60.0, NOW)
    assert old.liked[0][2] == pytest.approx(0.5 ** (60 / 60.0))  # 60 days = 1 half-life
    gain_fresh = fb_score(tmp_path, fresh)["2501.00001"].score - base
    gain_old = fb_score(tmp_path, old)["2501.00001"].score - base
    assert gain_fresh == pytest.approx(FEEDBACK_BOOST * 1.0, abs=1e-9)
    assert abs(gain_old / gain_fresh - 0.5) < 1e-9


@pytest.mark.parametrize("stamp", ["2026-11-21 12:00:00", "2026-11-21T12:00:00Z", "2026-11-21T12:00:00+00:00"])
def test_timestamp_forms_tolerated(stamp):
    fv = build_feedback_vectors([row("L", 1, stamp)], [], {"L": E1}, 60.0, NOW)
    assert fv.liked[0][2] == pytest.approx(1.0)


def test_vote_without_embedding_ignored(tmp_path):
    fv = build_feedback_vectors([row("X", 1), row("Y", -1)], [row("X", 1)], {}, 60.0, NOW)
    assert fv.liked == [] and fv.disliked == []
    assert fb_score(tmp_path, fv)["2501.00001"].reasons == []


def test_upvote_and_save_count_once_with_max_weight():
    saves = [{"paper_id": "L", "created_at": "2026-11-21T12:00:00+00:00"}]
    fv = build_feedback_vectors([row("L", 1, "2026-09-22T12:00:00+00:00")], saves, {"L": E1}, 60.0, NOW)
    assert len(fv.liked) == 1 and fv.liked[0][2] == pytest.approx(1.0)


def test_save_alone_is_liked():
    saves = [{"paper_id": "S", "created_at": "2026-11-21T12:00:00+00:00"}]
    fv = build_feedback_vectors([], saves, {"S": E1}, 60.0, NOW)
    assert [x[0] for x in fv.liked] == ["S"]


def test_no_feedback_scores_unchanged(tmp_path):
    a = fb_score(tmp_path, None)
    b = fb_score(tmp_path, FeedbackVectors([], []))
    assert {k: v.score for k, v in a.items()} == {k: v.score for k, v in b.items()}
    assert all(v.reasons == [] for v in a.values())


def test_up_then_clear_contributes_nothing(tmp_path):
    conn = open_db(tmp_path / "v.db")
    upsert_paper(conn, meta("L", "Liked"))
    record_vote(conn, "L", 1)
    record_vote(conn, "L", 0)
    fv = build_feedback_vectors(current_votes(conn), [], {"L": E1}, 60.0, NOW)
    assert fv.liked == [] and fv.disliked == []


def test_down_vote_beats_save_of_same_paper():
    saves = [{"paper_id": "P", "created_at": "2026-11-21T12:00:00+00:00"}]
    fv = build_feedback_vectors([row("P", -1)], saves, {"P": E1}, 60.0, NOW)
    assert fv.liked == [] and [x[0] for x in fv.disliked] == ["P"]


def test_many_diverse_likes_do_not_dilute(tmp_path):
    rng = np.random.default_rng(0)
    others = []
    for _ in range(19):
        v = rng.normal(size=8)
        v[0] = 0.0
        others.append(v / np.linalg.norm(v))
    dim = lambda v: np.concatenate([v, np.zeros(8 - len(v))])  # noqa: E731
    near = dim(E1)
    one = build_feedback_vectors([row("L", 1)], [], {"L": near}, 60.0, NOW)
    emb = {"L": near} | {f"o{i}": v for i, v in enumerate(others)}
    votes = [row("L", 1)] + [row(f"o{i}", 1) for i in range(19)]
    many = build_feedback_vectors(votes, [], emb, 60.0, NOW)
    rows = make_rows(tmp_path, [meta("2501.00001", "Alpha")])
    args = (rows, np.array([near]), Profile(topics=[], seeds=[]), EMPTY_PV)
    base = score_papers(*args)[0].score
    g1 = score_papers(*args, one)[0].score - base
    g20 = score_papers(*args, many)[0].score - base
    assert g20 == pytest.approx(g1, abs=1e-9) and g20 == pytest.approx(FEEDBACK_BOOST)


def test_reason_floor_both_sides(tmp_path):
    def reasons(cos):
        v = np.array([cos, (1 - cos**2) ** 0.5])
        fv = build_feedback_vectors([row("L", 1)], [], {"L": v}, 60.0, NOW)
        return fb_score(tmp_path, fv, vecs=(E1, E2))["2501.00001"].reasons

    assert reasons(FEEDBACK_REASON_FLOOR + 0.01) == ["liked similar: L"]
    assert reasons(FEEDBACK_REASON_FLOOR - 0.01) == []


def test_old_dislike_still_gets_reason_and_decayed_penalty(tmp_path):
    v = np.array([0.9, (1 - 0.81) ** 0.5])
    old = build_feedback_vectors([row("D", -1, "2026-07-24T12:00:00+00:00")], [], {"D": v}, 60.0, NOW)
    base = fb_score(tmp_path, None)["2501.00001"].score
    got = fb_score(tmp_path, old)["2501.00001"]
    assert old.disliked[0][2] == pytest.approx(0.25, abs=1e-3)  # 120 days = 2 half-lives
    assert "disliked similar: D" in got.reasons  # raw 0.9 is above the floor, however old
    assert got.score - base == pytest.approx(-FEEDBACK_PENALTY * old.disliked[0][2] * 0.9, abs=1e-9)
    # Below the floor the reason is hidden even though the (small) penalty still applies.
    far = np.array([0.6, 0.8])
    weak = build_feedback_vectors([row("D", -1, "2026-07-24T12:00:00+00:00")], [], {"D": far}, 60.0, NOW)
    assert fb_score(tmp_path, weak)["2501.00001"].reasons == []


def test_old_like_still_gets_reason_and_decayed_score(tmp_path):
    v = np.array([0.9, (1 - 0.81) ** 0.5])
    old = build_feedback_vectors([row("L", 1, "2026-07-24T12:00:00+00:00")], [], {"L": v}, 60.0, NOW)
    base = fb_score(tmp_path, None)["2501.00001"].score
    got = fb_score(tmp_path, old)["2501.00001"]
    assert old.liked[0][2] == pytest.approx(0.25, abs=1e-3)  # 120 days = 2 half-lives
    assert "liked similar: L" in got.reasons
    assert got.score - base == pytest.approx(0.2 * old.liked[0][2] * 0.9, abs=1e-9)


def test_negative_cosine_never_flips_the_feedback_sign(tmp_path):
    opposite = np.array([-1.0, 0.0])
    liked = fb_score(tmp_path, build_feedback_vectors([row("L", 1)], [], {"L": E1}, 60.0, NOW),
                     vecs=(opposite, E2))
    disliked = fb_score(tmp_path, build_feedback_vectors([row("D", -1)], [], {"D": E1}, 60.0, NOW),
                        vecs=(opposite, E2))
    # Opposite to a liked paper: no penalty. Opposite to a disliked paper: no boost.
    assert liked["2501.00001"].score == liked["2501.00002"].score
    assert disliked["2501.00001"].score == disliked["2501.00002"].score


# ---- shelf term ----


def shelf_score(tmp_path, shelf, vecs=(E1, E2)):
    sub = tmp_path / f"sdb{len(list(tmp_path.iterdir()))}"
    sub.mkdir()
    rows = make_rows(sub, [meta("2501.00001", "Alpha"), meta("2501.00002", "Beta")])
    return {s.id: s for s in score_papers(rows, np.array(vecs), Profile(topics=[], seeds=[]),
                                          EMPTY_PV, shelf=shelf)}


def test_empty_or_missing_shelf_changes_nothing(tmp_path):
    base = shelf_score(tmp_path, None)
    empty = shelf_score(tmp_path, ShelfVectors())
    for pid in base:
        assert empty[pid].score == base[pid].score and empty[pid].reasons == base[pid].reasons


def test_shelf_neighbour_boosts_and_is_named(tmp_path):
    base = shelf_score(tmp_path, None)
    got = shelf_score(tmp_path, ShelfVectors(["idea"], np.array([E1])))
    assert got["2501.00001"].score - base["2501.00001"].score == pytest.approx(SHELF_BOOST)
    assert got["2501.00002"].score == base["2501.00002"].score  # orthogonal: nothing
    assert "near your shelf: idea" in got["2501.00001"].reasons
    assert not any("shelf" in r for r in got["2501.00002"].reasons)


def test_shelf_uses_nearest_chunk_only(tmp_path):
    one = shelf_score(tmp_path, ShelfVectors(["idea"], np.array([E1])))
    many = shelf_score(tmp_path, ShelfVectors(["idea", "other", "more"], np.array([E1, E2, E2])))
    assert many["2501.00001"].score == one["2501.00001"].score


def test_shelf_negative_cosine_adds_nothing(tmp_path):
    base = shelf_score(tmp_path, None)
    got = shelf_score(tmp_path, ShelfVectors(["idea"], np.array([-E1])))
    assert got["2501.00001"].score == base["2501.00001"].score
    assert not any("shelf" in r for r in got["2501.00001"].reasons)


def test_shelf_reason_needs_the_floor(tmp_path):
    just_below = np.array([SHELF_REASON_FLOOR - 0.01, np.sqrt(1 - (SHELF_REASON_FLOOR - 0.01) ** 2)])
    got = shelf_score(tmp_path, ShelfVectors(["idea"], np.array([just_below])))
    assert got["2501.00001"].score > shelf_score(tmp_path, None)["2501.00001"].score
    assert not any("shelf" in r for r in got["2501.00001"].reasons)
