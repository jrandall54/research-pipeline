import json
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from research_pipeline import cli, serve
from research_pipeline.db import current_votes, is_saved, list_saved, open_db, upsert_paper
from research_pipeline.models import PaperMeta
from research_pipeline.vault import init_vault

runner = CliRunner()


def _fake(stdout="", returncode=0, raises=None):
    def run(argv):
        if raises:
            raise raises
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")
    return run


BAD = ["", " ", "0", "0.0.0.0", "0.0.0", "::", "::0", "[::]", "[::0]", "0:0:0:0:0:0:0:0",
       "::ffff:0.0.0.0", "192.168.1.5", "8.8.8.8", "example.com", "localhost.",
       "::ffff:127.0.0.1", "::ffff:100.64.0.5", "127.1", "fe80::1%eth0", "::1%lo"]
GOOD = ["localhost", "127.0.0.1", "::1", "100.64.0.5", "100.127.255.254", "fd7a:115c:a1e0::1"]


def test_localhost_case_insensitive():
    assert serve.resolve_host("LOCALHOST", _fake(raises=AssertionError)) == "localhost"


@pytest.mark.parametrize("good", GOOD)
def test_allowlist_accepts(good):
    assert serve.resolve_host(good, _fake(raises=AssertionError)) == good


def test_tailscale_result_is_checked():
    with pytest.raises(serve.ServeError):
        serve.resolve_host(None, _fake("0.0.0.0\n"))


@pytest.mark.parametrize("bad", BAD)
def test_refuses_all_interfaces(bad):
    with pytest.raises(serve.ServeError):
        serve.resolve_host(bad, _fake("100.64.0.5\n"))


def test_defaults_to_tailscale_ip():
    assert serve.resolve_host(None, _fake("100.64.0.5\n")) == "100.64.0.5"
    assert serve.resolve_host("127.0.0.1", _fake(raises=AssertionError)) == "127.0.0.1"


@pytest.mark.parametrize("runner_", [
    _fake(raises=FileNotFoundError()), _fake("", returncode=1), _fake("\n"),
])
def test_tailscale_failure_message(runner_):
    with pytest.raises(serve.ServeError, match="Tailscale not running; use --host 127.0.0.1"):
        serve.tailscale_ip(runner_)


def _vault_db(tmp_path):
    conn = open_db(tmp_path / "v.sqlite")
    upsert_paper(conn, PaperMeta(id="2401.00001", title="T", abstract="A", source="arxiv",
                                 url="http://x", version=1))
    conn.commit()
    return conn


def _run(conn, name, **params):
    sql = serve.build_metadata()["databases"]["vault"]["queries"][name]["sql"]
    conn.execute(sql, params)
    conn.commit()


def test_metadata_write_queries_insert_votes(tmp_path):
    conn = _vault_db(tmp_path)
    queries = serve.build_metadata()["databases"]["vault"]["queries"]
    assert {n for n, q in queries.items() if q.get("write")} == {
        "vote_up", "vote_down", "vote_clear", "save", "unsave"}
    _run(conn, "vote_up", paper_id="2401.00001")
    assert [(r["paper_id"], r["vote"]) for r in current_votes(conn)] == [("2401.00001", 1)]
    ts = conn.execute("SELECT created_at FROM votes").fetchone()[0]
    import re
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", ts)
    _run(conn, "vote_down", paper_id="2401.00001")
    assert current_votes(conn)[0]["vote"] == -1
    _run(conn, "vote_clear", paper_id="2401.00001")
    assert current_votes(conn) == []


def test_unknown_paper_id_is_rejected(tmp_path):
    conn = _vault_db(tmp_path)
    for name in ("vote_up", "vote_down", "vote_clear", "save"):
        _run(conn, name, paper_id="nope")
    assert conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0] == 0
    assert list_saved(conn) == []


def test_save_and_unsave_queries(tmp_path):
    conn = _vault_db(tmp_path)
    _run(conn, "save", paper_id="2401.00001")
    _run(conn, "save", paper_id="2401.00001")  # idempotent
    assert is_saved(conn, "2401.00001") and len(list_saved(conn)) == 1
    _run(conn, "unsave", paper_id="2401.00001")
    assert not is_saved(conn, "2401.00001")


def test_read_queries_run(tmp_path):
    conn = _vault_db(tmp_path)
    queries = serve.build_metadata()["databases"]["vault"]["queries"]
    for name in ("todays_digest", "saved"):
        assert not queries[name].get("write")
        conn.execute(queries[name]["sql"]).fetchall()


def test_command_argv(tmp_path):
    argv = serve.command(Path("/v/vault.sqlite"), "100.64.0.5", 8001, Path("/t/m.json"))
    assert argv == [sys.executable, "-m", "datasette", "serve", "/v/vault.sqlite",
                    "--host", "100.64.0.5", "--port", "8001", "--metadata", "/t/m.json"]
    assert "-i" not in argv and "--immutable" not in argv


def test_cli_missing_datasette(tmp_path, monkeypatch):
    init_vault(tmp_path / "v")
    monkeypatch.setattr(serve, "datasette_available", lambda: False)
    r = runner.invoke(cli.app, ["serve", "--host", "127.0.0.1", "--vault", str(tmp_path / "v")])
    assert r.exit_code == 1 and "uv sync --extra serve" in r.output


def test_cli_serve_cleans_up_metadata(tmp_path, monkeypatch):
    init_vault(tmp_path / "v")
    seen = {}
    monkeypatch.setattr(serve, "datasette_available", lambda: True)

    def fake_exec(argv):
        p = Path(argv[argv.index("--metadata") + 1])
        seen["path"], seen["exists"] = p, p.exists()
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(serve, "run_datasette", fake_exec)
    r = runner.invoke(cli.app, ["serve", "--host", "127.0.0.1", "--vault", str(tmp_path / "v")])
    assert r.exit_code == 0, r.output
    assert seen["exists"] and not seen["path"].exists()
    assert str(tmp_path / "v" / "vault.sqlite") in seen["argv"]


def test_cli_refuses_all_interfaces(tmp_path):
    init_vault(tmp_path / "v")
    r = runner.invoke(cli.app, ["serve", "--host", "0.0.0.0", "--vault", str(tmp_path / "v")])
    assert r.exit_code == 1


def test_metadata_is_json_and_write_flags():
    meta = json.loads(json.dumps(serve.build_metadata()))
    queries = meta["databases"]["vault"]["queries"]
    assert {n for n, q in queries.items() if q.get("write") is True} == {
        "vote_up", "vote_down", "vote_clear", "save", "unsave"}
    assert all("write" not in q for n, q in queries.items() if n in ("todays_digest", "saved"))
    assert all("on_success_message" in queries[n] for n in ("save", "unsave", "vote_up"))


def test_cli_sigterm_cleans_up_and_restores_handler(tmp_path, monkeypatch):
    init_vault(tmp_path / "v")
    monkeypatch.setattr(serve, "datasette_available", lambda: True)
    before = signal.getsignal(signal.SIGTERM)
    seen = {}

    def fake_run(argv):
        seen["path"] = Path(argv[argv.index("--metadata") + 1])
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler) and handler is not before  # our handler is installed
        handler(signal.SIGTERM, None)  # what a real SIGTERM would do; never kills pytest
        return 0

    monkeypatch.setattr(serve, "run_datasette", fake_run)
    try:
        r = runner.invoke(cli.app, ["serve", "--host", "127.0.0.1", "--vault", str(tmp_path / "v")])
        assert r.exit_code == 143
        assert not seen["path"].exists()
        assert signal.getsignal(signal.SIGTERM) == before
    finally:
        signal.signal(signal.SIGTERM, before)
