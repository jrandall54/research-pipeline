import pytest

from research_pipeline.models import Section
from research_pipeline.pruning import CHARS_PER_TOKEN, NEEDED_STEP, RESERVED_TOKENS, _size, classify, fit_to_budget
from research_pipeline.summary_models import Dropped


def sec(title, n=100):
    return Section(title=title, text="x" * n)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("References", "non_paper"), ("7 References", "non_paper"), ("Bibliography", "non_paper"),
        ("Acknowledgements", "non_paper"), ("Funding", "non_paper"),
        ("Author Contributions", "non_paper"), ("Competing Interests", "non_paper"),
        ("Conflicts of Interest", "non_paper"), ("Ethics Statement", "non_paper"),
        ("Reproducibility Checklist", "non_paper"), ("Instructions for reporting errors", "non_paper"),
        ("A Appendix", "appendix"), ("Appendix B Proofs", "appendix"),
        ("Supplementary Note 3", "appendix"), ("6 Supplementary Information", "appendix"),
        ("Extended Data", "appendix"), ("Supplemental Material", "appendix"),
        ("1 Introduction", "paper"), ("4 Results", "paper"),
    ],
)
def test_classify_table(title, expected):
    assert classify(title) == expected


def test_data_availability_statement_vs_content():
    assert classify("Data availability", "x" * 200) == "non_paper"
    assert classify("Data availability", "x" * 5000) == "paper"


def test_non_paper_always_dropped_even_when_it_fits():
    secs = [sec("Abstract"), sec("1 Intro"), sec("References", 50)]
    fit = fit_to_budget(secs, 1_000_000)
    assert [s.title for s in fit.sections] == ["Abstract", "1 Intro"]
    assert fit.body_cut is False
    assert fit.dropped == [Dropped(title="References", chars=len("References") + 50, reason="non_paper")]


def test_appendix_kept_when_paper_fits_dropped_first_when_short():
    secs = [sec("Abstract"), sec("1 Introduction", 1000), sec("References", 500), sec("Appendix A Proofs", 2000)]
    fit = fit_to_budget(secs, 32768)
    assert "Appendix A Proofs" in [s.title for s in fit.sections]
    assert [d.reason for d in fit.dropped] == ["non_paper"]
    fit = fit_to_budget(secs, 5000)  # budget 3000: core text fits, appendix does not
    assert [s.title for s in fit.sections] == ["Abstract", "1 Introduction"]
    assert fit.body_cut is False
    assert [d.reason for d in fit.dropped] == ["non_paper", "appendix"]


def test_children_of_dropped_heading_go_with_it():
    secs = [
        sec("1 Intro", 1000), sec("6 Supplementary Information", 50), sec("6.1 Note one", 2000),
        sec("6.2 Note two", 2000), sec("7 Conclusion", 100),
    ]
    fit = fit_to_budget(secs, 5000)
    assert [s.title for s in fit.sections] == ["1 Intro", "7 Conclusion"]
    assert [d.title for d in fit.dropped] == ["6 Supplementary Information", "6.1 Note one", "6.2 Note two"]


def test_appendix_letter_children_go_with_it():
    secs = [sec("1 Intro", 1000), sec("Appendix A Proofs", 50), sec("A.1 Lemma", 3000)]
    fit = fit_to_budget(secs, 5000)
    assert [s.title for s in fit.sections] == ["1 Intro"]


def test_ordered_trim_spares_protected_sections():
    secs = [
        sec("Abstract", 100), sec("1 Introduction", 500), sec("Related Work", 2000),
        sec("3 Method", 2000), sec("5 Results", 500), sec("6 Conclusion", 200),
    ]
    budget = (5000 - 4000) * 3
    fit = fit_to_budget(secs, 5000)
    assert fit.body_cut is True
    by = {s.title: s for s in fit.sections}
    for title in ("Abstract", "1 Introduction", "5 Results", "6 Conclusion"):
        assert by[title].text == next(s.text for s in secs if s.title == title)
    for title in ("Related Work", "3 Method"):
        assert by[title].text.endswith(" [...]") and len(by[title].text) < 2000
    assert sum(len(s.title) + len(s.text) for s in fit.sections) <= budget


def test_uniform_trim_when_ordered_trim_is_not_enough():
    secs = [sec("1 Introduction", 5000), sec("5 Results", 5000)]
    fit = fit_to_budget(secs, 5000)
    assert fit.body_cut is True
    assert len(fit.sections[0].text) == len(fit.sections[1].text) < 5000
    assert fit.sections[0].text.endswith(" [...]")
    assert sum(len(s.title) + len(s.text) for s in fit.sections) <= 3000


def test_degenerate_inputs():
    assert fit_to_budget([], 32768).sections == []
    fit = fit_to_budget([sec("References")], 32768)
    assert fit.sections == [] and fit.body_cut is False


def test_needed_ctx_set_only_when_body_cut():
    core = [sec("Abstract", 2000), sec("1 Introduction", 98_000), sec("4 Results", 200_000)]
    secs = [*core, sec("Appendix A Proofs", 90_000), sec("References", 5000)]
    core_chars = _size(core)  # titles included; appendix and references left out
    fit = fit_to_budget(secs, 32768)
    assert fit.body_cut is True
    assert (CHARS_PER_TOKEN, RESERVED_TOKENS, NEEDED_STEP) == (3, 4000, 1024)
    assert fit.needed_ctx == -(-(-(-core_chars // 3) + 4000) // 1024) * 1024
    assert fit.needed_ctx % 1024 == 0 and fit.needed_ctx > 32768


def test_needed_ctx_none_when_the_paper_fits():
    assert fit_to_budget([sec("Abstract"), sec("1 Intro", 1000)], 32768).needed_ctx is None


def test_needed_ctx_none_when_only_appendices_overflow():
    secs = [sec("1 Intro", 50_000), sec("Appendix A Proofs", 90_000)]
    fit = fit_to_budget(secs, 32768)
    assert fit.body_cut is False and fit.needed_ctx is None


def test_partition_splits_paper_appendix_and_non_paper():
    from research_pipeline.pruning import partition

    secs = [sec("1 Intro"), sec("6 Acknowledgements"), sec("6.1 Grants"), sec("Appendix A Proofs"),
            sec("A.1 Lemma"), sec("2 Method")]
    paper, appendix, dropped = partition(secs)
    assert [s.title for s in paper] == ["1 Intro", "2 Method"]
    assert [s.title for s in appendix] == ["Appendix A Proofs", "A.1 Lemma"]
    assert [(d.title, d.reason, d.chars) for d in dropped] == [
        ("6 Acknowledgements", "non_paper", 118), ("6.1 Grants", "non_paper", 110)]
