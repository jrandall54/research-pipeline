from pathlib import Path

import httpx
import pytest
import respx

from research_pipeline import fulltext
from research_pipeline.fulltext import (
    FullTextUnavailable,
    fetch_fulltext,
    parse_sections,
    strip_hidden,
)
from research_pipeline.sources.base import SourceError

HTML = (Path(__file__).parent / "fixtures" / "arxiv_paper.html").read_text(encoding="utf-8")
URL = "https://arxiv.org/html/2401.00001"


@pytest.fixture(autouse=True)
def fresh_spacing(monkeypatch):
    """Each test starts with no previous request and a controllable clock."""
    fulltext.reset_spacing()
    now = [1000.0]
    monkeypatch.setattr(fulltext, "_clock", lambda: now[0])
    return now


def _without_visible_zw_trap(text: str) -> str:
    # INJECTED-9 is zero-width-laced but otherwise visible text: stripping the invisible
    # characters is the fix, and the words a human would read ("INJECTED-9") remain.
    return text.replace("INJECTED-9", "")


def test_strip_hidden_removes_every_trap():
    out = strip_hidden(HTML)
    assert "INJECTED" not in _without_visible_zw_trap(out)
    assert "INJECTED-10" not in out  # Unicode-tag-character trap vanishes entirely
    # invisible characters are deleted from the text that remains
    assert "\u200b" not in out and "\u2060" not in out
    assert not any(0xE0000 <= ord(c) <= 0xE007F for c in out)
    assert "visible things" in out
    assert "Method details" in out


def test_parse_sections_titles_and_text():
    sections = parse_sections(HTML)
    assert [s.title for s in sections] == [
        "Abstract", "1 Introduction", "2 Method", "3 Results", "References",
    ]
    assert sections[0].text == "We study visible things. Nothing here is hidden."
    assert sections[1].text == "Intro text with newlines."
    assert all("INJECTED" not in _without_visible_zw_trap(s.text) for s in sections)


@respx.mock
def test_fetch_returns_sections():
    respx.get(URL).respond(200, text=HTML)
    assert fetch_fulltext("2401.00001", sleep=lambda s: None)[0].title == "Abstract"


@respx.mock
def test_fetch_404_raises_unavailable():
    respx.get(URL).respond(404)
    with pytest.raises(FullTextUnavailable):
        fetch_fulltext("2401.00001", sleep=lambda s: None)


@respx.mock
def test_fetch_non_arxiv_id_makes_no_request():
    route = respx.get(url__regex=r".*").respond(200, text=HTML)
    with pytest.raises(FullTextUnavailable):
        fetch_fulltext("10.1234/not-arxiv", sleep=lambda s: None)
    assert not route.called


@respx.mock
def test_fetch_500_raises_sourceerror():
    respx.get(URL).respond(500)
    with pytest.raises(SourceError):
        fetch_fulltext("2401.00001", sleep=lambda s: None)


@respx.mock
def test_fetch_network_error_raises_sourceerror():
    respx.get(URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(SourceError):
        fetch_fulltext("2401.00001", sleep=lambda s: None)


@respx.mock
def test_fetch_respects_spacing(fresh_spacing):
    respx.get(URL).respond(200, text=HTML)
    sleeps: list[float] = []
    fetch_fulltext("2401.00001", sleep=sleeps.append)
    assert sleeps == []  # first call never waits
    fresh_spacing[0] += 1.0
    fetch_fulltext("2401.00001", sleep=sleeps.append)
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 3.0
    assert sleeps[0] == pytest.approx(2.0)


@respx.mock
def test_fetch_rejects_oversized_response():
    respx.get(URL).respond(200, content=b"x" * (5 * 1024 * 1024 + 1))
    with pytest.raises(SourceError, match="too large"):
        fetch_fulltext("2401.00001", sleep=lambda s: None)


@pytest.mark.parametrize(
    "style",
    [
        "display:none!important",
        "display: none ! important",
        "color:#fff!important",
        "color:rgba(255,255,255,1)",
        "color:transparent",
        "position:absolute;left:-9999px!important",
        "position:absolute;left:-9999em",
    ],
)
def test_strip_hidden_handles_evasive_styles(style):
    out = strip_hidden(f'<body><p>ok</p><span style="{style}">INJECTED-X</span></body>')
    assert "INJECTED" not in out and "ok" in out


def test_parse_sections_recognises_latexml_abstract_h6():
    html = (
        '<body><div class="ltx_abstract"><h6 class="ltx_title ltx_title_abstract">'
        "Abstract</h6><p>Short abstract.</p></div>"
        "<section><h2>1 Intro</h2><p>Body.</p></section></body>"
    )
    secs = parse_sections(html)
    assert [s.title for s in secs] == ["Abstract", "1 Intro"]
    assert secs[0].text == "Short abstract."


def test_parse_sections_inline_markup_does_not_split_words():
    html = (
        "<body><h2>1 Intro</h2><p><em>foo</em>, and <span>H</span>ello world.</p>"
        "<p>Next para.</p></body>"
    )
    assert parse_sections(html)[0].text == "foo, and Hello world. Next para."


def test_ensure_usable_rejects_empty_and_thin_pages():
    from research_pipeline.fulltext import FullTextUnavailable, ensure_usable
    from research_pipeline.models import Section

    with pytest.raises(FullTextUnavailable):
        ensure_usable([])
    with pytest.raises(FullTextUnavailable):
        ensure_usable([Section(title="1 Intro", text="  tiny ")])
    ok = [Section(title="1 Intro", text="x" * 60)]
    assert ensure_usable(ok) == ok


LATEXML = (Path(__file__).parent / "fixtures" / "arxiv_latexml.html").read_text(encoding="utf-8")


def test_latexml_headings_are_clean_and_keep_numbers():
    titles = [s.title for s in parse_sections(LATEXML)]
    assert titles == ["Abstract", "1 Introduction", "2 Method ℝd"]
    assert not any("\\" in t for t in titles)
    assert not any("materiaDisplay" in t for t in titles)


def test_latexml_math_is_plain_text_exactly_once():
    secs = {s.title: s.text for s in parse_sections(LATEXML)}
    body = " ".join(secs.values())
    assert "\\" not in body  # no macro soup, no \displaystyle / \qquad / \materiaDisplay
    assert "Let x_{i} be the inputs" in secs["1 Introduction"]  # short alttext kept, once
    assert "x_{i}x_{i}" not in body and body.count("x_{i}") == 1
    assert "p<0.05 in every run" in body  # entity-decoded, not duplicated
    assert "We study α-stable models." in secs["Abstract"]  # \alpha -> its MathML symbol
    # long formula: MathML symbols, annotation text not repeated
    assert "ℒ(θ)=" in body and "\\mathcal" not in body
    assert "μ=" in body and "∑" in body


def test_latexml_prose_survives():
    body = " ".join(s.text for s in parse_sections(LATEXML))
    assert "We train a real classifier." in body
    assert "We fit a linear probe." in body


def test_long_math_is_cut():
    long_tex = "+".join(f"a{i}" for i in range(200))
    html = (
        "<h2>1 Intro</h2><p>Before <math alttext=\"" + long_tex + "\" display=\"block\"><semantics><mi>"
        + long_tex + "</mi></semantics></math> after prose.</p>"
    )
    text = parse_sections(html)[0].text
    assert len(text) < fulltext.MAX_MATH_CHARS + 40 and "after prose." in text


BOILERPLATE = (Path(__file__).parent / "fixtures" / "arxiv_boilerplate.html").read_text(encoding="utf-8")


def test_bibliography_and_page_chrome_are_not_sections():
    titles = [s.title for s in parse_sections(BOILERPLATE)]
    assert titles == ["1 Introduction", "Appendix A Details"]


def test_appendix_markup_is_kept_for_the_fit_stage():
    secs = parse_sections(BOILERPLATE)
    assert "Extra training details" in secs[-1].text
