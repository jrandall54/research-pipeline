from datetime import date

import pytest
from conftest import SECTIONS, AlwaysSummary
from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.cli import Pipeline
from research_pipeline.evaluate import Grade, latest_day, load_eval, report, run_eval, save_grade
from research_pipeline.fulltext import FullTextUnavailable
from research_pipeline.gold import GoldPaper, add_gold
from research_pipeline.llm import LlmError
from research_pipeline.models import PaperMeta
from research_pipeline.vault import init_vault

runner = CliRunner()
DAY = date(2026, 11, 21)


@pytest.fixture
def vault(tmp_path):
    path = tmp_path / "vault"
    init_vault(path, topics=["LLM security"])
    return path


def add_papers(vault, n, facts=("the method trains a classifier",)):
    for i in range(n):
        add_gold(vault, GoldPaper(paper_id=f"2401.{i:05d}", title=f"Paper {i}", key_facts=list(facts)))


def fetch(pid):
    return SECTIONS


def run(vault, n=1, llm=None, fetcher=fetch, abstract_for=None, day=DAY):
    return run_eval(vault, llm or AlwaysSummary([]), fetcher, 32768, day, abstract_for)


def test_run_eval_writes_json_and_markdown(vault):
    add_papers(vault, 2)
    md = run(vault)
    assert md == vault / "eval" / "2026-11-21.md"
    assert (vault / "eval" / "2026-11-21.json").exists()
    text = md.read_text()
    assert "- [ ] the method trains a classifier" in text
    assert "We train a classifier." in text  # cited section text beside the claim
    assert "Citations all verified: 2 of 2" in text
    data = load_eval(vault, DAY)
    assert [e.citations_ok for e in data.entries] == [True, True]


class FailFirst(AlwaysSummary):
    def __init__(self):
        super().__init__([])
        self.failed = False

    def generate(self, system, user, schema):
        if not self.failed:
            self.failed = True
            raise LlmError("model fell over")
        return super().generate(system, user, schema)


def test_run_eval_records_failure_and_continues(vault):
    add_papers(vault, 3)
    md = run(vault, llm=FailFirst())
    entries = load_eval(vault, DAY).entries
    assert entries[0].error == "model fell over" and entries[0].summary is None
    assert entries[1].summary and entries[2].summary
    assert "No summary: model fell over" in md.read_text()


def test_run_eval_abstract_only_paper_is_labelled(vault):
    add_papers(vault, 1)

    def none(pid):
        raise FullTextUnavailable("no html")

    md = run(vault, fetcher=none, abstract_for=lambda pid: "We study X.")
    assert "ABSTRACT ONLY" in md.read_text()
    assert load_eval(vault, DAY).entries[0].summary.basis == "abstract"


def test_run_eval_same_day_overwrites_and_clears_grades(vault):
    add_papers(vault, 1)
    run(vault)
    save_grade(vault, DAY, Grade(paper_id="2401.00000", accurate=True))
    run(vault)
    assert load_eval(vault, DAY).grades == []


# ---- CLI

def install(monkeypatch, fetcher=fetch):
    def pipe(config):
        return Pipeline(
            [], None, lambda ids: [PaperMeta(id=i, title="T", abstract="abs", source="arxiv", url="u") for i in ids],
            lambda: AlwaysSummary([]), fetcher,
        )

    monkeypatch.setattr(cli, "build_pipeline", pipe)


def erun(vault, *args, input=None):
    return runner.invoke(cli.app, ["eval", *args, "--vault", str(vault)], input=input)


def test_run_eval_empty_gold_cli_message(vault, monkeypatch):
    install(monkeypatch)
    res = erun(vault, "run")
    assert res.exit_code == 1
    assert "rpipe gold add" in res.output


def test_eval_run_cli_warns_and_reports_location(vault, monkeypatch):
    install(monkeypatch)
    add_papers(vault, 2)
    res = erun(vault, "run")
    assert res.exit_code == 0, res.output
    assert "fewer" in res.output.lower() or "Warning" in res.output
    assert "2026" in res.output and ".md" in res.output
    res = erun(vault, "run")
    assert "cleared" in res.output


def test_grade_flow_saves_each_grade_immediately(vault, monkeypatch):
    install(monkeypatch)
    add_papers(vault, 3)
    erun(vault, "run")
    res = erun(vault, "grade", input="y\n\nn\nwrong numbers\n")  # EOF at paper 3
    assert res.exit_code == 0, res.output
    assert "Stopped. Grades already given are saved" in res.output
    day = latest_day(vault)
    grades = load_eval(vault, day).grades
    assert [(g.accurate, g.note) for g in grades] == [(True, ""), (False, "wrong numbers")]
    res = erun(vault, "grade", input="y\n\n")
    assert "1 paper(s) to go" in res.output
    assert len(load_eval(vault, day).grades) == 3
    assert "Nothing left" in erun(vault, "grade").output


def graded_eval(vault, n, accurate):
    add_papers(vault, n)
    run(vault)
    for i in range(n):
        if i < accurate or i == n:
            save_grade(vault, DAY, Grade(paper_id=f"2401.{i:05d}", accurate=True))
        elif i < n:
            save_grade(vault, DAY, Grade(paper_id=f"2401.{i:05d}", accurate=False, note=f"bad {i}"))


def test_report_pass_and_fail(vault, tmp_path):
    graded_eval(vault, 10, 9)
    out = report(vault, DAY)
    assert "Result: PASS" in out and "Accurate: 9 of 10" in out
    v2 = tmp_path / "v2"
    init_vault(v2, topics=["x"])
    graded_eval(v2, 10, 8)
    out = report(v2, DAY)
    assert "Result: FAIL" in out and "bad 8" in out and "bad 9" in out and "Paper 8" in out


def test_report_ungraded_not_counted_accurate(vault):
    add_papers(vault, 2)
    run(vault)
    save_grade(vault, DAY, Grade(paper_id="2401.00000", accurate=True))
    out = report(vault, DAY)
    assert "Accurate: 1 of 2" in out and "Not graded yet" in out and "Paper 1" in out


def test_eval_report_without_eval_run_message(vault):
    res = erun(vault, "report")
    assert res.exit_code == 1
    assert "rpipe eval run" in res.output


def test_run_eval_thin_page_falls_back_to_abstract(vault):
    from research_pipeline.models import Section

    add_papers(vault, 1)
    run(vault, fetcher=lambda pid: [Section(title="Intro", text="x")], abstract_for=lambda pid: "We study X.")
    assert load_eval(vault, DAY).entries[0].summary.basis == "abstract"
