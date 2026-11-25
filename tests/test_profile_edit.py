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


def test_set_categories_all_normalised_and_exclusive(vault):
    pe.set_categories(vault, ["ALL"])
    assert load_config(vault / "config.yaml").arxiv_categories == ["all"]
    before = (vault / "config.yaml").read_bytes()
    for bad in (["all", "cs.CR"], ["cs.CR", "All"]):
        with pytest.raises(ConfigError, match="all"):
            pe.set_categories(vault, bad)
    assert (vault / "config.yaml").read_bytes() == before


def test_set_categories_error_points_at_examples_and_taxonomy(vault):
    with pytest.raises(ConfigError, match="category_taxonomy"):
        pe.set_categories(vault, ["CS-cr"])


def test_set_categories_accepts_hyphenated_subcategories_rejects_uppercase_prefix(vault):
    pe.set_categories(vault, ["physics.comp-ph", "cond-mat.mes-hall", "math-ph"])
    assert load_config(vault / "config.yaml").arxiv_categories == [
        "physics.comp-ph", "cond-mat.mes-hall", "math-ph"
    ]
    for bad in (["CS.CR"], ["cs."], ["cs_CR"]):
        with pytest.raises(ConfigError):
            pe.set_categories(vault, bad)


def test_set_llm_model_auto_is_stored_lowercase(vault):
    for spelling in ("Auto", "AUTO", " auto "):
        pe.set_llm_model(vault, "qwen3:14b")
        pe.set_llm_model(vault, spelling)
        assert load_config(vault / "config.yaml").llm_model == "auto"


def test_normalise_text_strips_c1_and_bidi_controls_but_keeps_scripts():
    assert pe.normalise_text("a\x85b\x9bc") == "a b c"
    assert pe.normalise_text("x‮evil‬ ⁦y⁩ ‎‏؜z") == "xevil y z"
    for text in ("أمن الذكاء الاصطناعي", "אבטחת מידע", "大模型安全", "Ünïcode café"):
        assert pe.normalise_text(text) == text


def test_add_topic_strips_bidi_override(vault):
    assert pe.add_topic(vault, "safe‮topic") is True
    assert "safetopic" in prof(vault).topics


def test_normalise_text_keeps_zwj_and_zwnj():
    # Deliberately kept: needed for Persian, Indic scripts and emoji sequences.
    for text in ("می\u200cخواهم", "क्\u200dष", "👩\u200d💻"):
        assert pe.normalise_text(text) == text


def test_add_author_and_keyword_strip_control_and_bidi(vault):
    assert pe.add_author(vault, "Ev\u202eil\x1b Name\x85") is True
    assert pe.add_keyword(vault, "jail\u202ebreak\x07 attack") is True
    p = prof(vault)
    assert "Evil Name" in p.followed_authors
    assert "jailbreak attack" in p.keywords


def test_enable_and_disable_source(vault):
    assert pe.disable_source(vault, "hf_daily") is True
    assert load_config(vault / "config.yaml").sources == ["arxiv"]
    assert pe.disable_source(vault, "hf_daily") is False
    assert pe.enable_source(vault, "hf_daily", {"arxiv", "hf_daily"}) is True
    assert load_config(vault / "config.yaml").sources == ["arxiv", "hf_daily"]
    assert pe.enable_source(vault, "hf_daily", {"arxiv", "hf_daily"}) is False


def test_disable_last_source_refused(vault):
    pe.disable_source(vault, "hf_daily")
    with pytest.raises(ValueError, match="at least one source must stay enabled"):
        pe.disable_source(vault, "arxiv")


def test_enable_unknown_source_lists_known(vault):
    with pytest.raises(ValueError, match="arxiv, hf_daily"):
        pe.enable_source(vault, "nope", {"arxiv", "hf_daily"})


def cfg(vault):
    return load_config(vault / "config.yaml")


def test_set_arxiv_max_results_round_trips_and_keeps_comments(vault):
    pe.set_arxiv_max_results(vault, 250)
    assert cfg(vault).arxiv_max_results == 250
    assert "# Most papers to fetch from arXiv per run" in (vault / "config.yaml").read_text()


@pytest.mark.parametrize("bad", [10, 49, 5001, 0, -5])
def test_set_arxiv_max_results_rejects_out_of_range(vault, bad):
    before = (vault / "config.yaml").read_bytes()
    with pytest.raises(ValueError, match="between 50 and 5000"):
        pe.set_arxiv_max_results(vault, bad)
    assert (vault / "config.yaml").read_bytes() == before


def test_set_arxiv_max_results_accepts_bounds(vault):
    pe.set_arxiv_max_results(vault, 50)
    pe.set_arxiv_max_results(vault, 5000)
    assert cfg(vault).arxiv_max_results == 5000


def test_set_embedding_model_round_trips_and_keeps_comments(vault):
    pe.set_embedding_model(vault, "  all-MiniLM-L6-v2 ")
    assert cfg(vault).embedding_model == "all-MiniLM-L6-v2"
    assert "# Sentence-embedding model" in (vault / "config.yaml").read_text()


@pytest.mark.parametrize("bad", ["", "   "])
def test_set_embedding_model_rejects_empty(vault, bad):
    before = (vault / "config.yaml").read_bytes()
    with pytest.raises(ValueError, match="empty"):
        pe.set_embedding_model(vault, bad)
    assert (vault / "config.yaml").read_bytes() == before


def test_setter_on_old_config_with_rss_feeds_and_no_max_results(vault):
    path = vault / "config.yaml"
    path.write_text(f"# my note\nvault_path: {vault}\nembedding_model: x/y\nrss_feeds: []\n")
    pe.set_arxiv_max_results(vault, 200)
    text = path.read_text()
    assert "# my note" in text and "embedding_model: x/y" in text and "rss_feeds: []" in text
    c = cfg(vault)
    assert c.arxiv_max_results == 200 and c.embedding_model == "x/y"


def test_set_night_deadline_normalises(vault):
    pe.set_night_deadline(vault, "7:00")
    assert load_config(vault / "config.yaml").night_deadline == "07:00"
    pe.set_night_deadline(vault, "OFF")
    assert load_config(vault / "config.yaml").night_deadline == "off"
    pe.set_night_deadline(vault, " 23:15 ")
    assert load_config(vault / "config.yaml").night_deadline == "23:15"
    for bad in ("25:00", "7pm", "", "12:60"):
        with pytest.raises(ValueError, match="HH:MM"):
            pe.set_night_deadline(vault, bad)
    assert load_config(vault / "config.yaml").night_deadline == "23:15"


def test_night_deadline_default_and_stored_quoted(vault):
    assert load_config(vault / "config.yaml").night_deadline == "07:00"
    pe.set_night_deadline(vault, "9:05")
    # Quoted, so YAML never reads it as a number of minutes (sexagesimal).
    assert 'night_deadline: "09:05"' in (vault / "config.yaml").read_text()
