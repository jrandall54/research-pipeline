from research_pipeline.prompts import CRITIQUE_SYSTEM, PROMPT_VERSION, SUMMARY_SYSTEM
from research_pipeline.summary_models import Critique


def test_critique_prompt_states_length_limits():
    assert "3 to 5 strengths, 3 to 5 weaknesses and 3 to 5 questions" in CRITIQUE_SYSTEM
    assert "at most two sentences" in CRITIQUE_SYSTEM


def test_summary_prompt_states_length_limits():
    assert "at most two sentences" in SUMMARY_SYSTEM
    assert "one_line" in SUMMARY_SYSTEM and "one sentence" in SUMMARY_SYSTEM


def test_prompt_version_bumped_for_pruning():
    assert PROMPT_VERSION == "2026-11-p5"


def test_limits_are_prompt_only_old_long_lists_still_load():
    claim = {"text": "x", "sections": ["A"]}
    c = Critique.model_validate({"strengths": [claim] * 9, "weaknesses": [claim] * 9, "questions": ["q"] * 12})
    assert len(c.strengths) == 9
