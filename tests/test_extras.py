from conftest import critique, summary

from research_pipeline.extras import check_extras
from research_pipeline.models import Section
from research_pipeline.summary_models import Claim, KeyNumber, PaperSummary

SECS = [
    Section(title="5 Results", text="Latency fell by 28% on average."),
    Section(title="5.1 Speed", text="The method cut latency by 28%."),
]
ABSTRACT = "We cut latency by 28%."


def make(hook=None, key_numbers=()):
    s = summary().model_copy(update={"hook": hook, "key_numbers": list(key_numbers)})
    return PaperSummary(summary=s, critique=critique(), basis="full_text", unverified=[])


def test_hook_with_found_number_is_kept():
    hook = Claim(text="Cuts latency by 28%.", sections=["5.1 Speed"])
    out = check_extras(make(hook), SECS, ABSTRACT)
    assert out.summary.hook == hook and out.checks == []


def test_hook_with_missing_number_falls_back_with_reason():
    hook = Claim(text="2.5x faster.", sections=["5 Results"])
    out = check_extras(make(hook), SECS, ABSTRACT)
    assert out.summary.hook is None
    assert out.checks == ["hook: 2.5x not found in §5"]


def test_hook_with_unknown_section_falls_back():
    hook = Claim(text="Cuts latency by 28%.", sections=["9 Nowhere"])
    out = check_extras(make(hook), SECS, ABSTRACT)
    assert out.summary.hook is None
    assert out.checks == ["hook: cites a section the paper does not have"]


def test_hook_without_numbers_is_kept():
    hook = Claim(text="Twice as fast.", sections=["5 Results"])
    assert check_extras(make(hook), SECS, ABSTRACT).summary.hook == hook


def test_hook_citing_abstract_checks_against_abstract():
    hook = Claim(text="Cuts latency by 28%.", sections=["Abstract"])
    out = check_extras(make(hook), SECS, ABSTRACT)  # no Abstract section stored
    assert out.summary.hook == hook
    bad = Claim(text="Cuts latency by 40%.", sections=["Abstract"])
    out = check_extras(make(bad), SECS, ABSTRACT)
    assert out.summary.hook is None and out.checks == ["hook: 40% not found in Abstract"]


def test_key_numbers_dropped_one_by_one():
    ks = [KeyNumber(value="28%", label="latency cut", sections=["5.1 Speed"]),
          KeyNumber(value="99%", label="accuracy", sections=["5 Results"]),
          KeyNumber(value="28%", label="again", sections=["5 Results"])]
    out = check_extras(make(key_numbers=ks), SECS, ABSTRACT)
    assert out.summary.key_numbers == [ks[0], ks[2]]
    assert out.checks == ["key number 99%: 99% not found in §5"]


def test_key_number_without_a_number_fails():
    ks = [KeyNumber(value="many", label="x", sections=["5 Results"])]
    out = check_extras(make(key_numbers=ks), SECS, ABSTRACT)
    assert out.summary.key_numbers == [] and out.checks == ["key number many: not a number"]


def test_missing_hook_is_not_a_failure():
    out = check_extras(make(None), SECS, ABSTRACT)
    assert out.summary.hook is None and out.checks == []


def test_key_numbers_capped_at_three():
    kns = [KeyNumber(value="28%", label=f"l{i}", sections=["5 Results"]) for i in range(5)]
    out = check_extras(make(None, kns), SECS, ABSTRACT)
    assert len(out.summary.key_numbers) == 3 and out.checks == []
