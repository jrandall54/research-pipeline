from research_pipeline import library_api
from research_pipeline.library_api import PaperView
from research_pipeline.summary_models import Claim, Critique, PaperSummary, Summary
from research_pipeline.webapp import present


def _rs(*raw):
    return library_api.plain_reasons(list(raw))


def test_why_sentence_one_two_three_none():
    assert present.why_sentence(_rs("topic: agentic workflow (0.80)")) == "you follow agentic workflow."
    assert present.why_sentence(_rs("keyword: fuzzing", "followed author: Ada")) == (
        "it mentions fuzzing and it's by Ada, whom you follow.")
    assert present.why_sentence(_rs("topic: agentic workflow (0.80)", "liked similar: 2611.1", "HF upvotes: 56")) == (
        "you follow agentic workflow, it resembles a paper you liked, "
        "and 56 people upvoted it on Hugging Face.")
    assert present.why_sentence(_rs("near your shelf: tools")) == 'it\'s close to "tools" on your shelf.'
    assert present.why_sentence([]) is None


def test_topic_of_and_color():
    assert present.topic_of(_rs("keyword: x", "topic: agentic workflow (0.80)")) == "Agentic workflow"
    assert present.topic_of(_rs("keyword: x")) is None
    assert present.topic_color("Agentic Workflow") == present.topic_color("agentic workflow")
    assert 0 <= present.topic_color("x") < 8


def test_section_tag_range_and_roman_and_unnumbered():
    assert present.section_tag(["5.1 Efficiency", "5.2 Coordination"]) == "§5.1–5.2"
    assert present.section_tag(["IV-B Research agenda"]) == "§IV-B"
    assert present.section_tag(["Abstract"]) is None
    assert present.section_tag(["5.1 A", "5.1 B"]) == "§5.1"
    assert present.section_tag(["Abstract", "3 Method"]) == "§3"
    assert present.section_tag(["A.1 Proofs"]) == "§A.1"
    assert present.section_tag([]) is None


def test_day_heading_is_fixed_english():
    assert present.day_heading("2026-11-25") == "Wednesday, Nov 25"


def _view(**kw):
    base = dict(id="1", title="T", abstract="word " * 100, authors=[], url="u", published=None,
                status="in_digest", digest_date="2026-11-25", triage_score=0.5, triage_reasons=[],
                summary=None, vote=0, saved=False)
    base.update(kw)
    return PaperView(**base)


def test_card_unsummarized_excerpt_and_state():
    c = present.card(_view(), "pending")
    assert c.one_line is None and c.summarize_state == "pending"
    assert c.abstract.endswith("…") and len(c.abstract) <= 301
    assert present.card(_view(abstract="short"), None).abstract == "short"
    assert present.card(_view(), None).summarize_state == "none"


def test_card_summarized():
    c1 = Claim(text="p", sections=["5.1 X"])
    c2 = Claim(text="a", sections=["Abstract"])
    s = PaperSummary(summary=Summary(one_line="one", problem=c1, approach=c2, results=c1, limitations=c1),
                     critique=Critique(strengths=[c1], weaknesses=[], questions=[]),
                     basis="full_text", unverified=[], read_mode="whole", parts=1)
    c = present.card(_view(summary=s, triage_reasons=["topic: llm agents (0.5)"]), "failed")
    assert c.one_line == "one" and c.problem == ("p", "§5.1") and c.approach == ("a", None)
    assert c.summarize_state == "done" and c.topic == "Llm agents"


def test_sentences_splits_on_sentence_ends_only():
    assert present.sentences("First point. Second point.") == ["First point.", "Second point."]
    assert present.sentences("It won. 13.8 vs 26.0 minutes.") == ["It won.", "13.8 vs 26.0 minutes."]
    assert present.sentences("Is it? (Yes.) Done!") == ["Is it?", "(Yes.)", "Done!"]


def test_sentences_keeps_abbreviations_initials_and_decimals_together():
    for text in [
        "Methods, e.g. LoRA, are used.",
        "As in Smith et al. The result holds.",
        "Shown in Fig. 3 and Eq. 2.",
        "Written by J. Smith and colleagues.",
        "RePrism achieves 1.37x the throughput.",
        "A beats B vs. C here.",
    ]:
        assert present.sentences(text) == [text], text


def test_sentences_one_and_empty():
    assert present.sentences("Only one sentence") == ["Only one sentence"]
    assert present.sentences("   ") == []
    assert present.sentences("Spaced  out.   Next one.") == ["Spaced  out.", "Next one."]
