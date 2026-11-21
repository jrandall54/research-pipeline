import builtins
import logging

import numpy as np
import pytest

from research_pipeline.db import get_paper, open_db, upsert_paper
from research_pipeline.embed import SentenceTransformerEmbedder
from research_pipeline.models import PaperMeta, Profile
from research_pipeline.rank import ProfileVectors, build_profile_vectors, score_papers


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
    assert ranked[0].score - ranked[1].score == pytest.approx(0.05 * np.log1p(42))


def test_followed_author_boost(tmp_path, embedder):
    profile = Profile(topics=["robustness"], seeds=[], followed_authors=["Ada Lovelace"])
    a = meta("2501.00001", "Robustness study", authors=["Ada Lovelace", "Bob"])
    b = meta("2501.00002", "Robustness study", authors=["Ada Lovelace Jr"])
    ranked = score(tmp_path, embedder, [b, a], profile)
    assert ranked[0].id == "2501.00001"
    assert "followed author: Ada Lovelace" in ranked[0].reasons
    assert ranked[0].score - ranked[1].score == pytest.approx(0.1)


def test_followed_author_match_is_case_insensitive(tmp_path, embedder):
    profile = Profile(topics=[], seeds=[], followed_authors=["ada lovelace"])
    (s,) = score(tmp_path, embedder, [meta("2501.00001", "X", authors=["Ada Lovelace"])], profile)
    assert s.score == pytest.approx(0.1)


def test_keyword_boost_is_capped_and_counted_once(tmp_path, embedder):
    profile = Profile(topics=[], seeds=[], keywords=["alpha", "beta", "gamma", "delta", "eps"])
    p = meta("2501.00001", "Alpha alpha", "beta gamma delta eps")
    (s,) = score(tmp_path, embedder, [p], profile)
    assert s.score == pytest.approx(0.2)
    assert sum(r.startswith("keyword:") for r in s.reasons) == 5


def test_keyword_single_hit(tmp_path, embedder):
    profile = Profile(topics=[], seeds=[], keywords=["Fuzzing"])
    (s,) = score(tmp_path, embedder, [meta("2501.00001", "On fuzzing kernels")], profile)
    assert s.score == pytest.approx(0.05)
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
