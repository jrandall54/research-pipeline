import pytest
from pydantic import ValidationError

from research_pipeline.models import PaperMeta, Profile, Status


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
