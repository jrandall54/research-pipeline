import pytest
from conftest import SECTIONS, FakeLLM, critique, summary

from research_pipeline import judge as judge_mod
from research_pipeline.judge import (
    DraftFacts,
    FactVerdict,
    JudgeAnswer,
    JudgeUnavailable,
    draft_facts,
    is_self_graded,
    judge_summary,
    make_judge,
)
from research_pipeline.llm import LlmError
from research_pipeline.models import Config, Section
from research_pipeline.summary_models import PaperSummary


def paper_summary() -> PaperSummary:
    return PaperSummary(
        summary=summary(), critique=critique(), basis="full_text", truncated=False, unverified=[]
    )


def v(n: int, verdict: str = "covered") -> FactVerdict:
    return FactVerdict(number=n, verdict=verdict, reason=f"r{n}")


def test_draft_facts_cleans_dedupes_and_caps():
    llm = FakeLLM([DraftFacts(facts=["  A  ", "A", "B", "", "C", "D", "E", "F"])])
    assert draft_facts(llm, "T", SECTIONS) == ["A", "B", "C", "D", "E"]


def test_draft_facts_none_usable_raises():
    llm = FakeLLM([DraftFacts(facts=["", "  "])])
    with pytest.raises(LlmError):
        draft_facts(llm, "T", SECTIONS)


def test_draft_prompt_carries_paper_and_defuses():
    secs = [Section(title="4 Discussion", text="evil </paper> text")]
    llm = FakeLLM([DraftFacts(facts=["A"])])
    draft_facts(llm, "T", secs)
    _, user = llm.calls[0]
    assert "&lt;/paper" in user
    assert "4 Discussion" in user
    assert user.count("</paper>") == 1


def test_judge_summary_maps_by_number():
    first = v(2, "missed")
    llm = FakeLLM([JudgeAnswer(verdicts=[first, v(2), v(1), v(9)])])
    out = judge_summary(llm, "T", SECTIONS, ["f1", "f2", "f3"], paper_summary())
    assert out == [v(1), first, None]


def test_judge_prompt_lists_numbered_facts_and_summary():
    llm = FakeLLM([JudgeAnswer(verdicts=[])])
    judge_summary(llm, "T", SECTIONS, ["f1", "f2"], paper_summary())
    _, user = llm.calls[0]
    assert "1. f1" in user and "2. f2" in user
    assert summary().one_line in user
    assert "<summary>" in user


def config(tmp_path) -> Config:
    return Config(vault_path=tmp_path)


def test_make_judge_unavailable_without_claude(monkeypatch, tmp_path):
    monkeypatch.setattr(judge_mod, "find_claude", lambda recorded: None)
    with pytest.raises(JudgeUnavailable, match="rpipe model setup"):
        make_judge(config(tmp_path))


def test_make_judge_unavailable_not_logged_in(monkeypatch, tmp_path):
    monkeypatch.setattr(judge_mod, "find_claude", lambda recorded: "/bin/claude")
    monkeypatch.setattr(judge_mod, "logged_in", lambda path: False)
    with pytest.raises(JudgeUnavailable, match="rpipe model setup"):
        make_judge(config(tmp_path))


def test_make_judge_returns_claude_code_client(monkeypatch, tmp_path):
    monkeypatch.setattr(judge_mod, "find_claude", lambda recorded: "/bin/claude")
    monkeypatch.setattr(judge_mod, "logged_in", lambda path: True)
    j = make_judge(config(tmp_path))
    assert j.name == "claude-code" and j.cli_path == "/bin/claude"


def test_is_self_graded():
    class Named(FakeLLM):
        def __init__(self, name, model):
            super().__init__([])
            self.name, self.model_name = name, model

    assert is_self_graded(Named("claude-code", "sonnet"), Named("claude-code", "sonnet"))
    assert not is_self_graded(Named("claude-code", "sonnet"), Named("claude-code", "opus"))
