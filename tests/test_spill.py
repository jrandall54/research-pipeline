"""Reruns of cut papers with part of the model in RAM (spill.py) and their place in `rpipe run`."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from conftest import SECTIONS, AlwaysSummary, FakeEmbedder, critique, summary
from typer.testing import CliRunner

from research_pipeline import cli, profile_edit, spill
from research_pipeline.bench import Bench, save_bench
from research_pipeline.cli import Pipeline
from research_pipeline.db import (
    get_paper,
    get_provenance,
    get_summary,
    open_db,
    record_failure,
    save_fulltext,
    save_summary,
    upsert_paper,
)
from research_pipeline.llm import CALL_TIMEOUT, LlmError, LlmTimeout, OllamaClient
from research_pipeline.models import PaperMeta
from research_pipeline.progress import ListReporter
from research_pipeline.summarize import TIMEOUT_STOPPED
from research_pipeline.summary_models import PaperSummary
from research_pipeline.vault import init_vault

URL = "http://ollama.test:11434"
MODEL = "testmodel:14b"
FIT = 57344
GIB = 1024**3
START = datetime(2026, 11, 24, 3, 0)


class SpillOllama:
    """A tiny Ollama whose model grows by `kv` bytes per context token; whatever does not fit
    in `vram` free GPU memory sits in RAM (`size_vram < size` in /api/ps)."""

    def __init__(self, base=10 * 10**9, kv=100_000, vram=16 * 10**9, trained=131072):
        self.base, self.kv, self.vram, self.trained = base, kv, vram, trained
        self.loaded: dict[str, int] = {}
        self.others: list[str] = []  # other models listed as loaded
        self.generate_calls: list[dict] = []
        self.squeeze = 0  # bytes of GPU memory lost on a spilled load (a lying estimate)
        self.show_calls = 0

    def show(self, request):
        self.show_calls += 1
        return httpx.Response(200, json={"model_info": {"qwen3.context_length": self.trained},
                                         "capabilities": ["completion"]})

    def generate(self, request):
        body = json.loads(request.content)
        self.generate_calls.append(body)
        if body.get("keep_alive") == 0:
            self.loaded.pop(body["model"], None)
        else:
            self.loaded[body["model"]] = body["options"]["num_ctx"]
        return httpx.Response(200, json={"done": True})

    def ps(self, request):
        out = [{"name": n, "size": 10**9, "size_vram": 10**9} for n in self.others]
        for name, ctx in self.loaded.items():
            size = self.base + self.kv * ctx
            vram = min(size, self.vram)
            if size > self.vram:
                vram -= self.squeeze
            out.append({"name": name, "size": size, "size_vram": vram})
        return httpx.Response(200, json={"models": out})

    def loads_at(self, ctx):
        return [c for c in self.generate_calls if c.get("keep_alive") != 0
                and c["options"]["num_ctx"] == ctx]

    def unloads(self):
        return [c for c in self.generate_calls if c.get("keep_alive") == 0]

    def install(self, mock):
        mock.post(f"{URL}/api/show").mock(side_effect=self.show)
        mock.post(f"{URL}/api/generate").mock(side_effect=self.generate)
        mock.get(f"{URL}/api/ps").mock(side_effect=self.ps)


class Clock:
    def __init__(self, start=START):
        self.t = start

    def __call__(self):
        return self.t


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "s.db")
    yield c
    c.close()


def cut(needed, body_cut=True):
    return PaperSummary(summary=summary(), critique=critique(), basis="full_text",
                        body_cut=body_cut, needed_ctx=needed if body_cut else None, unverified=[])


def add_cut(conn, pid, needed, ran=FIT, body_cut=True, provenance=True, backend="ollama"):
    upsert_paper(conn, PaperMeta(id=pid, title=f"Title {pid}", abstract="An abstract.",
                                 source="arxiv", url=f"https://arxiv.org/abs/{pid}"))
    save_fulltext(conn, pid, SECTIONS)
    kw = dict(prompt_version="v", paper_version=1, num_ctx=ran, backend=backend,
              gpu_share=1.0) if provenance else {}
    save_summary(conn, pid, cut(needed, body_cut), MODEL, **kw)
    conn.commit()


def fake_summarize(clock=None, advance=timedelta(minutes=5), fail=None):
    """Stand-in for summarize_paper: records the context, moves the clock, returns an uncut summary."""
    calls = []

    def run(llm, title, abstract, sections, num_ctx, on_phase=None, **kw):
        calls.append((title, llm.num_ctx, num_ctx, llm.timeout))
        run.kwargs.append(kw)
        if clock is not None:
            clock.t += advance
        if fail is not None and fail(title):
            raise fail(title)
        return cut(None, body_cut=False)

    run.calls = calls
    run.kwargs = []
    return run


def llm():
    return OllamaClient(URL, MODEL, FIT)


def save_test_bench(vault, seconds=60.0, tokens=20000):
    save_bench(vault, Bench(model=MODEL, num_ctx=FIT, read_tps=1000, write_tps=20,
                            seconds_per_paper=seconds, paper_tokens=tokens,
                            measured_at=datetime(2026, 11, 1, tzinfo=UTC)))


# ---- pure helpers ----

def test_candidates_and_buckets(conn):
    add_cut(conn, "2501.00001", 61440)
    add_cut(conn, "2501.00002", 66560)
    add_cut(conn, "2501.00003", 90112)
    add_cut(conn, "2501.00004", 40000, body_cut=False)
    add_cut(conn, "2501.00005", 200000, ran=131072)  # already rerun at the cap
    found = spill.rerun_candidates(conn, 131072)
    assert [(r.paper_id, r.needed_ctx, r.ran_ctx) for r in found] == [
        ("2501.00001", 61440, FIT), ("2501.00002", 66560, FIT), ("2501.00003", 90112, FIT)]
    groups = spill.buckets(found, 131072)
    assert [(ctx, [r.needed_ctx for r in rs]) for ctx, rs in groups] == [
        (65536, [61440]), (73728, [66560]), (90112, [90112])]


def test_bucket_is_capped_at_the_model_cap(conn):
    add_cut(conn, "2501.00001", 40960, ran=16384)
    add_cut(conn, "2501.00002", 50000, ran=16384)
    groups = spill.buckets(spill.rerun_candidates(conn, 32768), 32768)
    assert [(ctx, len(rs)) for ctx, rs in groups] == [(32768, 2)]


def test_candidates_ignore_old_rows(conn):
    add_cut(conn, "2501.00001", 61440, provenance=False)
    add_cut(conn, "2501.00002", None)  # cut, but from before needed_ctx was recorded
    assert spill.rerun_candidates(conn, 131072) == []


def test_cloud_summaries_are_not_candidates(conn):
    add_cut(conn, "2501.00001", 61440, backend="claude-code")
    add_cut(conn, "2501.00002", 61440, backend=None)
    assert spill.rerun_candidates(conn, 131072) == []


def test_ram_part_bytes():
    got = spill.ram_part_bytes(65536, 57344, s_fit=14e9, v_fit=14e9, s_8k=12e9)
    assert got == round((65536 - 57344) * (2e9 / 49152))


# ---- run_reruns ----

@pytest.fixture
def ollama():
    fake = SpillOllama()
    with respx.mock(assert_all_called=False) as mock:
        fake.install(mock)
        yield fake


def run(conn, tmp_path, deadline, clock=None, ram=32.0, reporter=None):
    clock = clock or Clock()
    return spill.run_reruns(conn, llm(), tmp_path, deadline, reporter=reporter,
                            now=clock, ram_free=lambda: ram)


def test_reruns_happy_path(conn, tmp_path, ollama, monkeypatch):
    add_cut(conn, "2501.00001", 61440)
    add_cut(conn, "2501.00002", 63488)
    save_test_bench(tmp_path)
    clock = Clock()
    fake = fake_summarize(clock)
    monkeypatch.setattr(spill, "summarize_paper", fake)
    reporter = ListReporter()
    counts, errors = run(conn, tmp_path, START + timedelta(hours=3), clock, reporter=reporter)
    assert counts == {"rerun": 2, "rerun_waiting": 0}
    assert errors == []
    for pid in ("2501.00001", "2501.00002"):
        assert get_provenance(conn, pid)["num_ctx"] == 65536
        assert get_provenance(conn, pid)["gpu_share"] is not None
        assert get_summary(conn, pid).body_cut is False
    assert len(ollama.loads_at(65536)) == 1
    assert len(ollama.loads_at(8192)) == 1
    assert [c[1] for c in fake.calls] == [65536, 65536]
    assert ollama.unloads() and not ollama.loaded  # unloaded at the end
    steps = [e for e in reporter.events if e.kind == "step"]
    assert [e.outcome for e in steps] == ["done", "done"]
    assert reporter.events[0].stage == "rerun" and reporter.events[0].total == 2


def test_rerun_never_falls_back(conn, tmp_path, ollama, monkeypatch):
    # A rerun exists to read the paper whole: it must never be sent to map-reduce instead.
    add_cut(conn, "2501.00001", 61440)
    save_test_bench(tmp_path)
    fake = fake_summarize(Clock())
    monkeypatch.setattr(spill, "summarize_paper", fake)
    counts, _ = run(conn, tmp_path, START + timedelta(hours=3))
    assert counts["rerun"] == 1
    assert fake.kwargs == [{"fallback_above": None}]


def test_model_cap_is_public(ollama):
    assert spill.model_cap(URL, MODEL) == 131072
    ollama.trained = 65536
    assert spill.model_cap(URL, MODEL) == 65536


def test_rerun_context_is_capped_at_the_trained_context(conn, tmp_path, ollama, monkeypatch):
    ollama.trained = 65536
    add_cut(conn, "2501.00001", 90112)
    add_cut(conn, "2501.00002", 90112, ran=65536)  # already rerun at the cap: not again
    save_test_bench(tmp_path)
    fake = fake_summarize(Clock())
    monkeypatch.setattr(spill, "summarize_paper", fake)
    counts, _ = run(conn, tmp_path, START + timedelta(hours=3))
    assert counts == {"rerun": 1, "rerun_waiting": 0}
    assert [c[1] for c in fake.calls] == [65536]


def test_no_candidates_returns_zeros_without_loading(conn, tmp_path, ollama):
    reporter = ListReporter()
    assert run(conn, tmp_path, START + timedelta(hours=3), reporter=reporter) == (
        {"rerun": 0, "rerun_waiting": 0}, [])
    assert ollama.generate_calls == [] and reporter.events == []


def test_no_reruns_after_deadline(conn, tmp_path, ollama, monkeypatch):
    add_cut(conn, "2501.00001", 61440)
    add_cut(conn, "2501.00002", 63488)
    monkeypatch.setattr(spill, "summarize_paper", fake_summarize())
    reporter = ListReporter()
    counts, errors = run(conn, tmp_path, None, reporter=reporter)
    assert counts == {"rerun": 0, "rerun_waiting": 2}
    assert ollama.generate_calls == [] and ollama.show_calls == 0  # Ollama is not asked anything
    assert len([e for e in reporter.events if e.kind == "note"]) == 1
    assert [e.outcome for e in reporter.events if e.kind == "step"] == ["waiting", "waiting"]


def test_no_reruns_after_the_model_timed_out(conn, tmp_path, ollama, monkeypatch):
    add_cut(conn, "2501.00001", 61440)
    fake = fake_summarize()
    monkeypatch.setattr(spill, "summarize_paper", fake)
    reporter = ListReporter()
    counts, _ = spill.run_reruns(conn, llm(), tmp_path, START + timedelta(hours=3),
                                 reporter=reporter, now=Clock(), ram_free=lambda: 32.0,
                                 model_timed_out=True)
    assert counts == {"rerun": 0, "rerun_waiting": 1}
    assert ollama.generate_calls == [] and fake.calls == []
    assert any("timed out" in e.label for e in reporter.events if e.kind == "note")


def test_loads_never_wait_longer_than_one_call(conn, tmp_path, ollama, monkeypatch):
    add_cut(conn, "2501.00001", 61440)
    save_test_bench(tmp_path)
    monkeypatch.setattr(spill, "summarize_paper", fake_summarize(Clock()))
    timeouts = []
    real = spill._load
    monkeypatch.setattr(spill, "_load", lambda u, m, c, t: timeouts.append(t) or real(u, m, c, t))
    run(conn, tmp_path, START + timedelta(hours=10))
    assert timeouts == [CALL_TIMEOUT] * 3  # normal context, 8192, the bucket


def test_each_call_gets_only_the_time_left(conn, tmp_path, ollama, monkeypatch):
    add_cut(conn, "2501.00001", 61440)
    clock = Clock()
    seen = []

    def slow(llm, title, abstract, sections, num_ctx, on_phase=None, **kw):
        on_phase("summary")
        seen.append(llm.timeout)
        clock.t += timedelta(minutes=30)
        on_phase("critique")
        seen.append(llm.timeout)
        return cut(None, body_cut=False)

    monkeypatch.setattr(spill, "summarize_paper", slow)
    run(conn, tmp_path, START + timedelta(minutes=90), clock)
    assert seen == [90 * 60, 60 * 60]


def test_ram_allowance_is_free_ram_minus_margin_only(conn, tmp_path, ollama, monkeypatch):
    add_cut(conn, "2501.00001", 61440)  # bucket 65536: 0.82 GB estimated, 0.55 GB really in RAM
    add_cut(conn, "2501.00002", 90112)  # bucket 90112: 3.28 GB estimated
    save_test_bench(tmp_path)
    fake = fake_summarize(Clock())
    monkeypatch.setattr(spill, "summarize_paper", fake)
    # 2.9 GiB = 3.11 GB may be used: enough for the first bucket, not the second (the model is
    # unloaded before the second reading, and the reading is still 6.9 GiB).
    counts, _ = run(conn, tmp_path, START + timedelta(hours=3), ram=6.9)
    assert counts == {"rerun": 1, "rerun_waiting": 1}
    assert ollama.loads_at(90112) == []


def test_unexpected_error_keeps_counts_so_far(conn, tmp_path, ollama, monkeypatch):
    add_cut(conn, "2501.00001", 61440)
    add_cut(conn, "2501.00002", 90112)
    save_test_bench(tmp_path)
    monkeypatch.setattr(spill, "summarize_paper", fake_summarize(Clock()))
    real, calls = spill.ram_part_bytes, []

    def flaky(*a):
        calls.append(a)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return real(*a)

    monkeypatch.setattr(spill, "ram_part_bytes", flaky)
    counts, errors = run(conn, tmp_path, START + timedelta(hours=3))
    assert counts == {"rerun": 1, "rerun_waiting": 1}
    assert errors == ["rerun: RuntimeError: boom"]
    assert not ollama.loaded


def test_deadline_stops_midway(conn, tmp_path, ollama, monkeypatch):
    add_cut(conn, "2501.00001", 61440)
    add_cut(conn, "2501.00002", 63488)
    clock = Clock()
    fake = fake_summarize(clock, advance=timedelta(minutes=50))
    monkeypatch.setattr(spill, "summarize_paper", fake)
    reporter = ListReporter()
    counts, _ = run(conn, tmp_path, START + timedelta(minutes=70), clock, reporter=reporter)
    assert counts == {"rerun": 1, "rerun_waiting": 1}
    assert len(fake.calls) == 1
    assert fake.calls[0][3] == 70 * 60  # call timeout = seconds left until the deadline
    assert get_provenance(conn, "2501.00002")["num_ctx"] == FIT  # still the cut summary
    assert [e.outcome for e in reporter.events if e.kind == "step"] == ["done", "waiting"]


def test_first_rerun_needs_the_margin_without_bench(conn, tmp_path, ollama, monkeypatch):
    add_cut(conn, "2501.00001", 61440)
    fake = fake_summarize()
    monkeypatch.setattr(spill, "summarize_paper", fake)
    reporter = ListReporter()
    counts, _ = run(conn, tmp_path, START + timedelta(minutes=59), reporter=reporter)
    assert counts == {"rerun": 0, "rerun_waiting": 1} and fake.calls == []
    assert [e.outcome for e in reporter.events if e.kind == "step"] == ["waiting"]
    assert ollama.generate_calls == []  # nothing loaded when even the first cannot start


def test_bench_estimate_refuses_a_rerun_that_cannot_finish(conn, tmp_path, ollama, monkeypatch):
    add_cut(conn, "2501.00001", 61440)  # 57,440 paper tokens
    save_test_bench(tmp_path, seconds=600.0, tokens=20000)  # 0.03 s/token x 3 = 5,170 s
    fake = fake_summarize()
    monkeypatch.setattr(spill, "summarize_paper", fake)
    counts, _ = run(conn, tmp_path, START + timedelta(minutes=80))
    assert counts == {"rerun": 0, "rerun_waiting": 1} and fake.calls == []


def test_reruns_skip_without_ram_reading(conn, tmp_path, ollama, monkeypatch):
    add_cut(conn, "2501.00001", 61440)
    monkeypatch.setattr(spill, "summarize_paper", fake_summarize())
    counts, _ = run(conn, tmp_path, START + timedelta(hours=3), ram=None)
    assert counts == {"rerun": 0, "rerun_waiting": 1}
    assert ollama.generate_calls == []


def test_reruns_refuse_a_bucket_over_the_ram_allowance(conn, tmp_path, ollama, monkeypatch):
    add_cut(conn, "2501.00001", 61440)  # bucket 65536: 0.55 GB in RAM
    fake = fake_summarize()
    monkeypatch.setattr(spill, "summarize_paper", fake)
    counts, _ = run(conn, tmp_path, START + timedelta(hours=3), ram=4.3)  # 0.3 GiB allowed
    assert counts == {"rerun": 0, "rerun_waiting": 1}
    assert ollama.loads_at(65536) == [] and fake.calls == []


def test_reruns_stop_when_load_exceeds_ram(conn, tmp_path, ollama, monkeypatch):
    add_cut(conn, "2501.00001", 61440)
    add_cut(conn, "2501.00002", 63488)
    ollama.squeeze = 2 * GIB  # the real load puts 2 GiB more in RAM than estimated
    fake = fake_summarize()
    monkeypatch.setattr(spill, "summarize_paper", fake)
    reporter = ListReporter()
    counts, _ = run(conn, tmp_path, START + timedelta(hours=3), ram=6.0, reporter=reporter)
    assert counts == {"rerun": 0, "rerun_waiting": 2}
    assert len(ollama.loads_at(65536)) == 1 and fake.calls == []
    assert ollama.unloads() and not ollama.loaded
    assert any("RAM" in e.label for e in reporter.events if e.kind == "note")


def test_reruns_skip_when_other_model_loaded(conn, tmp_path, ollama, monkeypatch):
    add_cut(conn, "2501.00001", 61440)
    ollama.others = ["other:7b"]
    monkeypatch.setattr(spill, "summarize_paper", fake_summarize())
    reporter = ListReporter()
    counts, _ = run(conn, tmp_path, START + timedelta(hours=3), reporter=reporter)
    assert counts == {"rerun": 0, "rerun_waiting": 1}
    assert ollama.generate_calls == []
    assert any("GPU busy" in e.label for e in reporter.events if e.kind == "note")


def test_reruns_skip_when_fitted_context_is_small(conn, tmp_path, ollama, monkeypatch):
    add_cut(conn, "2501.00001", 16384, ran=8192)
    monkeypatch.setattr(spill, "summarize_paper", fake_summarize())
    counts, _ = spill.run_reruns(conn, OllamaClient(URL, MODEL, 8192), tmp_path,
                                 START + timedelta(hours=3), now=Clock(), ram_free=lambda: 32.0)
    assert counts == {"rerun": 0, "rerun_waiting": 1}
    assert ollama.generate_calls == []


def test_rerun_failure_keeps_old_summary(conn, tmp_path, ollama, monkeypatch):
    add_cut(conn, "2501.00001", 61440)
    add_cut(conn, "2501.00002", 63488)
    record_failure(conn, "2501.00001", "earlier")
    before = get_paper(conn, "2501.00001")["retry_count"]
    save_test_bench(tmp_path)
    fake = fake_summarize(fail=lambda t: LlmError("model broke") if t.endswith("00001") else None)
    monkeypatch.setattr(spill, "summarize_paper", fake)
    counts, errors = run(conn, tmp_path, START + timedelta(hours=3))
    assert counts == {"rerun": 1, "rerun_waiting": 1}
    assert errors == ["rerun 2501.00001: model broke"]
    assert get_provenance(conn, "2501.00001")["num_ctx"] == FIT
    assert get_summary(conn, "2501.00001").body_cut is True
    assert get_paper(conn, "2501.00001")["retry_count"] == before


def test_rerun_timeout_stops_the_rest(conn, tmp_path, ollama, monkeypatch):
    add_cut(conn, "2501.00001", 61440)
    add_cut(conn, "2501.00002", 63488)
    save_test_bench(tmp_path)
    fake = fake_summarize(fail=lambda t: LlmTimeout("timed out"))
    monkeypatch.setattr(spill, "summarize_paper", fake)
    counts, errors = run(conn, tmp_path, START + timedelta(hours=3))
    assert counts == {"rerun": 0, "rerun_waiting": 2}
    assert len(fake.calls) == 1
    assert errors[0] == "rerun 2501.00001: timed out"
    assert not ollama.loaded


def test_ollama_unreachable_leaves_all_waiting(conn, tmp_path, monkeypatch):
    add_cut(conn, "2501.00001", 61440)
    monkeypatch.setattr(spill, "summarize_paper", fake_summarize())
    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="ollama.test").mock(side_effect=httpx.ConnectError("down"))
        counts, errors = run(conn, tmp_path, START + timedelta(hours=3))
    assert counts == {"rerun": 0, "rerun_waiting": 1}
    assert errors and errors[0].startswith("rerun: ")


# ---- the run command ----

runner = CliRunner()


class NamedSummary(AlwaysSummary):
    def __init__(self, name):
        super().__init__([])
        self.name = name


@pytest.fixture
def vault(tmp_path):
    path = tmp_path / "vault"
    init_vault(path, topics=["security evaluation of LLM capability"])
    (path / "profile.yaml").write_text('topics: ["security evaluation of LLM capability"]\nseeds: []\ntop_n: 2\n')
    return path


def install(monkeypatch, backend):
    monkeypatch.setattr(cli, "build_pipeline", lambda config: Pipeline(
        [], FakeEmbedder, lambda ids: [], lambda: NamedSummary(backend), lambda pid: SECTIONS))


def test_cloud_backend_gets_no_reruns(vault, monkeypatch):
    install(monkeypatch, "claude-code")
    called = []
    monkeypatch.setattr(cli, "run_reruns", lambda *a, **k: called.append(a) or ({}, []))
    res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert called == []


def test_ollama_backend_runs_reruns_and_reports_them(vault, monkeypatch):
    install(monkeypatch, "ollama")
    seen = {}

    def fake(conn, llm, vault_path, deadline, **kw):
        seen.update(vault=vault_path, deadline=deadline)
        return {"rerun": 2, "rerun_waiting": 1}, []

    monkeypatch.setattr(cli, "run_reruns", fake)
    res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert seen["vault"] == vault
    first = next((vault / "digests").glob("*.md")).read_text().splitlines()[0]
    assert " · rerun 2 · rerun waiting 1" in first


def test_normal_pass_timeout_tells_reruns_the_model_is_hung(vault, monkeypatch):
    install(monkeypatch, "ollama")
    monkeypatch.setattr(cli, "run_summarize", lambda *a, **k: ({}, [TIMEOUT_STOPPED]))
    seen = {}
    monkeypatch.setattr(cli, "run_reruns",
                        lambda *a, **k: seen.update(k) or ({"rerun_waiting": 1}, []))
    res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert seen["model_timed_out"] is True


def test_rerun_crash_becomes_an_error_line_and_digest_is_written(vault, monkeypatch):
    install(monkeypatch, "ollama")

    def boom(*a, **k):
        raise RuntimeError("kaput")

    monkeypatch.setattr(cli, "run_reruns", boom)
    res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    first = next((vault / "digests").glob("*.md")).read_text().splitlines()[0]
    assert "rerun: kaput" in first


def test_health_line_shows_reruns():
    line = cli.health_line(START.date(), {"rerun": 2, "rerun_waiting": 1}, [])
    assert " · rerun 2 · rerun waiting 1" in line
    plain = cli.health_line(START.date(), {"rerun": 0, "rerun_waiting": 0}, [])
    assert "rerun" not in plain


def test_later_bucket_reads_ram_after_unloading_the_last_load(conn, tmp_path, ollama, monkeypatch):
    add_cut(conn, "2501.00001", 61440)  # bucket 65536: 0.82 GB estimated, 0.55 GB really in RAM
    add_cut(conn, "2501.00002", 69632)  # bucket 73728: 1.64 GB estimated, 1.37 GB really in RAM
    save_test_bench(tmp_path)
    fake = fake_summarize(Clock())
    monkeypatch.setattr(spill, "summarize_paper", fake)

    def ram():  # 5.6 GiB free, less whatever our model holds in RAM right now
        held = sum(max(0, ollama.base + ollama.kv * c - ollama.vram) for c in ollama.loaded.values())
        return 5.6 - held / GIB

    # With bucket 1 still loaded only 1.09 GiB could be spared (< 1.64 GB); after unloading, 1.6 GiB.
    counts, errors = spill.run_reruns(conn, llm(), tmp_path, START + timedelta(hours=3),
                                      now=Clock(), ram_free=ram, sleep=lambda s: None)
    assert errors == []
    assert counts == {"rerun": 2, "rerun_waiting": 0}
    assert len(ollama.loads_at(73728)) == 1
    assert len(ollama.unloads()) == 2 and not ollama.loaded  # between buckets, and at the end


def test_unload_wait_is_bounded(monkeypatch):
    monkeypatch.setattr(spill, "unload", lambda *a: None)
    monkeypatch.setattr(spill, "_ps", lambda url: [{"name": MODEL}])  # never lets go
    slept, notes = [], []
    spill._unload_and_wait(URL, MODEL, notes.append, slept.append)
    assert sum(slept) == spill.UNLOAD_WAIT
    assert notes and "still loaded" in notes[0]


def test_bench_saved_without_tag_is_used(conn, tmp_path, ollama, monkeypatch):
    save_bench(tmp_path, Bench(model="foo", num_ctx=FIT, read_tps=1000, write_tps=20,
                               seconds_per_paper=600.0, paper_tokens=20000,
                               measured_at=datetime(2026, 11, 1, tzinfo=UTC)))
    add_cut(conn, "2501.00001", 61440)  # bench says ~5,170 s; only 2 h left but over the margin
    fake = fake_summarize(Clock())
    monkeypatch.setattr(spill, "summarize_paper", fake)
    client = OllamaClient(URL, "foo:latest", FIT)
    counts, _ = spill.run_reruns(conn, client, tmp_path, START + timedelta(minutes=80),
                                 now=Clock(), ram_free=lambda: 32.0)
    assert counts == {"rerun": 0, "rerun_waiting": 1}  # refused by the bench estimate
    assert fake.calls == []


# ---- summarize mode and fallback_above, wired into run and summarize ----


class ModeOllama(NamedSummary):
    """A fake Ollama client for the mode decision: `share` is what gpu_share() reads after load()."""

    def __init__(self, share=1.0, num_ctx=57_344, load_error=None):
        super().__init__("ollama")
        self.url, self.model_name, self.num_ctx = "http://ollama.test", "m", num_ctx
        self.share, self.load_error, self.loaded = share, load_error, 0

    def load(self):
        self.loaded += 1
        if self.load_error:
            raise self.load_error

    def gpu_share(self):
        return self.share


def install_llm(monkeypatch, llm):
    monkeypatch.setattr(cli, "build_pipeline", lambda config: Pipeline(
        [], FakeEmbedder, lambda ids: [], lambda: llm, lambda pid: SECTIONS))
    monkeypatch.setattr(cli, "run_reruns", lambda *a, **k: ({}, []))
    monkeypatch.setattr(cli, "model_cap", lambda url, model: 40_960)
    seen = {}
    monkeypatch.setattr(cli, "run_summarize",
                        lambda *a, **k: seen.update(k) or ({"summarized": 0}, []))
    return seen


def at_night(monkeypatch):
    """Pretend every deadline is a few hours away, whatever time the test runs at."""
    monkeypatch.setattr(cli.schedule, "deadline_at",
                        lambda now, value: None if value == "off" else START + timedelta(hours=4))


def set_cfg(vault, **values):
    from ruamel.yaml import YAML
    yaml = YAML()
    path = vault / "config.yaml"
    data = yaml.load(path.read_text())
    data.update(values)
    with path.open("w") as f:
        yaml.dump(data, f)


def test_run_passes_mode_and_fallback(vault, monkeypatch):
    at_night(monkeypatch)
    llm = ModeOllama(share=1.0)
    seen = install_llm(monkeypatch, llm)
    res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert seen["mode"] == "whole" and seen["fallback_above"] == 40_960 and llm.loaded == 1
    assert "whole: the model is fully in GPU memory" in res.stderr

    profile_edit.set_night_deadline(vault, "off")
    seen = install_llm(monkeypatch, ModeOllama(share=0.62))
    res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert seen["mode"] == "split" and seen["fallback_above"] == 0
    assert "split: part of the model runs from RAM (GPU share 0.62)" in res.stderr


def test_run_claude_code_reads_whole_with_no_fallback_cap(vault, monkeypatch):
    at_night(monkeypatch)
    llm = NamedSummary("claude-code")
    seen = install_llm(monkeypatch, llm)
    res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert seen["mode"] == "whole" and seen["fallback_above"] == 0


def test_run_set_mode_is_kept_without_loading(vault, monkeypatch):
    at_night(monkeypatch)
    set_cfg(vault, summarize_mode="split")
    llm = ModeOllama(share=1.0)
    seen = install_llm(monkeypatch, llm)
    res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert seen["mode"] == "split" and llm.loaded == 0


def test_run_auto_load_failure_is_whole(vault, monkeypatch):
    at_night(monkeypatch)
    llm = ModeOllama(share=0.0, load_error=LlmError("out of memory"))
    seen = install_llm(monkeypatch, llm)
    res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert seen["mode"] == "whole"
    assert "could not read how much of the model is in GPU memory" in res.stderr
    assert "out of memory" in res.stderr
    assert next((vault / "digests").glob("*.md")).exists()


def test_summarize_uses_deadline_setting_not_the_clock(vault, monkeypatch):
    # The real clock: daytime or night, a set deadline means reruns can happen tonight.
    from research_pipeline.db import open_db, upsert_paper
    conn = open_db(vault / "vault.sqlite")
    upsert_paper(conn, PaperMeta(id="2611.00001", title="T", source="arxiv", url="http://x"))
    conn.commit()
    conn.close()
    seen = install_llm(monkeypatch, ModeOllama(share=0.0))
    monkeypatch.setattr(cli, "_ready_llm", lambda p: p.make_llm())
    res = runner.invoke(cli.app, ["summarize", "2611.00001", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert seen["mode"] == "split" and seen["fallback_above"] == 40_960
    profile_edit.set_night_deadline(vault, "off")
    seen = install_llm(monkeypatch, ModeOllama(share=0.0))
    monkeypatch.setattr(cli, "_ready_llm", lambda p: p.make_llm())
    res = runner.invoke(cli.app, ["summarize", "2611.00001", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert seen["fallback_above"] == 0


def test_daytime_run_with_deadline_set_still_passes_the_cap(vault, monkeypatch):
    # deadline_at says "no reruns in this run" (over 12 hours away), but the deadline is set:
    # a paper cut now is rerun on a later night, so only papers above the cap go to parts.
    monkeypatch.setattr(cli.schedule, "deadline_at", lambda now, value: None)
    seen = install_llm(monkeypatch, ModeOllama(share=1.0))
    res = runner.invoke(cli.app, ["run", "--vault", str(vault)])
    assert res.exit_code == 0, res.output
    assert seen["fallback_above"] == 40_960
