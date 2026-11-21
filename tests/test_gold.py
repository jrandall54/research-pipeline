import os

import pytest
from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.gold import GOLD_TARGET, GoldPaper, add_fact, add_gold, load_gold, remove_gold
from research_pipeline.sources.base import SourceError
from research_pipeline.vault import ConfigError, init_vault
from test_cli_profile import StubArxiv, paper

runner = CliRunner()


@pytest.fixture
def vault(tmp_path):
    path = tmp_path / "vault"
    init_vault(path, topics=["LLM security"])
    return path


def gp(pid="2307.15043", facts=("a fact",)):
    return GoldPaper(paper_id=pid, title="T", key_facts=list(facts))


def test_load_gold_empty_when_no_file(vault):
    assert load_gold(vault) == []
    assert GOLD_TARGET == 10


def test_add_list_remove_roundtrip(vault):
    assert add_gold(vault, gp()) is True
    assert load_gold(vault) == [gp()]
    assert add_fact(vault, "2307.15043", "second") is True
    assert load_gold(vault)[0].key_facts == ["a fact", "second"]
    assert add_fact(vault, "9999.99999", "x") is False
    assert remove_gold(vault, "2307.15043") is True
    assert remove_gold(vault, "2307.15043") is False
    assert load_gold(vault) == []


def test_add_duplicate_in_another_spelling_returns_false(vault):
    assert add_gold(vault, gp("2307.15043")) is True
    assert add_gold(vault, gp("2307.15043v2")) is False
    assert len(load_gold(vault)) == 1
    assert remove_gold(vault, "arxiv:2307.15043v3") is True


def test_corrupt_gold_file_raises_configerror_naming_file(vault):
    (vault / "gold" / "gold.json").write_text("{not json")
    with pytest.raises(ConfigError, match="gold.json"):
        load_gold(vault)
    (vault / "gold" / "gold.json").write_text('{"a": 1}')
    with pytest.raises(ConfigError, match="gold.json"):
        load_gold(vault)


def test_atomic_write_leaves_no_temp_files(vault):
    add_gold(vault, gp())
    add_fact(vault, "2307.15043", "more")
    assert os.listdir(vault / "gold") == ["gold.json"]


def test_add_gold_creates_folder_lazily(tmp_path):
    add_gold(tmp_path / "v", gp())
    assert (tmp_path / "v" / "gold" / "gold.json").exists()


# ---- CLI

def grun(vault, *args, input=None):
    return runner.invoke(cli.app, ["gold", *args, "--vault", str(vault)], input=input)


def use(monkeypatch, stub):
    monkeypatch.setattr(cli, "make_arxiv", lambda config: stub)
    return stub


def test_cli_add_saves_paper_with_facts(vault, monkeypatch):
    use(monkeypatch, StubArxiv(by_id=[paper()]))
    res = grun(vault, "add", "2307.15043", input="y\nfact one\nfact two\n\n")
    assert res.exit_code == 0, res.output
    g = load_gold(vault)
    assert [(x.paper_id, x.key_facts) for x in g] == [("2307.15043", ["fact one", "fact two"])]
    assert g[0].title == "Universal and Transferable Adversarial Attacks"


def test_cli_add_zero_facts_saves_nothing(vault, monkeypatch):
    use(monkeypatch, StubArxiv(by_id=[paper()]))
    res = grun(vault, "add", "2307.15043", input="y\n\n")
    assert "At least one" in res.output
    assert load_gold(vault) == []
    assert not (vault / "gold" / "gold.json").exists()


def test_cli_add_eof_mid_facts_saves_nothing(vault, monkeypatch):
    use(monkeypatch, StubArxiv(by_id=[paper()]))
    res = grun(vault, "add", "2307.15043", input="y\nfact one\n")
    assert res.exit_code != 0
    assert load_gold(vault) == []


def test_cli_add_arxiv_failure_exit_1_unchanged(vault, monkeypatch):
    use(monkeypatch, StubArxiv(error=SourceError("HTTP 503")))
    res = grun(vault, "add", "2307.15043")
    assert res.exit_code == 1
    assert "HTTP 503" in res.output
    assert not (vault / "gold" / "gold.json").exists()


def test_cli_add_already_present(vault, monkeypatch):
    add_gold(vault, gp())
    stub = use(monkeypatch, StubArxiv(by_id=[paper()]))
    res = grun(vault, "add", "2307.15043v2")
    assert "already" in res.output
    assert stub.calls == []


def test_cli_list_remove_add_fact(vault):
    assert "0/10" in grun(vault, "list").output
    add_gold(vault, gp())
    out = grun(vault, "list").output
    assert "1/10" in out and "a fact" in out
    assert grun(vault, "add-fact", "2307.15043", "another").exit_code == 0
    assert "another" in grun(vault, "list").output
    assert grun(vault, "add-fact", "1111.1111", "x").exit_code == 1
    assert grun(vault, "remove", "2307.15043").exit_code == 0
    assert grun(vault, "remove", "2307.15043").exit_code == 1
    assert "0/10" in grun(vault, "list").output


def test_cli_corrupt_file_clean_error(vault):
    (vault / "gold" / "gold.json").write_text("nope")
    res = grun(vault, "list")
    assert res.exit_code == 1 and "gold.json" in res.output
