from datetime import date

import pytest
from fastapi.testclient import TestClient

from research_pipeline import db, library_api
from research_pipeline.db import mark_in_digest, open_db, save_summary, set_status, upsert_paper
from research_pipeline.models import PaperMeta, Status
from research_pipeline.summary_models import (
    Claim,
    Critique,
    Diagram,
    DiagramEdge,
    DiagramNode,
    KeyNumber,
    PaperSummary,
    Summary,
)
from research_pipeline.webapp.server import create_app


def _add(conn, pid, title, day, score, reasons=None, abstract="An abstract."):
    upsert_paper(conn, PaperMeta(id=pid, title=title, abstract=abstract, authors=["Ada"],
                                 source="arxiv", url=f"https://arxiv.org/abs/{pid}"))
    set_status(conn, pid, Status.triaged, score=score, reasons=reasons or [])
    conn.commit()
    mark_in_digest(conn, [pid], date.fromisoformat(day))


def _summary(conn, pid):
    c = Claim(text="claim text", sections=["5.1 Efficiency"])
    save_summary(conn, pid, PaperSummary(
        summary=Summary(one_line="the one line", problem=Claim(text="the problem", sections=["1 Intro"]),
                        approach=Claim(text="the approach", sections=["3 Method"]), results=c, limitations=c),
        critique=Critique(strengths=[c], weaknesses=[], questions=[]),
        basis="full_text", unverified=[]), "m")
    conn.commit()


def _request(conn, pid, status):
    rid = library_api.request(conn, "summarize", {"paper_id": pid})
    if status != "pending":
        db.mark_request(conn, rid, status, error="x" if status == "failed" else None)
    conn.commit()


@pytest.fixture
def vault(tmp_path):
    p = tmp_path / "v.sqlite"
    c = open_db(p)
    _add(c, "2611.25001", "Low Paper", "2026-11-25", 0.3)
    _add(c, "2611.25002", "High Paper", "2026-11-25", 0.9,
         ["topic: agentic workflow (0.80)", "HF upvotes: 56"], abstract="x" * 50)
    _summary(c, "2611.25002")
    _add(c, "2611.25003", "Old Paper", "2026-11-24", 0.5)
    yield c, p
    c.close()


@pytest.fixture
def client(vault):
    return TestClient(create_app(vault[1], {"testserver"}))


def test_feed_lists_latest_digest_in_score_order(client):
    html = client.get("/").text
    assert html.index("High Paper") < html.index("Low Paper") and "Old Paper" not in html
    assert "Wednesday, Nov 25" in html


def test_feed_shows_why_and_topic(client):
    html = client.get("/").text
    assert "Why you're seeing this:" in html
    assert "you follow agentic workflow and 56 people upvoted it on Hugging Face." in html
    assert "Agentic workflow" in html and "--topic-" in html


def test_card_read_fields_present_in_markup(client):
    html = client.get("/").text
    assert 'class="read-more"' in html and "the problem" in html and "§3" in html
    assert 'data-depth="skim"' in html and "aria-pressed" in html and "the one line" in html


def test_unsummarized_digest_paper_shows_abstract_and_summarize_button(client):
    html = client.get("/").text
    assert "An abstract." in html and "Summarize this" in html and 'data-action="summarize"' in html


def test_pending_request_shows_coming_tonight(client, vault):
    _request(vault[0], "2611.25001", "pending")
    html = client.get("/").text
    assert "Summary coming tonight" in html and "Summarize this" not in html


def test_failed_request_shows_try_again(client, vault):
    _request(vault[0], "2611.25001", "failed")
    html = client.get("/").text
    assert "Couldn't summarize this one" in html and "Try again" in html


def test_empty_vault_shows_friendly_empty_state(tmp_path):
    c = TestClient(create_app(tmp_path / "e.sqlite", {"testserver"}))
    html = c.get("/").text
    assert "No papers yet" in html and "Your first feed appears after the next nightly run." in html


def test_title_html_is_escaped(client, vault):
    _add(vault[0], "2611.25009", "<script>alert(1)</script>", "2026-11-25", 0.95)
    html = client.get("/").text
    assert "&lt;script&gt;" in html and "<script>alert(1)" not in html


def test_unknown_day_is_404(client):
    assert client.get("/day/2020-01-01").status_code == 404
    assert client.get("/day/garbage").status_code == 404


def test_earlier_days_link_points_to_previous_date(client):
    assert 'href="/day/2026-11-24"' in client.get("/").text
    old = client.get("/day/2026-11-24")
    assert old.status_code == 200 and "Old Paper" in old.text and "Tuesday, Nov 24" in old.text
    assert "Earlier days" not in old.text


def _with_hook(conn, pid, hook=True):
    c = Claim(text="claim text", sections=["5.1 Efficiency"])
    save_summary(conn, pid, PaperSummary(
        summary=Summary(one_line="the one line", problem=c, approach=c, results=c, limitations=c,
                        hook=Claim(text="Robots finish 28% sooner", sections=["5.1 Efficiency"]) if hook else None,
                        key_numbers=[KeyNumber(value="28%", label="less time", sections=["5.1 Efficiency"])]),
        critique=Critique(strengths=[c], weaknesses=[], questions=[]),
        basis="full_text", unverified=[]), "m")
    db.save_diagram(conn, pid, Diagram(
        shape="parts", caption="The parts",
        nodes=[DiagramNode(id="a", label="Planner", sections=["3 Method"]),
               DiagramNode(id="b", label="Worker", sections=["3 Method"])],
        edges=[DiagramEdge(source="a", target="b")]), "m")
    conn.commit()


def test_card_shows_hook_instead_of_one_line(client, vault):
    _with_hook(vault[0], "2611.25002")
    html = client.get("/").text
    assert "Robots finish 28% sooner" in html and "§5.1" in html
    assert "the one line" not in html


def test_card_falls_back_to_one_line(client):
    html = client.get("/").text
    assert "the one line" in html and 'class="one-line hook"' not in html and "key-numbers" not in html


def test_key_numbers_visible_outside_read_more(client, vault):
    _with_hook(vault[0], "2611.25002")
    html = client.get("/").text
    chips = html.index('<ul class="key-numbers">')
    assert "<strong>28%</strong> less time" in html
    assert chips < html.index('class="read-more"')


def test_card_has_no_diagram_paper_page_does(client, vault):
    # Phase 9.2: diagrams only on the paper screen, never in the feed.
    _with_hook(vault[0], "2611.25002")
    assert '<figure class="diagram' not in client.get("/").text
    assert '<figure class="diagram' in client.get("/paper/2611.25002").text
