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
