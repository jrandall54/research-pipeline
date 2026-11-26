"""The app's Mine / Following / Mix switch, labelled cards and the paper screen for followed posts."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from research_pipeline.db import (
    mark_in_digest,
    open_db,
    replace_followed_posts,
    set_status,
    upsert_follow,
    upsert_paper,
)
from research_pipeline.models import PaperMeta, Status
from research_pipeline.summary_models import Claim, Critique, PaperSummary, Summary
from research_pipeline.webapp.server import create_app

DAY = "2026-11-25"
OWN, SHARED, FOLLOWED = "2611.25001", "2611.25002", "2611.25003"


def _post(pid, **kw):
    base = dict(
        paper_id=pid, digest_date=DAY, rank=1, title=f"Post {pid}", abstract="A followed abstract.",
        authors=["Ann"], url=f"https://arxiv.org/abs/{pid}", published=None, summary=None,
        reasons=["topic: robotics (0.80)"], provenance=None,
    )
    base.update(kw)
    return base


def _publisher_summary():
    c = Claim(text="theirs", sections=["1 Intro"])
    s = PaperSummary(summary=Summary(one_line="publisher one line", problem=c, approach=c, results=c, limitations=c),
                     critique=Critique(strengths=[], weaknesses=[], questions=[]), basis="abstract", unverified=[])
    return s.model_dump(mode="json")


def _own(conn, pid, title):
    upsert_paper(conn, PaperMeta(id=pid, title=title, abstract="mine", source="arxiv", url=f"https://arxiv.org/abs/{pid}"))
    set_status(conn, pid, Status.triaged, score=0.9, reasons=["topic: security (0.9)"])
    conn.commit()
    mark_in_digest(conn, [pid], date.fromisoformat(DAY))


def _follow(conn, posts):
    upsert_follow(conn, "fa", "http://h/fa", "@alex", "Alex M.", ["robotics"])
    replace_followed_posts(conn, "fa", posts)
    conn.commit()


@pytest.fixture
def vault(tmp_path):
    p = tmp_path / "v.sqlite"
    c = open_db(p)
    _own(c, OWN, "My Paper")
    _own(c, SHARED, "Shared Paper")
    yield c, p
    c.close()


def _client(vault):
    return TestClient(create_app(vault[1], {"testserver"}))


def test_switch_hidden_without_follows(vault):
    c = _client(vault)
    for url in ("/", "/?view=following", "/?view=mix", f"/paper/{OWN}"):
        assert "data-view-set" not in c.get(url).text
    assert "My Paper" in c.get("/?view=following").text  # unknown choice falls back to Mine


def test_switch_shown_and_mine_unlabelled(vault):
    _follow(vault[0], [_post(FOLLOWED)])
    html = _client(vault).get("/").text
    assert 'data-view-set="following"' in html and "Post 2611.25003" not in html
    assert "You ·" not in html and "My Paper" in html


def test_following_view_lists_followed_posts_with_label(vault):
    _follow(vault[0], [_post(FOLLOWED)])
    html = _client(vault).get("/?view=following").text
    assert "Alex M. · Robotics" in html and "Post 2611.25003" in html and "My Paper" not in html
    assert "Alex M.&#39;s pipeline picked it because they follow robotics." in html
    assert "Why you're seeing this" not in html


def test_mix_view_shows_you_and_feed_label(vault):
    _follow(vault[0], [_post(SHARED), _post(FOLLOWED, rank=2)])
    html = _client(vault).get("/?view=mix").text
    assert "You · Alex M. · Security" in html          # yours and also posted by Alex
    assert "You · Security" in html                      # yours alone
    assert "Alex M. · Robotics" in html                  # theirs alone
    assert html.count("Post 2611.25002") == 0 and html.count("Shared Paper") >= 1


def test_earlier_days_and_day_route_keep_view(vault):
    _follow(vault[0], [_post(FOLLOWED), _post("2611.24001", digest_date="2026-11-24")])
    c = _client(vault)
    assert 'href="/day/2026-11-24?view=following"' in c.get("/?view=following").text
    assert "Post 2611.24001" in c.get("/day/2026-11-24?view=following").text
    assert c.get("/day/2026-11-24").status_code == 404  # not one of your own digest days


def test_followed_title_is_escaped(vault):
    _follow(vault[0], [_post(FOLLOWED, title="<script>x</script>")])
    c = _client(vault)
    for url in ("/?view=following", f"/paper/{FOLLOWED}"):
        html = c.get(url).text
        assert "&lt;script&gt;" in html and "<script>x" not in html


def test_followed_unsafe_link_not_linked(vault):
    _follow(vault[0], [_post(FOLLOWED, url="javascript:alert(1)")])
    html = _client(vault).get(f"/paper/{FOLLOWED}").text
    assert "javascript:" not in html and "Read the paper" not in html


def test_paper_page_for_followed_post(vault):
    _follow(vault[0], [_post(FOLLOWED, summary=_publisher_summary())])
    html = _client(vault).get(f"/paper/{FOLLOWED}?view=following").text
    assert "publisher one line" in html and "Alex M. · Robotics" in html
    assert "Alex M.&#39;s pipeline picked it because" in html
    assert 'href="/day/2026-11-25?view=following"' in html and "Summarize" not in html


def test_own_summary_beats_followed_post(vault):
    from research_pipeline.db import save_summary
    s = PaperSummary.model_validate(_publisher_summary())
    save_summary(vault[0], OWN, s.model_copy(update={}), "m")
    vault[0].commit()
    _follow(vault[0], [_post(OWN, summary=_publisher_summary())])
    html = _client(vault).get(f"/paper/{OWN}").text
    assert "pipeline picked it because" not in html and "Why you're seeing this" in html


def test_no_summarize_button_on_followed(vault):
    _follow(vault[0], [_post(FOLLOWED)])
    html = _client(vault).get("/?view=following").text
    assert "Post 2611.25003" in html and "Summarize this" not in html and 'data-action="summarize"' not in html
    assert "Summarize this" in _client(vault).get("/").text  # your own cards still offer it


def test_vote_on_followed_post_via_api(vault):
    _follow(vault[0], [_post(FOLLOWED, summary=_publisher_summary())])
    c = _client(vault)
    r = c.post(f"/api/paper/{FOLLOWED}/vote", json={"direction": "up"}, headers={"Origin": "http://testserver"})
    assert r.status_code == 200 and r.json() == {"vote": 1}
    assert vault[0].execute("SELECT 1 FROM papers WHERE id = ?", (FOLLOWED,)).fetchone()
    r = c.post(f"/api/paper/{FOLLOWED}/save", json={"saved": True}, headers={"Origin": "http://testserver"})
    assert r.status_code == 200
    assert "publisher one line" in c.get("/saved").text  # copied-in paper shows the publisher's summary


def test_own_paper_without_summary_keeps_own_card_and_no_label(vault):
    """I7: a followed post of the same id never takes over your own paper (paper screen, Saved)."""
    _follow(vault[0], [_post(OWN, summary=_publisher_summary())])
    c = _client(vault)
    html = c.get(f"/paper/{OWN}").text
    assert "publisher one line" not in html and "Alex M." not in html
    assert "Summarize this" in html and 'view=following' not in html
    c.post(f"/api/paper/{OWN}/save", json={"saved": True}, headers={"Origin": "http://testserver"})
    assert "publisher one line" not in c.get("/saved").text
