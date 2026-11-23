import pytest
from conftest import SECTIONS, FakeLLM
from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.cli import Pipeline
from research_pipeline.fulltext import FullTextUnavailable
from research_pipeline.gold import GoldPaper, add_gold, load_gold
from research_pipeline.judge import DraftFacts, JudgeUnavailable
from research_pipeline.models import PaperMeta
from research_pipeline.vault import init_vault

runner = CliRunner()


@pytest.fixture
def vault(tmp_path):
    path = tmp_path / "vault"
    init_vault(path, topics=["LLM security"])
    return path


def paper(pid):
    return PaperMeta(id=pid, title=f"Title {pid}", abstract="abs", authors=[], url="u", published="2026-01-01", source="arxiv")


class Calls:
    def __init__(self):
        self.seeds = []
        self.texts = []


def install(monkeypatch, judge_factory, fulltext=None, found=True):
    calls = Calls()

    def seeds(ids):
        calls.seeds.append(ids)
        return [paper(i) for i in ids] if found else []

    def text(pid):
        calls.texts.append(pid)
        if fulltext is not None:
            return fulltext(pid)
        return SECTIONS

    def build(config):
        return Pipeline(
            sources=[], make_embedder=lambda: None, seed_fetcher=seeds,
            make_llm=lambda: None, fulltext_fetcher=text, make_judge=judge_factory,
        )

    monkeypatch.setattr(cli, "build_pipeline", build)
    return calls


def invoke(vault, ids, text):
    return runner.invoke(cli.app, ["gold", "draft", *ids, "--vault", str(vault)], input=text)


def test_draft_keep_edit_drop_then_save(vault, monkeypatch):
    install(monkeypatch, lambda: FakeLLM([DraftFacts(facts=["a", "b", "c"])]))
    result = invoke(vault, ["2401.00001"], "k\ne\nb2\nd\n\n")
    assert result.exit_code == 0, result.output
    assert load_gold(vault)[0].key_facts == ["a", "b2"]
    assert load_gold(vault)[0].title == "Title 2401.00001"


def test_draft_extra_facts_appended(vault, monkeypatch):
    install(monkeypatch, lambda: FakeLLM([DraftFacts(facts=["a", "b"])]))
    result = invoke(vault, ["2401.00001"], "k\nk\nx\n\n")
    assert result.exit_code == 0, result.output
    assert load_gold(vault)[0].key_facts == ["a", "b", "x"]


def test_draft_empty_edit_keeps_fact(vault, monkeypatch):
    install(monkeypatch, lambda: FakeLLM([DraftFacts(facts=["a"])]))
    result = invoke(vault, ["2401.00001"], "e\n\n\n")
    assert result.exit_code == 0, result.output
    assert load_gold(vault)[0].key_facts == ["a"]


def test_draft_all_dropped_saves_nothing(vault, monkeypatch):
    install(monkeypatch, lambda: FakeLLM([DraftFacts(facts=["a"])]))
    result = invoke(vault, ["2401.00001"], "d\n\n")
    assert result.exit_code == 0
    assert "No facts kept, so nothing was saved for 2401.00001." in result.output
    assert load_gold(vault) == []


def test_draft_no_fulltext_skips_with_hint(vault, monkeypatch):
    def nope(pid):
        raise FullTextUnavailable("no html")

    install(monkeypatch, lambda: FakeLLM([]), fulltext=nope)
    result = invoke(vault, ["2401.00001"], "")
    assert result.exit_code == 0
    assert "rpipe gold add" in result.output
    assert load_gold(vault) == []


def test_draft_abort_saves_nothing_for_that_paper(vault, monkeypatch):
    install(monkeypatch, lambda: FakeLLM([DraftFacts(facts=["a"]), DraftFacts(facts=["z"])]))
    result = invoke(vault, ["2401.00001", "2401.00002"], "k\n\nk")
    assert result.exit_code == 0
    assert "Stopped. Nothing was saved for 2401.00002." in result.output
    assert [p.paper_id for p in load_gold(vault)] == ["2401.00001"]


def test_draft_judge_unavailable_exits_1(vault, monkeypatch):
    def factory():
        raise JudgeUnavailable("log in")

    calls = install(monkeypatch, factory)
    result = invoke(vault, ["2401.00001"], "")
    assert result.exit_code == 1
    assert "log in" in result.output
    assert calls.seeds == [] and calls.texts == []


def test_draft_skips_existing_gold(vault, monkeypatch):
    add_gold(vault, GoldPaper(paper_id="2401.00001", title="T", key_facts=["f"]))
    calls = install(monkeypatch, lambda: FakeLLM([]))
    result = invoke(vault, ["2401.00001"], "")
    assert result.exit_code == 0
    assert "already in your gold set" in result.output
    assert calls.seeds == []


def test_draft_paper_not_found_goes_on(vault, monkeypatch):
    calls = install(monkeypatch, lambda: FakeLLM([]), found=False)
    result = invoke(vault, ["2401.00001"], "")
    assert result.exit_code == 0
    assert "No paper found for 2401.00001 on arXiv." in result.output
    assert calls.texts == []
