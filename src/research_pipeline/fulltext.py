"""Fetch a paper's arXiv HTML version and turn it into clean, titled sections.

Paper text is untrusted: a page can hide instructions meant for an AI reader. So
``strip_hidden`` removes everything a human could not see before any text is used.
"""

import re
import time
from collections.abc import Callable

import httpx
from bs4 import BeautifulSoup, Comment, Tag

from research_pipeline.models import Section
from research_pipeline.sources.arxiv import MIN_SPACING, USER_AGENT, retry_delay
from research_pipeline.sources.base import SourceError

HTML_URL = "https://arxiv.org/html/{paper_id}"
MAX_BYTES = 5 * 1024 * 1024
# Same id pattern as digest.py: new-style (2401.00001) or old-style (cs.CR/0701001).
_ARXIV_ID = re.compile(r"^(\d{4}\.\d{4,5}|[a-z-]+(\.[A-Z]{2})?/\d{7})$")

_ALWAYS_REMOVED = ["script", "style", "noscript", "template", "svg"]
# Zero-width characters and the Unicode "tag" block (invisible, can spell hidden text).
_INVISIBLE = re.compile("[​-‏⁠﻿\U000e0000-\U000e007f]")
_WHITE = {"#fff", "#ffffff", "white", "rgb(255,255,255)", "rgba(255,255,255,1)", "transparent"}
_BLOCKS = {
    "p", "div", "section", "article", "header", "footer", "li", "ul", "ol", "dl", "dt",
    "dd", "br", "table", "tr", "td", "th", "blockquote", "pre", "figure", "figcaption",
    "h1", "h2", "h3", "h4", "h5", "h6",
}
_LENGTH = re.compile(r"^(-?\d*\.?\d+)([a-z%]*)$")
# Elements that are never the paper's argument, by class (confirmed on a real arXiv HTML page,
# 2026-11-22): the bibliography, arXiv's "Instructions for reporting errors" block, the site
# header and footer and the page navigation bar. Appendices are NOT listed: they are kept when
# the paper fits (see pruning.py).
_BOILERPLATE_CLASSES = {
    "ltx_bibliography", "keyboard-glossary", "arxiv-html-footer", "ltx_page_navbar",
    "ds-site-header", "ds-site-footer",
}

# Request spacing is module-level so the 3 s gap holds across separate calls.
_clock: Callable[[], float] = time.monotonic
_last_request: float | None = None


class FullTextUnavailable(Exception):
    """The paper has no arXiv HTML version (or is not an arXiv paper)."""


# Pages whose sections hold less text than this in total are treated as "no full text":
# a page with no recognisable headings, or only headings, is not worth summarising.
MIN_FULLTEXT_CHARS = 50


def ensure_usable(sections: list[Section]) -> list[Section]:
    """Return `sections`, or raise FullTextUnavailable if they hold almost no text."""
    if sum(len(s.text.strip()) for s in sections) < MIN_FULLTEXT_CHARS:
        raise FullTextUnavailable("page has no usable section text")
    return sections


def reset_spacing() -> None:
    """Forget the last request time (for tests)."""
    global _last_request
    _last_request = None


def _style_props(style: str) -> dict[str, str]:
    props: dict[str, str] = {}
    for part in style.lower().replace(" ", "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            props[k] = v.replace("!important", "")
    return props


def _px(value: str) -> float | None:
    """A CSS length as a number, with 0 in any unit as 0; None if not understood."""
    m = _LENGTH.match(value)
    if not m:
        return None
    num, unit = float(m.group(1)), m.group(2)
    if num == 0:
        return 0.0
    if unit in ("px", "pt"):
        return num
    if unit in ("em", "rem", "ex", "vw", "vh"):
        return num * 10  # roughly px; so -100em counts as far off-screen
    return None


def _style_hides(style: str) -> bool:
    p = _style_props(style)
    if p.get("display") == "none" or p.get("visibility") == "hidden":
        return True
    opacity = p.get("opacity")
    if opacity is not None:
        try:
            if float(opacity) == 0:
                return True
        except ValueError:
            pass
    size = _px(p.get("font-size", ""))
    if size is not None and size <= 2:
        return True
    color = p.get("color", "")
    if color in _WHITE or re.fullmatch(r"rgba\(255,255,255,(1|1\.0+)\)", color):
        return True
    for prop in ("left", "top", "text-indent"):
        v = _px(p.get(prop, ""))
        if v is not None and v <= -1000:
            return True
    return False


def _is_hidden(el: Tag) -> bool:
    if el.has_attr("hidden") or el.get("aria-hidden", "").strip().lower() == "true":
        return True
    style = el.get("style")
    return bool(style) and _style_hides(style)


def strip_hidden(html: str) -> str:
    """Return the page with everything a human reader could not see removed."""
    soup = BeautifulSoup(html, "html.parser")
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for el in soup.find_all(_ALWAYS_REMOVED):
        el.decompose()
    for el in soup.find_all(True):
        if el.decomposed:  # inside something already removed
            continue
        if _is_hidden(el):
            el.decompose()
    for s in soup.find_all(string=True):
        cleaned = _INVISIBLE.sub("", str(s))
        if cleaned != str(s):
            s.replace_with(cleaned)
    return str(soup)


def _is_heading(tag: Tag) -> bool:
    """h2/h3 start sections; LaTeXML's abstract title is an h6 (or any heading "Abstract")."""
    if tag.name in ("h2", "h3"):
        return True
    if tag.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        classes = tag.get("class") or []
        return "ltx_title_abstract" in classes or _norm(tag.get_text(" ")).lower() == "abstract"
    return False


def _norm(text: str) -> str:
    return " ".join(text.split())


# Layout-only TeX that carries no meaning in plain text.
_TEX_LAYOUT = re.compile(
    r"\\(?:displaystyle|textstyle|scriptstyle|scriptscriptstyle|left|right|"
    r"[bB]ig{1,2}[lr]?|materiaDisplay)(?![A-Za-z])"
)
_TEX_SPACE = re.compile(r"\\(?:qquad|quad)(?![A-Za-z])|\\[,;:! ]")
_MATHML_INVISIBLE = re.compile("[\u2061-\u2064]")  # function application, invisible times, ...
MAX_MATH_CHARS = 160  # a longer formula is cut so one equation cannot swamp the prose
_SHORT_TEX = 30


def _clean_tex(tex: str) -> str:
    tex = _TEX_LAYOUT.sub("", tex)
    tex = _TEX_SPACE.sub(" ", tex)
    return _norm(tex)


def _math_text(m: Tag) -> str:
    """One compact plain-text form of a <math> element (its meaning, exactly once).

    LaTeXML writes each formula as MathML (visible symbols) plus an `<annotation>` with the
    TeX source plus an `alttext` attribute with the same TeX. Taking all the text duplicates it
    and leaks macros, so we choose one: the cleaned `alttext` if it is short and has no
    backslash macro (`x_i`, `p<0.05`), otherwise the MathML symbols (Unicode: `μ`, `∑`, `∈`).
    """
    tex = _clean_tex(m.get("alttext") or "")
    if tex and len(tex) <= _SHORT_TEX and "\\" not in tex:
        text = tex
    else:
        for junk in m.find_all(["annotation", "annotation-xml"]):
            junk.decompose()
        text = _norm(_MATHML_INVISIBLE.sub("", m.get_text("")))
        if not text:
            text = _norm(re.sub(r"\\[A-Za-z]+", "", tex))
    if len(text) > MAX_MATH_CHARS:
        text = text[: MAX_MATH_CHARS - 1].rstrip() + "…"
    return text


def _flatten_math(soup: BeautifulSoup) -> None:
    """Replace every math element by its plain text; drop LaTeXML's failed-macro spans."""
    for err in soup.find_all(class_="ltx_ERROR"):
        err.decompose()  # undefined macro such as \materiaDisplay: typesetting residue
    for m in soup.find_all("math"):
        if m.decomposed:
            continue
        text = _math_text(m)
        block = m.get("display") == "block"
        m.replace_with(f" {text} " if block else text)


def parse_sections(html: str) -> list[Section]:
    """Split visible text into sections; each h2/h3 heading starts a new one."""
    soup = BeautifulSoup(strip_hidden(html), "html.parser")
    _flatten_math(soup)
    for el in soup.find_all(class_=lambda c: c in _BOILERPLATE_CLASSES):
        el.decompose()
    root = soup.body or soup
    sections: list[tuple[str, list[str]]] = []
    current: list[str] | None = None

    def walk(node: Tag) -> None:
        nonlocal current
        for child in node.children:
            if isinstance(child, Tag):
                if _is_heading(child):
                    title = _norm(child.get_text(" "))
                    if title:
                        current = []
                        sections.append((title, current))
                else:
                    block = child.name in _BLOCKS
                    if block and current is not None:
                        current.append(" ")
                    walk(child)
                    if block and current is not None:
                        current.append(" ")
            elif current is not None:
                current.append(str(child))

    walk(root)
    return [Section(title=t, text=_norm("".join(parts))) for t, parts in sections]


def _wait_for_spacing(sleep: Callable[[float], None]) -> None:
    if _last_request is not None:
        gap = MIN_SPACING - (_clock() - _last_request)
        if gap > 0:
            sleep(gap)


def fetch_fulltext(
    paper_id: str,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Section]:
    """Download and parse the arXiv HTML version of a paper.

    Raises FullTextUnavailable for a non-arXiv id or a 404, SourceError for any
    other failure (including a response over 5 MB).
    """
    global _last_request
    if not _ARXIV_ID.match(paper_id):
        raise FullTextUnavailable(f"{paper_id} is not an arXiv id")
    own = client is None
    http = client or httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT})
    attempt = 0
    try:
        while True:
            _wait_for_spacing(sleep)
            wait: float | None = None
            try:
                with http.stream(
                    "GET", HTML_URL.format(paper_id=paper_id), headers={"User-Agent": USER_AGENT},
                    follow_redirects=True,
                ) as resp:
                    if resp.status_code == 404:
                        raise FullTextUnavailable(f"no HTML version of {paper_id}")
                    # A 429/503 means "too many requests": wait and try again a few times.
                    wait = retry_delay(resp.status_code, resp.headers.get("Retry-After"), attempt)
                    if wait is None:
                        resp.raise_for_status()
                        body = bytearray()
                        for chunk in resp.iter_bytes():
                            body.extend(chunk)
                            if len(body) > MAX_BYTES:
                                raise SourceError(f"arXiv HTML for {paper_id} is too large (over 5 MB)")
                        encoding = resp.encoding or "utf-8"
            except httpx.HTTPStatusError as exc:
                limited = " (rate limited)" if exc.response.status_code in (429, 503) else ""
                raise SourceError(f"arXiv HTML returned HTTP {exc.response.status_code}{limited}") from exc
            except httpx.HTTPError as exc:
                raise SourceError(f"arXiv HTML request failed: {exc!r}") from exc
            finally:
                _last_request = _clock()
            if wait is None:
                break
            sleep(wait)
            attempt += 1
    finally:
        if own:
            http.close()
    return parse_sections(bytes(body).decode(encoding, errors="replace"))
