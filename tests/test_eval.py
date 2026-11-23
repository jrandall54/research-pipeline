from datetime import date

import pytest
from conftest import SECTIONS, AlwaysSummary, FakeLLM
from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.cli import Pipeline
from research_pipeline.evaluate import (
    EvalEntry,
    EvalFile,
    Grade,
    compare,
    latest_day,
    load_eval,
    report,
    run_eval,
    save_grade,
)
from research_pipeline.fulltext import FullTextUnavailable
from research_pipeline.gold import GoldPaper, add_gold
from research_pipeline.judge import FactVerdict, JudgeAnswer
from research_pipeline.llm import LlmError, LlmLimitReached
from research_pipeline.models import PaperMeta, Section
from research_pipeline.prompts import PROMPT_VERSION
from research_pipeline.summarize import PaperSummary
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


def test_run_eval_emits_one_step_per_paper(vault):
    from research_pipeline.progress import ListReporter

    add_papers(vault, 2)
    llm = FailFirst()
    rep = ListReporter()
    run_eval(vault, llm, fetch, 32768, DAY, None, reporter=rep)
    steps = [(e.label, e.outcome) for e in rep.events if e.kind == "step"]
    assert steps == [("2401.00000", "failed"), ("2401.00001", "ok")]
    assert rep.events[0].kind == "start" and rep.events[0].total == 2
    assert rep.events[-1].kind == "done"


# ---- judge ----

def covered(n=1):
    return FactVerdict(number=n, verdict="covered", reason="r")


def judge_ok():
    return JudgeAnswer(verdicts=[covered()])


class ScriptedJudge(FakeLLM):
    def generate(self, system, user, schema):
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def jrun(vault, judge, day=DAY, llm=None):
    return run_eval(vault, llm or AlwaysSummary([]), fetch, 32768, day, judge=judge)


def test_eval_judge_records_verdicts_and_setup(vault):
    add_papers(vault, 1)
    jrun(vault, ScriptedJudge([judge_ok()]))
    data = load_eval(vault, DAY)
    assert [v.verdict for v in data.entries[0].verdicts] == ["covered"]
    assert data.setup.model == "fake-llm"
    assert data.setup.prompt_version == PROMPT_VERSION
    assert data.setup.num_ctx == 32768 and data.setup.judge_model == "fake-llm"


def test_eval_judge_limit_stops_judging_keeps_summaries(vault):
    add_papers(vault, 3)
    judge = ScriptedJudge([LlmLimitReached("limit")])
    jrun(vault, judge)
    data = load_eval(vault, DAY)
    assert all(e.summary for e in data.entries)
    assert all(e.verdicts is None for e in data.entries)
    assert [e.judge_error for e in data.entries] == ["usage limit reached"] * 3
    assert judge.responses == []  # asked once, then stopped
    assert "Judged: 0 of 3" in report(vault, DAY)


def test_eval_judge_error_skips_one_paper(vault):
    add_papers(vault, 2)
    jrun(vault, ScriptedJudge([LlmError("boom"), judge_ok()]))
    e = load_eval(vault, DAY).entries
    assert e[0].verdicts is None and e[0].judge_error == "boom"
    assert e[1].verdicts is not None


def test_eval_self_graded_flag(vault):
    add_papers(vault, 1)
    llm, judge = AlwaysSummary([]), ScriptedJudge([judge_ok()])
    llm.name = judge.name = "claude-code"
    jrun(vault, judge, llm=llm)
    assert load_eval(vault, DAY).setup.self_graded
    assert "Self-graded" in report(vault, DAY)


def test_report_counts_not_judged_separately(vault):
    add_papers(vault, 1, facts=("a", "b"))
    jrun(vault, ScriptedJudge([judge_ok()]))
    assert "Facts covered: 1 of 2 (missed 0, contradicted 0, not judged 1)" in report(vault, DAY)


def _file(day, covered_n, contradicted=0, unverified=0, pid="p1"):

    s = PaperSummary.model_validate(
        {**run_summary_dict(), "unverified": ["x"] * unverified}
    )
    vs = [covered(i + 1) for i in range(covered_n)]
    vs += [FactVerdict(number=len(vs) + 1, verdict="contradicted", reason="r") for _ in range(contradicted)]
    vs += [FactVerdict(number=len(vs) + 1, verdict="missed", reason="r") for _ in range(4 - len(vs))]
    return EvalFile(day=day, entries=[EvalEntry(paper_id=pid, title="T", summary=s, verdicts=vs)])


def run_summary_dict():
    from research_pipeline.summarize import summarize_paper

    return summarize_paper(AlwaysSummary([]), "T", "", SECTIONS, 32768).model_dump()


def test_compare_pass_and_fail_rules():
    base = _file(DAY, 4)
    assert compare(base, _file(date(2026, 11, 22), 3)).endswith("Change: PASS")
    assert compare(base, _file(date(2026, 11, 22), 2)).endswith("Change: FAIL (facts covered)")
    assert "FAIL (contradicted)" in compare(base, _file(date(2026, 11, 22), 3, contradicted=1))
    assert "FAIL (unverified citations)" in compare(base, _file(date(2026, 11, 22), 4, unverified=1))


def test_compare_no_common_papers():
    assert compare(_file(DAY, 2, pid="a"), _file(date(2026, 11, 22), 2, pid="b")) == "No earlier judged run to compare."


def test_report_compares_with_earlier_judged_run(vault):
    add_papers(vault, 1)
    jrun(vault, ScriptedJudge([judge_ok()]), day=DAY)
    jrun(vault, ScriptedJudge([judge_ok()]), day=date(2026, 11, 22))
    out = report(vault, date(2026, 11, 22))
    assert "Compared with the earlier run on 2026-11-21" in out and "Change: PASS" in out
    assert "No earlier judged run to compare." in report(vault, DAY)


def test_old_eval_file_still_loads(vault):
    add_papers(vault, 1)
    run(vault)
    import json

    path = vault / "eval" / "2026-11-21.json"
    d = json.loads(path.read_text())
    d.pop("setup")
    for e in d["entries"]:
        e.pop("verdicts"), e.pop("judge_error")
    path.write_text(json.dumps(d))
    out = report(vault, DAY)
    assert "Human grades: none yet" in out and "Judged" not in out


def test_cli_eval_run_judge_unavailable_exits_before_summarizing(vault, monkeypatch):
    install(monkeypatch)
    add_papers(vault, 1)
    res = erun(vault, "run", "--judge")
    assert res.exit_code == 1
    assert "judge is not available" in res.output
    assert not (vault / "eval").exists() or not list((vault / "eval").glob("*.json"))


def test_eval_judge_unexpected_error_does_not_duplicate_entry(vault):
    add_papers(vault, 2)
    jrun(vault, ScriptedJudge([RuntimeError("odd"), judge_ok()]))
    e = load_eval(vault, DAY).entries
    assert len(e) == 2
    assert e[0].summary is not None and e[0].judge_error == "odd" and e[0].verdicts is None
    assert e[1].verdicts is not None


def test_eval_judge_progress_says_judging(vault):
    from research_pipeline.progress import ListReporter

    add_papers(vault, 1)
    rep = ListReporter()
    run_eval(vault, AlwaysSummary([]), fetch, 32768, DAY, reporter=rep, judge=ScriptedJudge([judge_ok()]))
    assert any(ev.kind == "note" and "judging" in ev.label for ev in rep.events)
    assert [ev.current for ev in rep.events if ev.kind == "step"] == [1]


# ---- review fixes ----

def facts_file(day, facts, verdicts, pid="p1", setup=None):
    s = PaperSummary.model_validate(run_summary_dict())
    return EvalFile(
        day=day, setup=setup,
        entries=[EvalEntry(paper_id=pid, title="T", summary=s, verdicts=verdicts, key_facts=facts)],
    )


def test_same_day_rerun_keeps_baseline(vault):
    add_papers(vault, 1)
    jrun(vault, ScriptedJudge([judge_ok()]))
    jrun(vault, ScriptedJudge([judge_ok()]))
    assert (vault / "eval" / "2026-11-21.previous.json").exists()
    assert latest_day(vault) == DAY  # .previous is not a day
    out = report(vault, DAY)
    assert "the earlier run today" in out and "Change: PASS" in out
    assert "No earlier judged run" not in out


def test_unjudged_rerun_keeps_older_previous(vault):
    add_papers(vault, 1)
    jrun(vault, ScriptedJudge([judge_ok()]))
    jrun(vault, ScriptedJudge([judge_ok()]))  # first judged run kept as .previous
    run(vault)  # replaces a judged file: it becomes .previous
    run(vault)  # replaces an unjudged file: .previous is left alone
    prev = load_eval(vault, DAY).__class__.model_validate_json((vault / "eval" / "2026-11-21.previous.json").read_text())
    assert prev.entries[0].verdicts is not None


def test_cli_says_baseline_kept(vault, monkeypatch):
    install(monkeypatch)
    add_papers(vault, 1)
    jrun(vault, ScriptedJudge([judge_ok()]), day=date.today())
    res = erun(vault, "run")
    assert "Kept today's earlier judged run as the baseline" in res.output
    assert "rpipe eval grade, then rpipe eval report (or re-run with --judge" in res.output


def test_compare_matches_facts_by_text_when_one_is_edited():
    a = facts_file(DAY, ["f1", "f2", "f3"], [covered(1), covered(2), covered(3)])
    b = facts_file(
        date(2026, 11, 22), ["f1", "edited", "F3"],
        [covered(1), FactVerdict(number=2, verdict="missed", reason="r"), covered(3)],
    )
    out = compare(a, b)
    assert "facts changed for p1 since the earlier run; compared only the unchanged ones" in out
    assert "Facts covered: 2 -> 2" in out and out.endswith("Change: PASS")


def test_compare_fact_judged_in_only_one_run_is_skipped():
    a = facts_file(DAY, ["f1", "f2"], [covered(1), covered(2)])
    b = facts_file(DAY, ["f1", "f2"], [covered(1), None])
    out = compare(a, b)
    assert "Facts covered: 1 -> 1" in out


def test_compare_shows_both_setups():
    from research_pipeline.evaluate import EvalSetup

    def su(model, judge):
        return EvalSetup(backend="ollama", model=model, num_ctx=8192, prompt_version="v1", judge_model=judge)

    a = facts_file(DAY, ["f1"], [covered(1)], setup=su("m-old", "j1"))
    b = facts_file(date(2026, 11, 22), ["f1"], [covered(1)], setup=su("m-new", "j2"))
    out = compare(a, b)
    assert "Earlier: ollama/m-old" in out and "judge j1" in out
    assert "Now: ollama/m-new" in out and "judge j2" in out


def test_report_no_human_grades_has_no_human_result(vault):
    add_papers(vault, 1)
    jrun(vault, ScriptedJudge([judge_ok()]))
    out = report(vault, DAY)
    assert "Human grades: none yet (optional: rpipe eval grade)" in out
    assert "Result:" not in out and "Accurate:" not in out
    save_grade(vault, DAY, Grade(paper_id="2401.00000", accurate=True))
    assert "Result: PASS" in report(vault, DAY)


def test_report_lists_not_judged_reasons(vault):
    add_papers(vault, 1)
    jrun(vault, ScriptedJudge([LlmError("boom")]))
    assert "not judged 2401.00000 Paper 0: boom" in report(vault, DAY)


def test_eval_markdown_lists_what_was_left_out_and_cleans_titles(vault):
    add_papers(vault, 1)
    secs = [*SECTIONS, Section(title="References", text="[1] x"), Section(title="Funding\x1b[31m", text="grant")]
    md = run(vault, fetcher=lambda pid: secs).read_text()
    left = next(line for line in md.splitlines() if line.startswith("Left out:"))
    assert "References (" in left and "Funding" in left and "\x1b" not in left
