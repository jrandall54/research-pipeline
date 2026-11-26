"""The People page, the Follow button and the Mix ticks."""

import pytest
from fastapi.testclient import TestClient

from research_pipeline import library_api
from research_pipeline.db import open_db, replace_directory, upsert_follow
from research_pipeline.webapp.server import create_app

ORIGIN = {"origin": "http://testserver"}


def _entry(login, name, topics, **kw):
    base = {"feed_id": f"github:{login}/rpipe-feed", "feed_url": f"https://{login}.github.io/rpipe-feed/feed.json",
            "handle": login, "display_name": name, "topics": topics, "posts_per_day": 3.2,
            "preview": [{"title": f"Paper by {name}", "one_line": "A short line."}], "checked_at": "2026-11-25T00:00:00Z"}
    base.update(kw)
    return base


@pytest.fixture
def vault(tmp_path):
    p = tmp_path / "v.sqlite"
    c = open_db(p)
    replace_directory(c, [_entry("zed", "Zed Z.", ["cooking"]), _entry("alex", "Alex M.", ["agents", "security"])])
    c.commit()
    yield c, p
    c.close()


def _client(vault, **kw):
    return TestClient(create_app(vault[1], {"testserver"}, **kw))


def test_people_page_lists_directory_sorted(vault):
    html = _client(vault, my_topics=["agents", "security"]).get("/people").text
    assert html.index("Alex M.") < html.index("Zed Z.")
    assert "shares 2 of your topics" in html and "About 3.2 papers a day" in html
    assert "Recently in Alex M." in html and "Paper by Alex M." in html


def test_people_page_shows_setup_command_when_private(vault):
    html = _client(vault).get("/people").text
    assert "Your feed is private" in html and "rpipe share setup" in html
    published = {"handle": "me", "display_name": "Me M.", "address": "https://me.github.io/rpipe-feed/feed.json"}
    html = _client(vault, share=published).get("/people").text
    assert "https://me.github.io/rpipe-feed/feed.json" in html and "rpipe share stop" in html
    assert "Your feed is private" not in html


def test_follow_button_records_follow(vault):
    c = _client(vault)
    r = c.post("/api/feed/alex/follow", headers=ORIGIN)
    assert r.status_code == 200 and r.json() == {"following": True}
    assert [f.handle for f in library_api.follows(vault[0])] == ["alex"]
    assert "Following · posts arrive tonight" in c.get("/people").text
    r = c.post("/api/feed/nobody/follow", headers=ORIGIN)
    assert r.status_code == 404 and "error" in r.json()


def test_follow_cross_site_refused(vault):
    r = _client(vault).post("/api/feed/alex/follow", headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    assert library_api.follows(vault[0]) == []


def test_mix_tick_sets_in_mix(vault):
    upsert_follow(vault[0], "github:alex/rpipe-feed", "u", "alex", "Alex M.", ["agents"])
    vault[0].commit()
    c = _client(vault)
    assert c.post("/api/feed/Alex/mix", json={"on": False}, headers=ORIGIN).json() == {"in_mix": False}
    assert library_api.follows(vault[0])[0].in_mix is False
    assert c.post("/api/feed/alex/mix", json={"on": True}, headers=ORIGIN).json() == {"in_mix": True}
    assert library_api.follows(vault[0])[0].in_mix is True
    assert c.post("/api/feed/alex/mix", json={"on": "yes"}, headers=ORIGIN).status_code == 400
    assert c.post("/api/feed/nobody/mix", json={"on": True}, headers=ORIGIN).status_code == 404


def test_following_section_shows_stopped_and_tick(vault):
    upsert_follow(vault[0], "github:alex/rpipe-feed", "u", "alex", "Alex M.", ["agents"])
    vault[0].execute("UPDATE follows SET active = 0")
    vault[0].commit()
    assert library_api.follows(vault[0])[0].stopped
    html = _client(vault).get("/people").text
    assert 'data-mix="alex"' in html and "Alex M. stopped publishing" in html


def test_mix_view_shows_the_showing_ticks(vault):
    upsert_follow(vault[0], "github:alex/rpipe-feed", "u", "alex", "Alex M.", ["agents"])
    vault[0].commit()
    c = _client(vault)
    assert 'data-mix="alex"' in c.get("/?view=mix").text
    assert 'data-mix="alex"' not in c.get("/?view=following").text


def test_nav_has_people_link(vault):
    html = _client(vault).get("/people").text
    assert 'href="/people" aria-current="page"' in html
    assert 'href="/people"' in _client(vault).get("/search").text


def test_own_feed_cannot_be_followed_and_shows_you(vault):
    me = {"handle": "alex", "display_name": "Alex M.", "address": "https://alex.github.io/rpipe-feed/feed.json"}
    c = _client(vault, share=me)
    r = c.post("/api/feed/Alex/follow", headers=ORIGIN)
    assert r.status_code == 400 and "own feed" in r.json()["error"]
    assert library_api.follows(vault[0]) == []
    html = c.get("/people").text
    assert 'data-follow="alex"' not in html and ">You<" in html and 'data-follow="zed"' in html
