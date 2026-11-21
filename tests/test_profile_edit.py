"""Validated, atomic, comment-preserving edits of profile.yaml and config.yaml."""

import os

import pytest

from research_pipeline import profile_edit as pe
from research_pipeline.vault import ConfigError, init_vault, load_config, load_profile


@pytest.fixture
def vault(tmp_path):
    init_vault(tmp_path / "v", topics=["agent security"])
    return tmp_path / "v"


def prof(vault):
    return load_profile(vault / "profile.yaml")


def test_default_template_has_no_placeholder_seeds(vault):
    assert prof(vault).seeds == []


def test_add_topic_appends_and_is_idempotent(vault):
    assert pe.add_topic(vault, "llm security") is True
    assert pe.add_topic(vault, "  LLM   Security ") is False
    assert prof(vault).topics == ["agent security", "llm security"]


def test_add_topic_rejects_empty(vault):
    with pytest.raises(ValueError):
        pe.add_topic(vault, "   ")


def test_remove_topic_by_number_and_by_value(vault):
    pe.add_topic(vault, "kernels")
    pe.add_topic(vault, "fuzzing")
    assert pe.remove_topic(vault, "2") is True
    assert prof(vault).topics == ["agent security", "fuzzing"]
    assert pe.remove_topic(vault, " FUZZING ") is True
    assert prof(vault).topics == ["agent security"]
    assert pe.remove_topic(vault, "nothing here") is False
    with pytest.raises(ValueError):
        pe.remove_topic(vault, "9")


def test_authors_and_keywords_round_trip(vault):
    assert pe.add_author(vault, "Jane  Example") is True
    assert pe.add_author(vault, "jane example") is False
    assert pe.add_keyword(vault, "prompt injection") is True
    assert pe.add_keyword(vault, "Prompt Injection") is False
    p = prof(vault)
    assert p.followed_authors == ["Jane Example"]
    assert p.keywords == ["prompt injection"]
    assert pe.remove_author(vault, "1") is True
    assert pe.remove_keyword(vault, "prompt injection") is True
    assert pe.remove_keyword(vault, "prompt injection") is False
    p = prof(vault)
    assert p.followed_authors == [] and p.keywords == []


def test_add_seed_normalises_version(vault):
    assert pe.add_seed_id(vault, "2307.15043v2") is True
    assert prof(vault).seeds == ["2307.15043"]
    assert pe.add_seed_id(vault, "2307.15043") is False


def test_seed_with_trailing_zero_stays_a_string(vault):
    pe.add_seed_id(vault, "2301.10000")
    pe.add_topic(vault, "another edit")  # re-reads and re-writes the file
    assert prof(vault).seeds == ["2301.10000"]
    assert '"2301.10000"' in (vault / "profile.yaml").read_text()


def test_unquoted_hand_written_seed_is_not_corrupted(vault):
    path = vault / "profile.yaml"
    path.write_text("topics: [a]\nseeds: [2301.10000]\n")
    pe.add_topic(vault, "b")
    assert prof(vault).seeds == ["2301.10000"]


def test_remove_seed_by_id_version_and_number(vault):
    pe.add_seed_id(vault, "2307.15043")
    pe.add_seed_id(vault, "1706.03762")
    assert pe.remove_seed(vault, "2307.15043v3") is True
    assert pe.remove_seed(vault, "1") is True
    assert prof(vault).seeds == []
    assert pe.remove_seed(vault, "1706.03762") is False


def test_normalise_helpers():
    assert pe.normalise_text("  A   b\tC ") == "A b C"
    assert pe.normalise_arxiv_id("2307.15043v12") == "2307.15043"
    assert pe.normalise_arxiv_id("arXiv:2307.15043") == "2307.15043"
    assert pe.normalise_arxiv_id("https://arxiv.org/abs/2307.15043v2") == "2307.15043"
    assert pe.normalise_arxiv_id("cs.CR/0701001v1") == "cs.CR/0701001"
    for bad in ["", "hello", "123.45", "2307.150431234"]:
        with pytest.raises(ValueError):
            pe.normalise_arxiv_id(bad)


def test_comments_survive_edit(vault):
    before = (vault / "profile.yaml").read_text()
    comments = [ln for ln in before.splitlines() if ln.startswith("#")]
    assert comments
    pe.add_topic(vault, "new one")
    pe.add_seed_id(vault, "2307.15043")
    pe.add_author(vault, "Jane Example")
    after = (vault / "profile.yaml").read_text()
    for line in comments:
        assert line in after


def test_hand_added_comment_survives(vault):
    path = vault / "profile.yaml"
    path.write_text(path.read_text() + "# my own note\n")
    pe.add_keyword(vault, "x")
    assert "# my own note" in path.read_text()


def test_invalid_hand_edit_refuses_and_leaves_file(vault):
    path = vault / "profile.yaml"
    path.write_text(path.read_text().replace("top_n: 15", "top_n: 0"))
    before = path.read_bytes()
    with pytest.raises(ConfigError, match="profile.yaml"):
        pe.add_topic(vault, "x")
    assert path.read_bytes() == before


def test_set_top_n(vault):
    pe.set_top_n(vault, 7)
    assert prof(vault).top_n == 7
    assert "# How many papers" in (vault / "profile.yaml").read_text()


def test_set_top_n_rejects_zero(vault):
    before = (vault / "profile.yaml").read_bytes()
    with pytest.raises(ConfigError, match="profile.yaml"):
        pe.set_top_n(vault, 0)
    assert (vault / "profile.yaml").read_bytes() == before


def test_set_categories_validates_format(vault):
    pe.set_categories(vault, ["cs.CR", "stat.ML", "hep-th", "math.OC"])
    assert load_config(vault / "config.yaml").arxiv_categories == [
        "cs.CR", "stat.ML", "hep-th", "math.OC"
    ]
    before = (vault / "config.yaml").read_bytes()
    for bad in (["CS-cr"], [""], []):
        with pytest.raises(ConfigError, match="config.yaml"):
            pe.set_categories(vault, bad)
    assert (vault / "config.yaml").read_bytes() == before
    assert "# arXiv categories" in before.decode()


def test_write_is_atomic(vault, monkeypatch):
    path = vault / "profile.yaml"
    before = path.read_bytes()

    def boom(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        pe.add_topic(vault, "x")
    assert path.read_bytes() == before
    assert sorted(p.name for p in vault.glob("*.yaml")) == ["config.yaml", "profile.yaml"]
    assert not list(vault.glob("*.tmp")) and not list(vault.glob(".*"))


HAND_WRITTEN = """# my profile
topics:
  - a topic  # why
  - another
seeds:
  - "2307.15043"
followed_authors: [A One, B Two]
keywords: []
top_n: 15
"""


def test_edit_keeps_untouched_list_indentation_and_comments(vault):
    path = vault / "profile.yaml"
    path.write_text(HAND_WRITTEN)
    pe.add_author(vault, "C Three")
    after = path.read_text()
    assert "topics:\n  - a topic  # why\n  - another\nseeds:\n  - \"2307.15043\"\n" in after
    assert "followed_authors: [A One, B Two, C Three]" in after


def test_edit_of_block_list_keeps_two_space_indent(vault):
    path = vault / "profile.yaml"
    path.write_text(HAND_WRITTEN)
    pe.add_topic(vault, "third")
    assert "topics:\n  - a topic  # why\n  - another\n  - third\n" in path.read_text()
