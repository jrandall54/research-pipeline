import pytest
from fastapi.testclient import TestClient

from research_pipeline.webapp.server import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "vault.sqlite", {"testserver"}))


def test_static_css_served(client):
    assert client.get("/static/app.css").status_code == 200


def test_wrong_host_refused(client):
    assert client.get("/static/app.css", headers={"host": "evil.example"}).status_code == 400


def test_cross_site_post_refused(client):
    r = client.post("/static/app.css", headers={"origin": "http://evil.example"})
    assert r.status_code == 403


def test_same_origin_post_passes_guard(client):  # 404/405 from routing, not 403
    r = client.post("/nothing", headers={"origin": "http://testserver"})
    assert r.status_code in (404, 405)


def test_post_without_origin_or_referer_refused(client):
    assert client.post("/nothing").status_code == 403


def test_manifest_and_fonts_served(client):
    m = client.get("/static/manifest.webmanifest")
    assert m.status_code == 200 and m.json()["display"] == "standalone"
    assert client.get("/static/fonts/inter-latin-400-normal.woff2").status_code == 200
    assert client.get("/static/fonts/OFL.txt").status_code == 200


def test_ipv6_host_and_referer_fallback(tmp_path):
    c = TestClient(create_app(tmp_path / "v.sqlite", {"::1"}))
    assert c.get("/static/app.css", headers={"host": "[::1]:8002"}).status_code == 200
    ok = c.post("/nothing", headers={"host": "[::1]:8002", "referer": "http://[::1]:8002/x"})
    assert ok.status_code == 404
    https = c.post("/nothing", headers={"host": "[::1]:8002", "origin": "https://[::1]:8002"})
    assert https.status_code == 403


def test_base_template_renders_nav_and_escapes():
    from research_pipeline.webapp.server import templates

    html = templates.env.from_string(
        '{% extends "base.html" %}{% block content %}{{ t }}{% endblock %}'
    ).render(t="<script>x</script>", nav="feed")
    assert "&lt;script&gt;" in html
    assert 'href="/saved"' in html and 'href="/search"' in html and "viewport-fit=cover" in html


def test_get_conn_opens_and_closes(tmp_path):
    import sqlite3

    from research_pipeline.webapp.server import get_conn

    app = create_app(tmp_path / "v.sqlite", {"testserver"})
    gen = get_conn(type("R", (), {"app": app})())
    conn = next(gen)
    assert conn.execute("select 1").fetchone()[0] == 1
    gen.close()
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("select 1")
