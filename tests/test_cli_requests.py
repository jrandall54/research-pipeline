"""`rpipe run` drains the request list; `rpipe requests run|list`; request counts in the health line."""

import json
from datetime import date

import pytest
from conftest import SECTIONS, AlwaysSummary, FakeEmbedder, FakeLLM
from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.cli import Pipeline
from research_pipeline.db import insert_request, mark_request, open_db, upsert_paper
from research_pipeline.llm import LlmLimitReached
from research_pipeline.models import PaperMeta
from research_pipeline.run_lock import run_lock
from research_pipeline.vault import init_vault

runner = CliRunner()
PID = "2611.00001"


class FixtureSource:
    name = "fixture"

    def __init__(self, papers):
        self._papers = papers

    def fetch(self):
        return self._papers


def meta(pid, title="Adversarial robustness"):
    return PaperMeta(id=pid, title=title, abstract="adversarial robustness study", source="arxiv",
                     url=f"https://arxiv.org/abs/{pid}")


@pytest.fixture
def vault(tmp_path):
    path = tmp_path / "vault"
    init_vault(path, topics=["adversarial robustness"])
    (path / "profile.yaml").write_text('topics: ["adversarial robustness"]\nseeds: []\ntop_n: 2\n')
    return path


def install(monkeypatch, llm, papers=()):
    monkeypatch.setattr(
        cli, "build_pipeline",
        lambda config: Pipeline([FixtureSource(list(papers))], FakeEmbedder, lambda ids: [],
                                lambda: llm, lambda pid: SECTIONS),
    )


def queue(vault, pid=PID, status="skipped"):
    """A paper already in the vault (not waiting for the normal summarize stage) and a request for it."""
    conn = open_db(vault / "vault.sqlite")
    upsert_paper(conn, meta(pid, "Old paper"))
    conn.execute("UPDATE papers SET status = ? WHERE id = ?", (status, pid))
    conn.commit()
    rid = insert_request(conn, "summarize", {"paper_id": pid})
    conn.close()
    return rid


def request_row(vault, rid):
    conn = open_db(vault / "vault.sqlite")
    try:
        return dict(conn.execute("SELECT * FROM requests WHERE id = ?", (rid,)).fetchone())
    finally:
        conn.close()


def last_run_counts(vault):
    conn = open_db(vault / "vault.sqlite")
    try:
        return json.loads(conn.execute("SELECT counts FROM runs ORDER BY id DESC").fetchone()[0])
    finally:
        conn.close()


def test_nightly_run_drains_requests(vault, monkeypatch):
    rid = queue(vault)
    install(monkeypatch, AlwaysSummary([]), papers=[meta("2611.00010"), meta("2611.00011")])
    res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert request_row(vault, rid)["status"] == "done"
    counts = last_run_counts(vault)
    assert counts["requests_done"] == 1 and counts["summarized"] == 2  # the normal stage, unchanged
    first = next((vault / "digests").glob("*.md")).read_text().splitlines()[0]
    assert "requests done 1" in first


def test_nightly_run_drains_on_quiet_night(vault, monkeypatch):
    rid = queue(vault)
    llm = AlwaysSummary([])
    install(monkeypatch, llm)  # no new papers
    res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert llm.calls  # the model was used although nothing new came in
    assert request_row(vault, rid)["status"] == "done"
    assert last_run_counts(vault)["requests_done"] == 1


def test_drain_skipped_after_usage_limit_stop(vault, monkeypatch):
    rid = queue(vault)
    llm = FakeLLM([LlmLimitReached("weekly limit")])
    install(monkeypatch, llm, papers=[meta("2611.00010")])
    res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert len(llm.calls) == 1  # the summarize stage's one call; the drain never called
    row = request_row(vault, rid)
    assert row["status"] == "pending" and row["tries"] == 0
    assert last_run_counts(vault)["requests_waiting"] == 1


def test_health_line_shows_request_counts():
    line = cli.health_line(date(2026, 11, 25), {"requests_done": 1, "requests_waiting": 2}, [])
    assert "requests done 1" in line and "requests waiting 2" in line
    assert "requests failed" not in line
    assert "requests" not in cli.health_line(date(2026, 11, 25), {}, [])


def test_requests_list_output(vault):
    res = runner.invoke(cli.app, ["requests", "list", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert res.output.strip() == "No requests."
    first = queue(vault, PID)
    second = queue(vault, "2611.00002")
    conn = open_db(vault / "vault.sqlite")
    mark_request(conn, first, "failed", error="model broke", add_try=True)
    conn.close()
    res = runner.invoke(cli.app, ["requests", "list", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    lines = res.output.strip().splitlines()
    assert len(lines) == 2
    assert lines[0].split()[:5] == [str(second), "pending", "summarize", "2611.00002", "0"]
    assert lines[1].split()[:5] == [str(first), "failed", "summarize", PID, "1"]
    assert lines[1].endswith("model broke") and not lines[0].endswith("model broke")


def test_requests_run_drains(vault, monkeypatch):
    rid = queue(vault)
    install(monkeypatch, AlwaysSummary([]))
    res = runner.invoke(cli.app, ["requests", "run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert "requests_done=1" in res.output
    assert request_row(vault, rid)["status"] == "done"


def test_requests_run_with_nothing_queued(vault, monkeypatch):
    install(monkeypatch, FakeLLM([]))
    res = runner.invoke(cli.app, ["requests", "run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert "No requests waiting" in res.output


def test_requests_run_needs_a_model(vault):
    cfg = vault / "config.yaml"
    cfg.write_text(cfg.read_text() + "\nllm_backend: none\n")
    res = runner.invoke(cli.app, ["requests", "run", "--vault", str(vault)])
    assert res.exit_code == 1


def test_requests_run_takes_lock(vault, monkeypatch):
    queue(vault)
    install(monkeypatch, AlwaysSummary([]))
    with run_lock(vault, "rpipe run"):
        res = runner.invoke(cli.app, ["requests", "run", "--vault", str(vault)])
    assert res.exit_code == 75
    assert "rpipe run is already working" in res.stderr


class OllamaLike(FakeLLM):
    """A scripted model that says it is Ollama, so `rpipe run` goes on to the rerun stage."""

    name = "ollama"


def fake_reruns(monkeypatch):
    """Skip the real mode probe and reruns; record what the rerun stage was told."""
    calls = []
    monkeypatch.setattr(cli, "_summarize_plan", lambda *a, **k: ("whole", 0))

    def reruns(conn, llm, vault, deadline, *, reporter=None, model_timed_out=False):
        calls.append(model_timed_out)
        return {}, []

    monkeypatch.setattr(cli, "run_reruns", reruns)
    return calls


def exhaust(vault, rid):
    """Three runs each died while working on this request."""
    conn = open_db(vault / "vault.sqlite")
    for _ in range(3):
        mark_request(conn, rid, "running", add_try=True)
    conn.close()


def test_quiet_night_with_only_an_exhausted_request_does_not_load_the_model(vault, monkeypatch):
    rid = queue(vault)
    exhaust(vault, rid)
    picked = []
    real_plan = cli._summarize_plan
    monkeypatch.setattr(cli, "_summarize_plan", lambda *a, **k: picked.append(k["has_work"]) or real_plan(*a, **k))
    install(monkeypatch, FakeLLM([]))
    res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert picked == [False]  # nothing runnable: no model load for a reading mode
    assert request_row(vault, rid)["status"] == "failed"
    first = next((vault / "digests").glob("*.md")).read_text().splitlines()[0]
    assert "requests waiting" not in first and "requests failed 1" in first


def test_requests_run_with_only_an_exhausted_request(vault, monkeypatch):
    rid = queue(vault)
    exhaust(vault, rid)
    install(monkeypatch, FakeLLM([]))
    res = runner.invoke(cli.app, ["requests", "run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert "No requests waiting" in res.output


def test_drain_crash_is_recorded_and_reruns_and_digest_still_run(vault, monkeypatch):
    queue(vault)
    calls = fake_reruns(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("database disk image is malformed")

    monkeypatch.setattr(cli, "drain_requests", boom)
    install(monkeypatch, OllamaLike([]))
    res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert calls == [False]  # the rerun stage still ran
    first = next((vault / "digests").glob("*.md")).read_text().splitlines()[0]
    assert "requests: database disk image is malformed" in first
    conn = open_db(vault / "vault.sqlite")
    errors = json.loads(conn.execute("SELECT errors FROM runs").fetchone()[0])
    conn.close()
    assert "requests: database disk image is malformed" in errors


def test_drain_timeout_tells_reruns_the_model_timed_out(vault, monkeypatch):
    from research_pipeline.llm import LlmTimeout

    rid = queue(vault)
    calls = fake_reruns(monkeypatch)
    install(monkeypatch, OllamaLike([LlmTimeout("slow")]))  # no new papers: the drain's call times out
    res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert calls == [True]
    row = request_row(vault, rid)
    assert row["status"] == "pending" and row["tries"] == 1


def test_drain_skipped_after_summarize_timeout(vault, monkeypatch):
    from research_pipeline.llm import LlmTimeout

    rid = queue(vault)
    calls = fake_reruns(monkeypatch)
    llm = OllamaLike([LlmTimeout("slow")])
    install(monkeypatch, llm, papers=[meta("2611.00010")])
    res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert len(llm.calls) == 1  # the summarize stage's call; the drain never called
    row = request_row(vault, rid)
    assert row["status"] == "pending" and row["tries"] == 0
    assert calls == [True]
    assert last_run_counts(vault)["requests_waiting"] == 1
