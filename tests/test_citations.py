from research_pipeline.citations import section_texts
from research_pipeline.models import Section

SECS = [Section(title="1 Intro", text="intro text"), Section(title="2 Method", text="method text")]


def test_section_texts_matches_titles_ignoring_case():
    assert section_texts([" 2 method "], SECS, "abs") == ["method text"]


def test_section_texts_none_when_a_title_is_unknown():
    assert section_texts(["1 Intro", "9 Nope"], SECS, "abs") is None


def test_section_texts_abstract_without_an_abstract_section():
    assert section_texts(["Abstract"], SECS, "the abstract") == ["the abstract"]


def test_section_texts_abstract_with_an_abstract_section():
    secs = [Section(title="Abstract", text="section abstract"), *SECS]
    assert section_texts(["Abstract"], secs, "the abstract") == ["section abstract", "the abstract"]
