import pytest
from conftest import FakeLLM
from test_gold import _add_candidate
from test_gold_draft import install
from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.db import open_db
from research_pipeline.evaluate import report as eval_report
from research_pipeline.fulltext import FullTextUnavailable
from research_pipeline.gold import GoldPaper, add_gold, load_gold
from research_pipeline.judge import DraftFacts, JudgeUnavailable
from research_pipeline.vault import init_vault

runner = CliRunner()


@pytest.fixture
def vault(tmp_path):
    path = tmp_path / "vault"
    init_vault(path, topics=["LLM security"])
    conn = open_db(path / "vault.sqlite")
    for n in range(1, 4):
        _add_candidate(conn, f"2401.0000{n}", n * 3000)
    conn.close()
    return path


def auto(vault, *extra):
    return runner.invoke(cli.app, ["gold", "auto", *extra, "--vault", str(vault)])


def facts_llm(n):
    return FakeLLM([DraftFacts(facts=[f"fact {i} a", f"fact {i} b"]) for i in range(n)])


def test_auto_saves_unreviewed_papers_without_prompts(vault, monkeypatch):
    install(monkeypatch, lambda: facts_llm(3))
    result = auto(vault, "--count", "3")
    assert result.exit_code == 0, result.output
    papers = load_gold(vault)
    assert len(papers) == 3
    assert all(p.unreviewed and len(p.key_facts) == 2 for p in papers)
    assert "Added 3 paper(s), all unreviewed" in result.output


def test_auto_default_fills_up_to_target_and_skips_existing(vault, monkeypatch):
    add_gold(vault, GoldPaper(paper_id="2401.00001", title="T", key_facts=["mine"]))
    calls = install(monkeypatch, lambda: facts_llm(5))
    result = auto(vault)
    assert result.exit_code == 0, result.output
    ids = [p.paper_id for p in load_gold(vault)]
    assert ids.count("2401.00001") == 1 and len(ids) == 3  # only 2 candidates left in the vault
    assert "2401.00001" not in [i for call in calls.seeds for i in call]
    assert load_gold(vault)[0].unreviewed is False  # the hand-made paper is untouched


def test_auto_does_nothing_when_set_is_full(vault, monkeypatch):
    for n in range(10):
        add_gold(vault, GoldPaper(paper_id=f"2301.0000{n}", title="T", key_facts=["f"]))
    calls = install(monkeypatch, lambda: facts_llm(1))
    result = auto(vault)
    assert result.exit_code == 0
    assert "nothing to add" in result.output
    assert calls.seeds == []


def test_auto_judge_unavailable_exits_1_before_fetching(vault, monkeypatch):
    def factory():
        raise JudgeUnavailable("log in")

    calls = install(monkeypatch, factory)
    result = auto(vault)
    assert result.exit_code == 1 and "log in" in result.output
    assert calls.seeds == [] and load_gold(vault) == []


def test_auto_skips_paper_without_full_text_and_goes_on(vault, monkeypatch):
    def text(pid):
        if pid == "2401.00002":
            raise FullTextUnavailable("no html")
        from conftest import SECTIONS
        return SECTIONS

    install(monkeypatch, lambda: facts_llm(3), fulltext=text)
    result = auto(vault, "--count", "3")
    assert result.exit_code == 0, result.output
    assert sorted(p.paper_id for p in load_gold(vault)) == ["2401.00001", "2401.00003"]


def test_auto_with_no_candidates_points_to_run(tmp_path, monkeypatch):
    path = tmp_path / "empty"
    init_vault(path, topics=["x"])
    install(monkeypatch, lambda: facts_llm(1))
    result = auto(path)
    assert result.exit_code == 0 and "rpipe run" in result.output


def test_list_marks_unreviewed(vault):
    add_gold(vault, GoldPaper(paper_id="2401.00001", title="T", key_facts=["f"], unreviewed=True))
    out = runner.invoke(cli.app, ["gold", "list", "--vault", str(vault)]).output
    assert "(unreviewed: drafted by Claude)" in out


def test_old_gold_files_load_as_reviewed(vault):
    (vault / "gold").mkdir(exist_ok=True)
    (vault / "gold" / "gold.json").write_text('[{"paper_id": "2401.00001", "title": "T", "key_facts": ["f"]}]')
    assert load_gold(vault)[0].unreviewed is False


def _judged_report(vault, unreviewed):
    from datetime import date

    from test_eval import AlwaysSummary, ScriptedJudge, fetch, judge_ok

    from research_pipeline.evaluate import run_eval

    add_gold(vault, GoldPaper(paper_id="2401.00001", title="T", key_facts=["f"], unreviewed=unreviewed))
    day = date(2026, 11, 21)
    run_eval(vault, AlwaysSummary([]), fetch, 32768, day, judge=ScriptedJudge([judge_ok()]))
    return eval_report(vault, day)


def test_report_warns_when_key_is_unreviewed(vault):
    assert "Unreviewed answer key: 1 of 1 papers" in _judged_report(vault, True)


def test_report_has_no_warning_for_a_reviewed_key(vault):
    assert "Unreviewed answer key" not in _judged_report(vault, False)
