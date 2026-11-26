from research_pipeline.prompts import CRITIQUE_TASK, PROMPT_VERSION, SUMMARY_TASK
from research_pipeline.summary_models import Critique, PaperSummary


def test_critique_prompt_states_length_limits():
    assert "3 to 5 strengths, 3 to 5 weaknesses and 3 to 5 questions" in CRITIQUE_TASK
    assert "at most two short sentences" in CRITIQUE_TASK


def test_summary_prompt_states_length_limits():
    assert "at most two short sentences" in SUMMARY_TASK
    assert "one_line" in SUMMARY_TASK and "one sentence" in SUMMARY_TASK


def test_prompt_version_is_p9():
    assert PROMPT_VERSION == "2026-11-p9"


def test_summary_prompt_asks_for_hook_and_key_numbers():
    from research_pipeline.prompts import SHARED_SYSTEM

    text = SHARED_SYSTEM + SUMMARY_TASK
    assert "hook" in text and "key_numbers" in text and "at most 90 characters" in text
    assert "no questions, no teasers" in text


def test_plain_words_rule_in_summary_critique_and_combine():
    from research_pipeline.models import Section
    from research_pipeline.prompts import build_combine_prompts, build_critique_prompt, build_summary_prompt
    from research_pipeline.summary_models import Claim

    rule = "Write for a reader who is not an expert in this field"
    secs = [Section(title="A", text="b")]
    for system, _ in (
        build_summary_prompt("T", secs, "full_text"),
        build_critique_prompt("T", secs, "full_text"),
        *build_combine_prompts("T", [Claim(text="x", sections=["A"])], ["A"]),
    ):
        assert rule in system


def test_limits_are_prompt_only_old_long_lists_still_load():
    claim = {"text": "x", "sections": ["A"]}
    c = Critique.model_validate({"strengths": [claim] * 9, "weaknesses": [claim] * 9, "questions": ["q"] * 12})
    assert len(c.strengths) == 9


def test_old_summary_reads_as_whole():
    claim = {"text": "x", "sections": ["A"]}
    old = {
        "summary": {"one_line": "x", "problem": claim, "approach": claim, "results": claim, "limitations": claim},
        "critique": {"strengths": [claim], "weaknesses": [claim], "questions": ["q"]},
        "basis": "full_text",
        "unverified": [],
    }
    ps = PaperSummary.model_validate(old)
    assert ps.read_mode == "whole" and ps.parts == 1
