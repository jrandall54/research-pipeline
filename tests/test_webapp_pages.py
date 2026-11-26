import html as htmllib
from datetime import date

import pytest
from fastapi.testclient import TestClient

from research_pipeline import library_api
from research_pipeline.db import mark_in_digest, open_db, save_summary, set_status, upsert_paper
from research_pipeline.models import PaperMeta, Status
from research_pipeline.summary_models import Claim, Critique, PaperSummary, Summary
from research_pipeline.webapp import present
from research_pipeline.webapp.server import create_app


def _add(conn, pid, title, day, score, authors=("Ada",), abstract="An abstract about gadgets."):
    upsert_paper(conn, PaperMeta(id=pid, title=title, abstract=abstract, authors=list(authors),
                                 source="arxiv", url=f"https://arxiv.org/abs/{pid}"))
    set_status(conn, pid, Status.triaged, score=score, reasons=["topic: agents (0.80)"])
    conn.commit()
    mark_in_digest(conn, [pid], date.fromisoformat(day))


def _summarize(conn, pid, **kw):
    c = Claim(text="claim text", sections=["5.1 Efficiency"])
    base = dict(
        summary=Summary(one_line="the one line", problem=Claim(text="the problem", sections=["1 Intro"]),
                        approach=Claim(text="the approach", sections=["3 Method"]), results=c, limitations=c),
        critique=Critique(strengths=[Claim(text="strong point", sections=["4 Experiments"])],
                          weaknesses=[Claim(text="weak point", sections=["6 Limits"])],
                          questions=["Does it scale? [sections: 5.1 Efficiency]", "Why?"]),
        basis="full_text", unverified=[])
    base.update(kw)
    save_summary(conn, pid, PaperSummary(**base), "test-model", prompt_version="p1")
    conn.commit()


@pytest.fixture
def vault(tmp_path):
    p = tmp_path / "v.sqlite"
    c = open_db(p)
    _add(c, "2611.25005", "Gadget Paper", "2026-11-25", 0.9, authors=("A", "B", "C", "D", "E"))
    _add(c, "2611.25006", "Other Same Day", "2026-11-25", 0.5)
    _add(c, "2611.25007", "Different Day", "2026-11-24", 0.5)
    _summarize(c, "2611.25005")
    yield c, p
    c.close()


@pytest.fixture
def client(vault):
    return TestClient(create_app(vault[1], {"testserver"}))


def _detail(conn, pid="2611.25005", provenance=None):
    return present.paper_detail(library_api.get_paper(conn, pid), None, provenance)


def test_paper_page_shows_brief_and_critique_with_tags(client):
    html = client.get("/paper/2611.25005").text
    assert "Gadget Paper" in html and "the one line" in html and "the problem" in html
    assert "strong point" in html and "weak point" in html
    assert "§4" in html and "§6" in html and "A, B, C and 2 others" in html
    assert "Read the paper" in html and "https://arxiv.org/abs/2611.25005" in html


def test_paper_page_critique_headings(client):
    html = client.get("/paper/2611.25005").text
    for h in ("How solid is it?", "Strong points", "Weak points", "Questions to ask"):
        assert h in html
    assert "Strengths" not in html and "Weaknesses" not in html


def test_unsafe_paper_url_is_not_a_link(vault):
    c, p = vault
    c.execute("update papers set url = 'javascript:alert(1)' where id = '2611.25005'")
    c.commit()
    html = TestClient(create_app(p, {"testserver"})).get("/paper/2611.25005").text
    assert "javascript:" not in html and "Read the paper" not in html
    assert present.safe_url("javascript:alert(1)") is None
    assert present.safe_url("https://a.org/x") == "https://a.org/x"
    assert present.safe_url("HTTP://a.org/x") == "HTTP://a.org/x"


def test_paper_page_includes_day_list(client):
    html = client.get("/paper/2611.25005").text
    start = html.index('class="day-list"')
    block = html[start:html.index("</aside>", start)]
    assert "Other Same Day" in block and 'href="/paper/2611.25006"' in block
    assert "Different Day" not in block and 'aria-current="true"' in block


def test_paper_page_accepts_version_suffix(client):
    r = client.get("/paper/2611.25005v2")
    assert r.status_code == 200 and "Gadget Paper" in r.text


def test_unknown_paper_is_404_page(client):
    for path in ("/paper/2611.99999", "/paper/not-an-id"):
        r = client.get(path)
        assert r.status_code == 404 and "That paper isn't in your library." in htmllib.unescape(r.text)
        assert 'href="/saved"' in r.text  # still inside the app frame


def test_about_lists_read_mode_and_unverified(vault):
    conn, _ = vault
    _summarize(conn, "2611.25005", basis="abstract", body_cut=True, parts=3, unverified=["bad claim"])
    d = _detail(conn, provenance=library_api.summary_provenance(conn, "2611.25005"))
    assert "Read from the abstract only" in d.about
    assert "Read in 3 parts" in d.about
    assert "Some of the paper was too long and was cut" in d.about
    assert 'Could not check: "bad claim"' in d.about
    assert any(a.startswith("Summarized by test-model on 20") for a in d.about)


def test_about_full_text_and_no_provenance(vault):
    assert _detail(vault[0]).about == ["Read from the full paper"]


def test_questions_strip_section_suffix(vault):
    assert _detail(vault[0]).questions == ["Does it scale?", "Why?"]


def test_saved_lists_saved_newest_first(client, vault):
    conn, _ = vault
    library_api.save(conn, "2611.25006", True)
    conn.execute("UPDATE saves SET created_at = '2026-01-01T00:00:00' WHERE paper_id = '2611.25006'")
    library_api.save(conn, "2611.25007", True)
    conn.execute("UPDATE saves SET created_at = '2026-02-01T00:00:00' WHERE paper_id = '2611.25007'")
    conn.commit()
    html = client.get("/saved").text
    assert html.index("Different Day") < html.index("Other Same Day") and "Gadget Paper" not in html


def test_saved_empty_message(client):
    assert "Nothing saved yet." in client.get("/saved").text


def test_search_finds_by_title_word(client):
    html = client.get("/search?q=gadget").text
    assert "Gadget Paper" in html and 'href="/paper/2611.25005"' in html


def test_search_empty_query_shows_box_only(client):
    html = client.get("/search").text
    assert 'name="q"' in html and "No papers match" not in html


def test_search_no_match_message_escapes_query(client):
    raw = client.get("/search", params={"q": "<b>zzz</b>"}).text
    assert "&lt;b&gt;zzz&lt;/b&gt;" in raw and "<b>zzz" not in raw
    assert 'No papers match "<b>zzz</b>".' in htmllib.unescape(raw)
