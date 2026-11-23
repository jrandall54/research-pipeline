import os

import pytest
from conftest import critique, summary
from fakes import StubArxiv, paper
from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.db import open_db, save_fulltext, save_summary, upsert_paper
from research_pipeline.gold import (
    GOLD_TARGET,
    GoldPaper,
    add_fact,
    add_gold,
    edit_fact,
    load_gold,
    remove_fact,
    remove_gold,
    suggest_papers,
)
from research_pipeline.models import Section
from research_pipeline.sources.base import SourceError
from research_pipeline.summary_models import PaperSummary
from research_pipeline.vault import ConfigError, init_vault

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
    res = grun(vault, "add-fact", "2307.15043", "another")
    assert res.exit_code == 0 and "Added a fact to 2307.15043." in res.output
    assert "another" in grun(vault, "list").output
    assert grun(vault, "add-fact", "1111.1111", "x").exit_code == 1
    res = grun(vault, "remove", "2307.15043")
    assert res.exit_code == 0 and "Removed 2307.15043." in res.output
    assert grun(vault, "remove", "2307.15043").exit_code == 1
    assert "0/10" in grun(vault, "list").output


def test_cli_corrupt_file_clean_error(vault):
    (vault / "gold" / "gold.json").write_text("nope")
    res = grun(vault, "list")
    assert res.exit_code == 1 and "gold.json" in res.output


def three(vault):
    add_gold(vault, GoldPaper(paper_id="2307.15043", title="T", key_facts=["one", "two", "three"]))


def facts(vault):
    return load_gold(vault)[0].key_facts


def test_edit_fact_core(vault):
    three(vault)
    assert edit_fact(vault, "2307.15043v2", 2, "  new  ") == "two"
    assert facts(vault) == ["one", "new", "three"]
    assert edit_fact(vault, "1111.1111", 1, "x") is None
    for bad in (0, 4):
        with pytest.raises(ValueError):
            edit_fact(vault, "2307.15043", bad, "x")
    with pytest.raises(ValueError):
        edit_fact(vault, "2307.15043", 1, "  ")
    assert facts(vault) == ["one", "new", "three"]


def test_remove_fact_core(vault):
    three(vault)
    assert remove_fact(vault, "2307.15043", 1) == "one"
    assert facts(vault) == ["two", "three"]
    assert remove_fact(vault, "1111.1111", 1) is None
    with pytest.raises(ValueError):
        remove_fact(vault, "2307.15043", 3)
    remove_fact(vault, "2307.15043", 1)
    with pytest.raises(ValueError, match="only fact"):
        remove_fact(vault, "2307.15043", 1)
    assert facts(vault) == ["three"]


def test_cli_list_numbers_facts(vault):
    three(vault)
    assert "2. two" in grun(vault, "list").output


def test_cli_edit_fact_args(vault):
    three(vault)
    res = grun(vault, "edit-fact", "2307.15043", "2", "better")
    assert res.exit_code == 0 and "Updated fact 2" in res.output
    assert facts(vault) == ["one", "better", "three"]


def test_cli_edit_fact_guided(vault):
    three(vault)
    res = runner.invoke(cli.app, ["gold", "edit-fact", "--vault", str(vault)], input="2307.15043\n3\nfixed\n")
    assert res.exit_code == 0
    assert "3. three" in res.output
    assert facts(vault) == ["one", "two", "fixed"]


def test_cli_edit_fact_errors(vault):
    three(vault)
    assert grun(vault, "edit-fact", "1111.1111", "1", "x").exit_code == 1
    bad = grun(vault, "edit-fact", "2307.15043", "9", "x")
    assert bad.exit_code == 1 and "no fact 9" in bad.output
    assert grun(vault, "edit-fact", "2307.15043", "1", "  ").exit_code == 1
    assert facts(vault) == ["one", "two", "three"]


def test_cli_remove_fact_confirm(vault):
    three(vault)
    res = runner.invoke(cli.app, ["gold", "remove-fact", "2307.15043", "1", "--vault", str(vault)], input="n\n")
    assert res.exit_code == 0 and "Cancelled" in res.output
    assert facts(vault) == ["one", "two", "three"]
    res = runner.invoke(cli.app, ["gold", "remove-fact", "2307.15043", "1", "--vault", str(vault)], input="y\n")
    assert res.exit_code == 0 and "Removed fact 1" in res.output
    assert facts(vault) == ["two", "three"]


def test_cli_remove_fact_yes_guided_and_errors(vault):
    three(vault)
    assert grun(vault, "remove-fact", "2307.15043", "5", "--yes").exit_code == 1
    assert grun(vault, "remove-fact", "1111.1111", "1", "--yes").exit_code == 1
    res = runner.invoke(cli.app, ["gold", "remove-fact", "--yes", "--vault", str(vault)], input="2307.15043\n2\n")
    assert res.exit_code == 0
    assert facts(vault) == ["one", "three"]
    grun(vault, "remove-fact", "2307.15043", "1", "--yes")
    last = grun(vault, "remove-fact", "2307.15043", "1", "--yes")
    assert last.exit_code == 1 and "only fact" in last.output
    assert facts(vault) == ["three"]


def test_cli_guided_prompt_with_unknown_id_exits_1(vault):
    three(vault)
    for cmd, extra in (("edit-fact", []), ("remove-fact", ["--yes"])):
        res = runner.invoke(cli.app, ["gold", cmd, *extra, "--vault", str(vault)], input="1111.1111\n")
        assert res.exit_code == 1 and "1111.1111 is not in your gold set" in res.output
    assert facts(vault) == ["one", "two", "three"]


def test_cli_fact_commands_accept_versioned_and_mixed_case_ids(vault):
    three(vault)
    res = grun(vault, "edit-fact", "2307.15043v2", "1", "uno")
    assert res.exit_code == 0 and "Updated fact 1 of 2307.15043." in res.output
    res = grun(vault, "remove-fact", "ArXiv:2307.15043v3", "2", "--yes")
    assert res.exit_code == 0 and "Removed fact 2 of 2307.15043." in res.output
    assert facts(vault) == ["uno", "three"]


def test_cli_edit_and_remove_fact_clean_error_when_paper_vanishes(vault, monkeypatch):
    three(vault)
    monkeypatch.setattr(cli, "edit_fact", lambda *a: None)
    monkeypatch.setattr(cli, "remove_fact", lambda *a: None)
    res = grun(vault, "edit-fact", "2307.15043", "1", "x")
    assert res.exit_code == 1 and "not in your gold set" in res.output
    res = grun(vault, "remove-fact", "2307.15043", "1", "--yes")
    assert res.exit_code == 1 and "not in your gold set" in res.output
    assert "Traceback" not in res.output


def test_gold_fact_input_is_cleaned_like_topics(vault):
    add_gold(vault, gp())
    add_fact(vault, "2307.15043", "bad\x1b[31m fact‮  here\x07")
    edit_fact(vault, "2307.15043", 1, "ed\x85ited‮  one")
    assert load_gold(vault)[0].key_facts == ["ed ited one", "bad [31m fact here"]


def test_cli_gold_add_cleans_typed_facts(vault, monkeypatch):
    use(monkeypatch, StubArxiv(by_id=[paper()]))
    res = grun(vault, "add", "2307.15043", "--yes", input="clean\x07 me‮\n\n")
    assert res.exit_code == 0
    assert facts(vault) == ["clean me"]


def test_cli_add_fact_and_edit_fact_clean_and_reject_control_only_text(vault):
    three(vault)
    assert grun(vault, "add-fact", "2307.15043", "\x07‮").exit_code == 1
    assert grun(vault, "edit-fact", "2307.15043", "1", "\x07‮").exit_code == 1
    assert facts(vault) == ["one", "two", "three"]


def test_gold_list_cleans_hand_edited_control_and_bidi_text(vault):
    add_gold(vault, GoldPaper(paper_id="2307.15043", title="Ti\x1b[31mtle\u202e", key_facts=["fa\x07ct\u202e one"]))
    out = grun(vault, "list").output
    assert "\x1b" not in out and "\x07" not in out and "\u202e" not in out
    assert "Ti [31mtle" in out and "fa ct one" in out


def _add_candidate(conn, pid, chars, summarized=True):
    upsert_paper(conn, paper(pid, title=f"Paper {pid}"))
    save_fulltext(conn, pid, [Section(title="Intro", text="x" * chars)])
    if summarized:
        save_summary(
            conn, pid,
            PaperSummary(summary=summary(), critique=critique(), basis="full_text", truncated=False, unverified=[]),
            "m",
        )
    conn.commit()


def test_suggest_spreads_lengths(vault):
    conn = open_db(vault / "vault.sqlite")
    for n in range(1, 10):
        _add_candidate(conn, f"2401.0000{n}", n * 3000)
    picks = suggest_papers(conn, [], count=3)
    assert [s.tokens for s in picks] == [1000, 5000, 9000]


def test_suggest_skips_gold_and_unsummarized(vault):
    conn = open_db(vault / "vault.sqlite")
    _add_candidate(conn, "2401.00001", 3000)
    _add_candidate(conn, "2401.00002", 6000)
    _add_candidate(conn, "2401.00003", 9000, summarized=False)
    picks = suggest_papers(conn, [gp("2401.00001")])
    assert [s.paper_id for s in picks] == ["2401.00002"]


def test_suggest_fewer_than_count_returns_all(vault):
    conn = open_db(vault / "vault.sqlite")
    _add_candidate(conn, "2401.00001", 6000)
    _add_candidate(conn, "2401.00002", 3000)
    assert [s.tokens for s in suggest_papers(conn, [], count=5)] == [1000, 2000]


def test_cli_gold_suggest_lists_and_hints(vault):
    conn = open_db(vault / "vault.sqlite")
    _add_candidate(conn, "2401.00001", 3000)
    conn.close()
    out = runner.invoke(cli.app, ["gold", "suggest", "--vault", str(vault)])
    assert out.exit_code == 0, out.output
    assert "2401.00001" in out.output and "~1000 tokens" in out.output
    assert out.output.strip().splitlines()[-1].endswith("rpipe gold draft ID ...")


def test_cli_gold_suggest_empty_vault(vault):
    out = runner.invoke(cli.app, ["gold", "suggest", "--vault", str(vault)])
    assert out.exit_code == 0
    assert "No summarized papers with full text yet — run `rpipe run` first, or add papers by hand with `rpipe gold add`." in out.output
