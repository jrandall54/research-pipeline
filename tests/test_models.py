import pytest
from pydantic import ValidationError

from research_pipeline.models import Config, PaperMeta, Profile, Status


def test_paper_meta_defaults():
    p = PaperMeta(id="2401.00001", title="T", source="arxiv", url="http://x")
    assert p.abstract == ""
    assert p.version == 1
    assert p.authors == [] and p.repos == []
    assert p.hf_upvotes == 0 and p.published is None


def test_status_values_match_spec():
    assert {s.value for s in Status} == {
        "new", "triaged", "fetched", "summarized", "in_digest", "skipped", "failed",
    }


def test_profile_top_n_default_is_15():
    assert Profile(topics=["a"], seeds=[]).top_n == 15


def test_profile_rejects_top_n_below_1():
    with pytest.raises(ValidationError):
        Profile(topics=["a"], seeds=[], top_n=0)


def test_config_defaults():
    from research_pipeline.models import Config

    c = Config(vault_path="/tmp/v")
    assert c.arxiv_categories == ["cs.LG", "cs.CL", "cs.CR", "cs.OS", "cs.SE"]
    assert c.embedding_model == "BAAI/bge-small-en-v1.5"
    with pytest.raises(ValidationError):
        Config()


def test_config_default_arxiv_max_results_and_floor():
    from research_pipeline.models import Config

    assert Config(vault_path="/v").arxiv_max_results == 1000
    with pytest.raises(ValidationError):
        Config(vault_path="/v", arxiv_max_results=0)


def test_config_accepts_all_but_not_mixed_or_bad_categories():
    from research_pipeline.models import Config

    assert Config(vault_path="/v", arxiv_categories=["all"]).arxiv_categories == ["all"]
    assert Config(vault_path="/v", arxiv_categories=["cs.CR", "hep-th"]).arxiv_categories == ["cs.CR", "hep-th"]
    for bad in (["all", "cs.CR"], ["ALL"], ["CS-cr"], []):
        with pytest.raises(ValidationError):
            Config(vault_path="/v", arxiv_categories=bad)


@pytest.mark.parametrize("good", ["physics.comp-ph", "cond-mat.stat-mech", "cs.CR", "stat.ML", "math-ph", "hep-th"])
def test_config_accepts_real_arxiv_categories(good):
    from research_pipeline.models import Config

    assert Config(vault_path="/v", arxiv_categories=[good]).arxiv_categories == [good]


@pytest.mark.parametrize("bad", ["", "cs.", ".CR", "CS.CR", "Cs.CR", "CS-cr", "cs_CR", "cs CR", "123", "cs.C R"])
def test_config_rejects_malformed_categories(bad):
    from research_pipeline.models import Config

    with pytest.raises(ValidationError):
        Config(vault_path="/v", arxiv_categories=[bad])


def test_config_without_new_keys_loads_with_defaults(tmp_path):  # old vault
    cfg = Config(vault_path=tmp_path)
    assert (cfg.llm_backend, cfg.llm_cli_path, cfg.sources) == ("ollama", None, ["arxiv", "hf_daily"])


@pytest.mark.parametrize("bad", [[], ["arxiv", "arxiv"], ["Bad Name"]])
def test_sources_must_be_nonempty_unique_and_named_sanely(bad):
    with pytest.raises(ValidationError):
        Config(vault_path="/v", sources=bad)


def test_llm_backend_name_pattern():
    with pytest.raises(ValidationError):
        Config(vault_path="/v", llm_backend="Not Ok")


def test_llm_effort_defaults_to_low_and_old_config_lacks_key(tmp_path):
    from research_pipeline.models import Config
    assert Config(vault_path=tmp_path).llm_effort == "low"


@pytest.mark.parametrize("value", ["low", "medium", "high"])
def test_llm_effort_accepts_allowed(tmp_path, value):
    from research_pipeline.models import Config
    assert Config(vault_path=tmp_path, llm_effort=value).llm_effort == value


@pytest.mark.parametrize("value", ["max", "xhigh", "", "LOW", "fast"])
def test_llm_effort_rejects_others(tmp_path, value):
    from research_pipeline.models import Config
    with pytest.raises(ValidationError):
        Config(vault_path=tmp_path, llm_effort=value)


def test_config_night_deadline_minutes_int_becomes_hh_mm():
    # PyYAML reads unquoted 23:00 as 1380 (base 60); it must still load.
    assert Config(vault_path="/v", night_deadline=1380).night_deadline == "23:00"
    assert Config(vault_path="/v", night_deadline=420).night_deadline == "07:00"
    assert Config(vault_path="/v", night_deadline=0).night_deadline == "00:00"
    for bad in (1440, -1, "7pm", "24:00"):
        with pytest.raises(ValidationError):
            Config(vault_path="/v", night_deadline=bad)
    with pytest.raises(ValidationError):
        Config(vault_path="/v", night_deadline=True)
