from datetime import date

import pytest
from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.db import finish_run, open_db, previous_run_unfinished, start_run
from research_pipeline.run_lock import run_lock
from research_pipeline.vault import init_vault

runner = CliRunner()


def test_runs_command_lists_newest_first(tmp_path):
    init_vault(tmp_path / "v", topics=["x"])
    conn = open_db(tmp_path / "v" / "vault.sqlite")
    first, second = start_run(conn), start_run(conn)
    finish_run(conn, first, {"inserted": 1}, [])
    finish_run(conn, second, {"inserted": 2}, ["boom"])
    conn.close()
    res = runner.invoke(cli.app, ["runs", "--vault", str(tmp_path / "v"), "--limit", "5"])
    assert res.exit_code == 0, res.output
    lines = res.output.strip().splitlines()
    assert len(lines) == 2
    assert "new 2" in lines[0] and "errors 1" in lines[0]
    assert "new 1" in lines[1] and "errors 0" in lines[1]


def test_health_line_flags_unfinished_previous_run():
    base = cli.health_line(date(2026, 11, 21), {}, [])
    assert "previous run did not finish" not in base
    flagged = cli.health_line(date(2026, 11, 21), {}, [], previous_unfinished=True)
    assert flagged.endswith("previous run did not finish")


def test_previous_run_unfinished_ignores_the_current_run(tmp_path):
    conn = open_db(tmp_path / "v.sqlite")
    assert previous_run_unfinished(conn, start_run(conn)) is False  # no earlier run
    crashed = start_run(conn)
    current = start_run(conn)
    assert previous_run_unfinished(conn, current) is True
    finish_run(conn, crashed, {}, [])
    assert previous_run_unfinished(conn, current) is False


def test_runs_shows_unfinished_run_as_did_not_finish(tmp_path):
    init_vault(tmp_path / "v", topics=["x"])
    conn = open_db(tmp_path / "v" / "vault.sqlite")
    start_run(conn)
    conn.close()
    res = runner.invoke(cli.app, ["runs", "--vault", str(tmp_path / "v")])
    assert res.exit_code == 0, res.output
    assert "did not finish" in res.output and "no counts" in res.output


def test_runs_with_no_runs_says_so(tmp_path):
    init_vault(tmp_path / "v", topics=["x"])
    res = runner.invoke(cli.app, ["runs", "--vault", str(tmp_path / "v")])
    assert res.exit_code == 0 and "No runs yet" in res.output


def test_runs_rejects_zero_limit(tmp_path):
    init_vault(tmp_path / "v", topics=["x"])
    res = runner.invoke(cli.app, ["runs", "--vault", str(tmp_path / "v"), "--limit", "0"])
    assert res.exit_code == 2


def _count(vault, table):
    conn = open_db(vault / "vault.sqlite")
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


# Every command that does pipeline work, touches the model or GPU, restarts Ollama, or fetches
# into the vault (the "locked" list in docs/guide/running.md), with the fewest arguments it needs.
LOCKED_COMMANDS = [
    ["run"],
    ["collect"],
    ["triage"],
    ["summarize"],
    ["summarize", "--backlog"],
    ["summarize", "2307.15043", "--refetch"],
    ["digest"],
    ["eval", "run"],
    ["model", "fit"],
    ["model", "check"],
    ["model", "bench"],
    ["model", "tune"],
    ["model", "tune", "--undo"],
    ["model", "recommend"],
    ["model", "setup"],
    ["profile", "suggest", "I study fuzzing."],
    ["gold", "add", "2307.15043"],
    ["gold", "suggest"],
    ["gold", "auto"],
    ["gold", "draft", "2307.15043"],
    ["shelf", "sync"],
    ["shelf", "add", "NOTE"],
    ["requests", "run"],
    ["share", "setup"],
    ["share", "publish"],
    ["share", "fetch"],
    ["share", "follow", "alex"],
    ["share", "browse"],
    ["share", "stop"],
]


@pytest.mark.parametrize("args", LOCKED_COMMANDS, ids=" ".join)
def test_locked_commands_stop(tmp_path, monkeypatch, args):
    vault = tmp_path / "v"
    init_vault(vault, topics=["x"])
    note = tmp_path / "idea.md"
    note.write_text("an idea about fuzzing", encoding="utf-8")
    args = [str(note) if a == "NOTE" else a for a in args]

    def forbidden(*a, **k):
        raise AssertionError("reached network, model, embedder or Ollama before the lock")

    # Anything that would leave the process: a missed lock fails loudly instead of exiting 75.
    for target, name in [
        (cli, "build_pipeline"), (cli, "build_llm"), (cli, "make_arxiv"), (cli, "_load_embedder"),
        (cli, "detect_machine"), (cli, "_scan_server"), (cli.ollama_fit, "resolve"),
        (cli.ollama_fit, "cached_fit"), (cli.bench_mod, "run_bench"), (cli.judge, "make_judge"),
        (cli.ollama_tune, "run_tty"), (cli.ollama_tune, "wait_ready"),
        (cli, "_share_runner"), (cli, "_share_http"),
    ]:
        monkeypatch.setattr(target, name, forbidden)
    monkeypatch.setattr(cli, "_is_tty", lambda: True)  # tune and setup refuse to run otherwise
    dropin = tmp_path / "rpipe.conf"
    dropin.write_text("[Service]\n", encoding="utf-8")
    monkeypatch.setattr(cli.ollama_tune, "DROPIN", dropin)  # so `tune --undo` would go on to restart

    papers, runs = _count(vault, "papers"), _count(vault, "runs")
    with run_lock(vault, "rpipe run"):
        result = runner.invoke(cli.app, [*args, "--vault", str(vault)], input="y\n" * 5)
    assert result.exit_code == cli.RUN_LOCKED_EXIT == 75, result.output
    assert "rpipe run is already working" in result.stderr
    assert (_count(vault, "papers"), _count(vault, "runs")) == (papers, runs)
    assert not list((vault / "digests").glob("*.md"))  # `digest` wrote nothing


# ---- sharing stages (Phase 10) ----


def _share_run(tmp_path, monkeypatch, runner_script=None, follow=False):
    import httpx
    from conftest import FakeEmbedder
    from fakes import FakeRunner, done

    from research_pipeline import profile_edit, registry
    from research_pipeline.cli import Pipeline
    from research_pipeline.db import upsert_follow

    monkeypatch.setattr(registry, "_entry_points", lambda group: [])
    vault = tmp_path / "v"
    init_vault(vault, topics=["agents"])
    cfg = vault / "config.yaml"
    cfg.write_text(cfg.read_text().replace("llm_backend: ollama", "llm_backend: none"), encoding="utf-8")
    monkeypatch.setattr(cli, "build_pipeline", lambda c: Pipeline([], FakeEmbedder, lambda ids: [], lambda: None, lambda p: []))
    profile_edit.set_share(vault, enabled=True, display_name="Alex M.", handle="alex")
    (vault / "share" / "rpipe-feed" / ".git").mkdir(parents=True)
    script = {("git", "diff", "--cached"): done(1)}
    script.update(runner_script or {})
    fake = FakeRunner(script)
    monkeypatch.setattr(cli, "_share_runner", lambda: fake)
    base = "https://bo.github.io/rpipe-feed/"
    from research_pipeline import feed_format as ff
    feed = ff.build_files([], display_name="Bo", topics=["x"], active=True, now="2026-11-26T03:00:00Z", base_url=base)

    def handler(request):
        if str(request.url) == base + "feed.json":
            return httpx.Response(200, content=feed["feed.json"])
        return httpx.Response(404)

    monkeypatch.setattr(cli, "_share_http", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    if follow:
        conn = open_db(vault / "vault.sqlite")
        upsert_follow(conn, "github:bo/rpipe-feed", base + "feed.json", "bo", "Bo", ["x"])
        conn.commit()
        conn.close()
    return vault, fake


def _last_run(vault):
    import json

    from research_pipeline.db import list_runs

    conn = open_db(vault / "vault.sqlite")
    try:
        row = list_runs(conn, 1)[0]
        return json.loads(row["counts"]), json.loads(row["errors"])
    finally:
        conn.close()


def test_run_publishes_after_digest(tmp_path, monkeypatch):
    vault, fake = _share_run(tmp_path, monkeypatch)
    result = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert result.exit_code == 0, result.output
    assert list((vault / "digests").glob("*.md"))
    assert ["git", "push"] in fake.calls
    assert (vault / "share" / "rpipe-feed" / "feed.json").exists()


def test_run_push_failure_is_reported_not_fatal(tmp_path, monkeypatch):
    from fakes import done

    vault, _ = _share_run(tmp_path, monkeypatch, {("git", "push"): done(1, "", "remote: Permission denied")})
    result = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert result.exit_code == 0, result.output
    assert list((vault / "digests").glob("*.md"))
    _, errors = _last_run(vault)
    assert any("sharing: push failed, will retry tomorrow" in e and "Permission denied" in e for e in errors)


def test_run_fetch_counts_in_health_line(tmp_path, monkeypatch):
    vault, _ = _share_run(tmp_path, monkeypatch, follow=True)
    result = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert result.exit_code == 0, result.output
    (digest,) = (vault / "digests").glob("*.md")
    assert "followed: 1 feed, 0 new posts" in digest.read_text()
    counts, _ = _last_run(vault)
    assert counts["followed_feeds"] == 1


def test_health_line_followed_pluralises():
    assert "followed: 3 feeds, 1 new post ·" in cli.health_line(
        date(2026, 11, 26), {"followed_feeds": 3, "followed_new": 1}, [])
    assert "followed" not in cli.health_line(date(2026, 11, 26), {}, [])
