import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from research_pipeline import library_api
from research_pipeline.db import mark_in_digest, open_db, set_status, upsert_paper
from research_pipeline.models import PaperMeta, Status
from research_pipeline.webapp.server import create_app

ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def vault(tmp_path):
    p = tmp_path / "v.sqlite"
    c = open_db(p)
    upsert_paper(c, PaperMeta(id="2611.25005", title="Gadget Paper", abstract="About gadgets.", authors=["A"],
                              source="arxiv", url="https://arxiv.org/abs/2611.25005"))
    set_status(c, "2611.25005", Status.triaged, score=0.9, reasons=["topic: agents (0.80)"])
    c.commit()
    mark_in_digest(c, ["2611.25005"], date(2026, 11, 25))
    yield c, p
    c.close()


@pytest.fixture
def client(vault):
    return TestClient(create_app(vault[1], {"testserver"}))


def _post(client, path, body=None):
    return client.post(path, json=body, headers=ORIGIN)


def test_vote_up_then_clear(client, vault):
    r = _post(client, "/api/paper/2611.25005/vote", {"direction": "up"})
    assert r.status_code == 200 and r.json() == {"vote": 1}
    assert library_api.get_paper(vault[0], "2611.25005").vote == 1
    assert _post(client, "/api/paper/2611.25005/vote", {"direction": "down"}).json() == {"vote": -1}
    assert _post(client, "/api/paper/2611.25005/vote", {"direction": "clear"}).json() == {"vote": 0}
    assert library_api.get_paper(vault[0], "2611.25005").vote == 0


def test_bad_direction_400(client):
    for body in ({"direction": "sideways"}, {}, None, [1]):
        r = _post(client, "/api/paper/2611.25005/vote", body)
        assert r.status_code == 400 and "error" in r.json()
    r = client.post("/api/paper/2611.25005/vote", content="not json", headers=ORIGIN)
    assert r.status_code == 400


def test_save_and_unsave(client, vault):
    assert _post(client, "/api/paper/2611.25005v2/save", {"saved": True}).json() == {"saved": True}
    assert library_api.get_paper(vault[0], "2611.25005").saved is True
    assert _post(client, "/api/paper/2611.25005/save", {"saved": False}).json() == {"saved": False}
    assert library_api.get_paper(vault[0], "2611.25005").saved is False
    assert _post(client, "/api/paper/2611.25005/save", {"saved": "yes"}).status_code == 400


def test_summarize_adds_request_and_repeat_is_same(client, vault):
    r = _post(client, "/api/paper/2611.25005/summarize")
    assert r.status_code == 200 and r.json() == {"state": "pending"}
    assert vault[0].execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1
    assert _post(client, "/api/paper/2611.25005v2/summarize").json() == {"state": "pending"}
    assert vault[0].execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1


def test_unknown_paper_404_json(client):
    for path, body in (("vote", {"direction": "up"}), ("save", {"saved": True}), ("summarize", None)):
        r = _post(client, f"/api/paper/2611.99999/{path}", body)
        assert r.status_code == 404
        assert r.json() == {"error": "That paper isn't in your library."}
    assert _post(client, "/api/paper/garbage/vote", {"direction": "up"}).status_code == 404


def test_busy_database_503(client, monkeypatch):
    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(library_api, "vote", locked)
    r = _post(client, "/api/paper/2611.25005/vote", {"direction": "up"})
    assert r.status_code == 503
    assert r.json() == {"error": "Your library is busy. Try again in a moment."}


def test_action_without_origin_403(client, vault):
    r = client.post("/api/paper/2611.25005/vote", json={"direction": "up"})
    assert r.status_code == 403
    assert library_api.get_paper(vault[0], "2611.25005").vote == 0


def test_unmatched_api_path_is_json_404_but_pages_stay_html(client):
    r = client.get("/api/nothing")
    assert r.status_code == 404 and r.json() == {"error": "Not found"}
    assert _post(client, "/api/nothing").json() == {"error": "Not found"}
    page = client.get("/paper/2611.99999")
    assert page.status_code == 404 and "text/html" in page.headers["content-type"]


def test_cards_and_paper_page_have_vote_and_save_buttons(client):
    for url in ("/", "/paper/2611.25005"):
        html = client.get(url).text
        assert 'data-action="vote-up"' in html and "More like this" in html
        assert 'data-action="vote-down"' in html and "Less like this" in html
        assert 'data-action="save"' in html


def test_concurrent_requests_share_no_connection_trouble(client):
    from concurrent.futures import ThreadPoolExecutor

    def hit(i):
        if i % 2:
            return client.get("/paper/2611.25005").status_code
        return _post(client, "/api/paper/2611.25005/vote", {"direction": "up" if i % 4 else "clear"}).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(hit, range(80)))
    assert set(codes) == {200}
