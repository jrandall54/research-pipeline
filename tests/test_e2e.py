from datetime import date
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import FakeEmbedder, FakeLLM
from conftest import SECTIONS, critique, summary
from research_pipeline.llm import LlmError
from research_pipeline import cli
from research_pipeline.cli import Pipeline
from research_pipeline.models import PaperMeta
from research_pipeline.sources.arxiv import parse_arxiv_atom
from research_pipeline.sources.base import SourceError
from research_pipeline.sources.hf_daily import parse_hf_daily
from research_pipeline.vault import init_vault

FIXTURES = Path(__file__).parent / "fixtures"
runner = CliRunner()


class FixtureSource:
    def __init__(self, name, papers=None, error=None):
        self.name, self._papers, self._error = name, papers or [], error

    def fetch(self):
        if self._error:
            raise self._error
        return self._papers


def seed_fetcher(ids):
    return [
        PaperMeta(id=i, title="Assessing the Evolution of LLM Capability in Software Vulnerability Research", abstract="SoK on LLM vulnerability research",
                  source="arxiv", url=f"https://arxiv.org/abs/{i}")
        for i in ids
    ]


@pytest.fixture
def vault(tmp_path):
    path = tmp_path / "vault"
    init_vault(path, topics=["security evaluation of LLM capability"])
    (path / "profile.yaml").write_text(
        'topics: ["security evaluation of LLM capability"]\nseeds: ["9999.00001"]\ntop_n: 2\n'
    )
    return path


def fetch_text(pid):
    return SECTIONS


class AlwaysSummary(FakeLLM):
    """Answers every request with a valid summary or critique, whatever the order."""

    def generate(self, system, user, schema):
        self.calls.append((system, user))
        return summary() if schema.__name__ == "Summary" else critique()


def make_llm():
    return AlwaysSummary([])


def install(monkeypatch, sources=None, fetcher=seed_fetcher, llm=make_llm):
    arxiv = parse_arxiv_atom((FIXTURES / "arxiv_atom.xml").read_text())
    hf = parse_hf_daily(json.loads((FIXTURES / "hf_daily.json").read_text()))
    srcs = sources if sources is not None else [FixtureSource("arxiv", arxiv), FixtureSource("hf_daily", hf)]
    monkeypatch.setattr(
        cli, "build_pipeline", lambda config: Pipeline(srcs, FakeEmbedder, fetcher, llm, fetch_text)
    )


def run_cli(*args):
    return runner.invoke(cli.app, list(args))


def test_e2e_offline_run_produces_digest(vault, monkeypatch):
    install(monkeypatch)
    res = run_cli("run", "--vault", str(vault))
    assert res.exit_code == 0, res.output
    files = list((vault / "digests").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text()
    first = text.splitlines()[0]
    assert first.startswith("Run ") and "errors: none" in first
    assert text.index("SoK: Assessing the Evolution of LLM Capability") < text.index("## 2.")
    assert "## 1. [SoK: Assessing" in text
    assert (vault / "cache" / "seeds.json").exists()

    import sqlite3
    row = sqlite3.connect(vault / "vault.sqlite").execute(
        "SELECT finished_at, counts, errors FROM runs"
    ).fetchone()
    assert row[0] is not None
    counts = json.loads(row[1])
    assert counts["inserted"] > 0 and counts["triaged"] == 2
    assert json.loads(row[2]) == []
    # seeds never enter papers
    assert sqlite3.connect(vault / "vault.sqlite").execute(
        "SELECT COUNT(*) FROM papers WHERE id = '9999.00001'"
    ).fetchone()[0] == 0


def test_e2e_second_run_same_day_adds_no_duplicates(vault, monkeypatch):
    install(monkeypatch)
    run_cli("run", "--vault", str(vault))
    path = next((vault / "digests").glob("*.md"))
    first = path.read_text()
    res = run_cli("run", "--vault", str(vault))
    assert res.exit_code == 0, res.output
    second = path.read_text()
    assert second.count("\n## ") == first.count("\n## ")
    import sqlite3
    conn = sqlite3.connect(vault / "vault.sqlite")
    ids = [r[0] for r in conn.execute("SELECT id FROM papers")]
    assert len(ids) == len(set(ids))
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2


def test_run_on_uninitialised_vault_exits_nonzero(tmp_path, monkeypatch):
    install(monkeypatch)
    res = run_cli("run", "--vault", str(tmp_path / "nope"))
    assert res.exit_code == 1
    assert "rpipe init" in res.output


def test_console_scripts_expose_short_name_and_long_alias():
    import tomllib
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    scripts = tomllib.loads(pyproject.read_text())["project"]["scripts"]
    assert scripts["rpipe"] == "research_pipeline.cli:app"
    assert scripts["research-pipeline"] == "research_pipeline.cli:app"


def test_failing_source_exits_zero_and_is_reported(vault, monkeypatch):
    hf = parse_hf_daily(json.loads((FIXTURES / "hf_daily.json").read_text()))
    install(monkeypatch, [FixtureSource("arxiv", error=SourceError("arXiv API returned HTTP 500")),
                          FixtureSource("hf_daily", hf)])
    res = run_cli("run", "--vault", str(vault))
    assert res.exit_code == 0, res.output
    first = next((vault / "digests").glob("*.md")).read_text().splitlines()[0]
    assert "errors: arxiv: arXiv API returned HTTP 500" in first
    import sqlite3
    errors = sqlite3.connect(vault / "vault.sqlite").execute("SELECT errors FROM runs").fetchone()[0]
    assert json.loads(errors) == ["arxiv: arXiv API returned HTTP 500"]


def test_seed_fetch_failure_does_not_stop_the_run(vault, monkeypatch):
    def boom(ids):
        raise RuntimeError("arXiv down")

    install(monkeypatch, fetcher=boom)
    res = run_cli("run", "--vault", str(vault))
    assert res.exit_code == 0, res.output
    first = next((vault / "digests").glob("*.md")).read_text().splitlines()[0]
    assert "seeds: could not fetch 9999.00001: arXiv down" in first


def _no_extra_pipeline(monkeypatch):
    def make_embedder():
        raise RuntimeError("sentence-transformers is not installed; run `uv sync --extra embed`")

    hf = parse_hf_daily(json.loads((FIXTURES / "hf_daily.json").read_text()))
    monkeypatch.setattr(
        cli, "build_pipeline",
        lambda config: Pipeline([FixtureSource("hf_daily", hf)], make_embedder, seed_fetcher, make_llm, fetch_text),
    )


@pytest.mark.parametrize("cmd", ["run", "triage"])
def test_missing_embed_extra_is_a_clear_error_for_run_and_triage(vault, monkeypatch, cmd):
    _no_extra_pipeline(monkeypatch)
    res = run_cli(cmd, "--vault", str(vault))
    assert res.exit_code == 1
    assert "uv sync --extra embed" in res.output


def test_collect_and_digest_work_without_the_embed_extra(vault, monkeypatch):
    _no_extra_pipeline(monkeypatch)
    res = run_cli("collect", "--vault", str(vault))
    assert res.exit_code == 0, res.output
    assert "inserted=" in res.output
    res = run_cli("digest", "--vault", str(vault), "--date", "2026-01-02")
    assert res.exit_code == 0, res.output
    assert (vault / "digests" / "2026-01-02.md").exists()


def test_individual_commands_compose_to_a_digest(vault, monkeypatch):
    install(monkeypatch)
    for cmd in ("collect", "triage"):
        assert run_cli(cmd, "--vault", str(vault)).exit_code == 0
    res = run_cli("digest", "--vault", str(vault), "--date", "2026-01-02")
    assert res.exit_code == 0, res.output
    assert (vault / "digests" / "2026-01-02.md").exists()


def test_digest_rejects_bad_date(vault):
    res = run_cli("digest", "--vault", str(vault), "--date", "yesterday")
    assert res.exit_code == 1


def test_real_build_pipeline_does_not_load_the_embedder_until_asked(vault, monkeypatch):
    def boom(model_name):
        raise RuntimeError("sentence-transformers is not installed; run `uv sync --extra embed`")

    monkeypatch.setattr(cli, "SentenceTransformerEmbedder", boom)
    pipeline = cli.build_pipeline(cli.load_config(vault / "config.yaml"))
    with pytest.raises(RuntimeError):
        pipeline.make_embedder()


@pytest.mark.parametrize("cmd", ["collect", "triage", "digest"])
def test_single_stage_commands_leave_runs_empty(vault, monkeypatch, cmd):
    import sqlite3

    install(monkeypatch)
    if cmd == "triage":
        assert run_cli("collect", "--vault", str(vault)).exit_code == 0
    res = run_cli(cmd, "--vault", str(vault))
    assert res.exit_code == 0, res.output
    assert sqlite3.connect(vault / "vault.sqlite").execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_build_pipeline_passes_arxiv_max_results(vault):
    (vault / "config.yaml").write_text(
        (vault / "config.yaml").read_text() + "arxiv_max_results: 42\n"
    )
    pipeline = cli.build_pipeline(cli.load_config(vault / "config.yaml"))
    assert pipeline.sources[0].max_results == 42


def test_arxiv_cap_warning_reaches_health_line_and_runs_errors(vault, monkeypatch):
    warning = "arxiv: reached max_results (5) before already-seen papers; some may be missed"

    class CappedSource(FixtureSource):
        last_warning = warning

    arxiv = parse_arxiv_atom((FIXTURES / "arxiv_atom.xml").read_text())
    install(monkeypatch, [CappedSource("arxiv", arxiv)])
    res = run_cli("run", "--vault", str(vault))
    assert res.exit_code == 0, res.output
    first = next((vault / "digests").glob("*.md")).read_text().splitlines()[0]
    assert f"errors: {warning}" in first
    import sqlite3
    errors = sqlite3.connect(vault / "vault.sqlite").execute("SELECT errors FROM runs").fetchone()[0]
    assert json.loads(errors) == [warning]


@pytest.mark.parametrize("exc", [OSError("download failed"), ValueError("bad torch")])
def test_embedding_model_load_failure_is_a_clear_error(vault, monkeypatch, exc):
    def make_embedder():
        raise exc

    monkeypatch.setattr(
        cli, "build_pipeline",
        lambda config: Pipeline([], make_embedder, seed_fetcher, make_llm, fetch_text),
    )
    res = run_cli("triage", "--vault", str(vault))
    assert res.exit_code == 1
    assert f"could not load embedding model BAAI/bge-small-en-v1.5: {exc}" in res.output


def test_health_line_collapses_whitespace_to_one_line():
    line = cli.health_line(date(2026, 1, 2), {}, ["arxiv: boom\n  second\tline", "x\n\ny"])
    assert "\n" not in line and "\t" not in line
    assert "errors: arxiv: boom second line; x y" in line


def test_e2e_run_digest_contains_cited_summary(vault, monkeypatch):
    install(monkeypatch)
    res = run_cli("run", "--vault", str(vault))
    assert res.exit_code == 0, res.output
    text = next((vault / "digests").glob("*.md")).read_text()
    assert "A classifier for X." in text
    assert "[§ 2 Method]" in text
    first = text.splitlines()[0]
    assert "summarized 2" in first and "errors: none" in first
    assert "abstract-only" not in first and "summary failures" not in first


def test_e2e_ollama_down_still_writes_digest_and_names_cause(vault, monkeypatch):
    def down():
        raise LlmError("cannot reach Ollama at http://localhost:11434: start it with `ollama serve`")

    install(monkeypatch, llm=down)
    res = run_cli("run", "--vault", str(vault))
    assert res.exit_code == 0, res.output
    text = next((vault / "digests").glob("*.md")).read_text()
    first = text.splitlines()[0]
    assert "cannot reach Ollama" in first and "summar" in first
    assert "## 1. [SoK: Assessing" in text and "A classifier for X." not in text


def test_e2e_failed_check_is_also_skipped_not_fatal(vault, monkeypatch):
    class Down(AlwaysSummary):
        def check(self):
            raise LlmError("model 'qwen3:14b' is not installed: run `ollama pull qwen3:14b`")

    install(monkeypatch, llm=lambda: Down([]))
    res = run_cli("run", "--vault", str(vault))
    assert res.exit_code == 0, res.output
    first = next((vault / "digests").glob("*.md")).read_text().splitlines()[0]
    assert "ollama pull qwen3:14b" in first


def test_e2e_abstract_only_shows_in_health_line(vault, monkeypatch):
    from research_pipeline.fulltext import FullTextUnavailable

    def none(pid):
        raise FullTextUnavailable("no HTML")

    install(monkeypatch)
    arxiv = parse_arxiv_atom((FIXTURES / "arxiv_atom.xml").read_text())
    monkeypatch.setattr(
        cli, "build_pipeline",
        lambda config: Pipeline([FixtureSource("arxiv", arxiv)], FakeEmbedder, seed_fetcher, make_llm, none),
    )
    res = run_cli("run", "--vault", str(vault))
    assert res.exit_code == 0, res.output
    text = next((vault / "digests").glob("*.md")).read_text()
    assert "abstract-only 2" in text.splitlines()[0]
    assert "Summary based on the abstract only (full text unavailable)" in text


def test_summarize_command_with_nothing_triaged(vault, monkeypatch):
    install(monkeypatch)
    res = run_cli("summarize", "--vault", str(vault))
    assert res.exit_code == 0, res.output
    assert "nothing to summarize" in res.output.lower()


def test_summarize_command_runs_stage_alone(vault, monkeypatch):
    install(monkeypatch)
    for cmd in ("collect", "triage"):
        assert run_cli(cmd, "--vault", str(vault)).exit_code == 0
    res = run_cli("summarize", "--vault", str(vault))
    assert res.exit_code == 0, res.output
    assert "summarized=2" in res.output


def test_summarize_command_reports_ollama_down(vault, monkeypatch):
    def down():
        raise LlmError("cannot reach Ollama")

    install(monkeypatch, llm=down)
    for cmd in ("collect", "triage"):
        assert run_cli(cmd, "--vault", str(vault)).exit_code == 0
    res = run_cli("summarize", "--vault", str(vault))
    assert res.exit_code == 1
    assert "cannot reach Ollama" in res.output


def test_e2e_version_bump_resummarises_from_new_text(vault, monkeypatch):
    import sqlite3
    from research_pipeline.models import Section

    install(monkeypatch)
    assert run_cli("run", "--vault", str(vault)).exit_code == 0
    conn = sqlite3.connect(vault / "vault.sqlite")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT id, title, abstract FROM papers WHERE status='in_digest' LIMIT 1").fetchone()
    pid = row["id"]
    from research_pipeline.db import get_fulltext, get_summary, upsert_paper

    assert get_summary(conn, pid) is not None
    upsert_paper(conn, PaperMeta(id=pid, title=row["title"], abstract="new abstract", source="arxiv",
                                 url=f"https://arxiv.org/abs/{pid}", version=2))
    conn.commit()
    assert get_summary(conn, pid) is None and get_fulltext(conn, pid) is None
    new = [Section(title="Fresh", text="Entirely new text for version two of this paper, long enough.")]
    monkeypatch.setattr(
        cli, "build_pipeline",
        lambda config: Pipeline([FixtureSource("arxiv", [])], FakeEmbedder, seed_fetcher, make_llm, lambda p: new),
    )
    assert run_cli("run", "--vault", str(vault)).exit_code == 0
    assert [s.title for s in get_fulltext(conn, pid)] == ["Fresh"]


# ---------------------------------------------------------------- auto model and context

import httpx
import respx

from test_ollama_fit import GB, URL as OLLAMA, FakeOllama


def install_real_llm(monkeypatch, vault):
    arxiv = parse_arxiv_atom((FIXTURES / "arxiv_atom.xml").read_text())
    hf = parse_hf_daily(json.loads((FIXTURES / "hf_daily.json").read_text()))
    srcs = [FixtureSource("arxiv", arxiv), FixtureSource("hf_daily", hf)]
    monkeypatch.setattr(
        cli, "build_pipeline",
        lambda config: Pipeline(srcs, FakeEmbedder, seed_fetcher,
                                lambda: cli.build_llm(config, vault), fetch_text),
    )
    assert run_cli("model", "set-url", OLLAMA, "--vault", str(vault)).exit_code == 0


def two_model_fake() -> FakeOllama:
    return FakeOllama({
        "big": {"size": 30 * GB, "params": 30 * GB, "ctx": 40960},
        "mid": {"size": 8 * GB, "params": 14 * GB, "ctx": 40960},
    }, free=20 * GB, kv_per_token=100_000)


def chat(request):
    body = json.loads(request.content)
    out = summary() if body["format"]["title"] == "Summary" else critique()
    return httpx.Response(200, json={"message": {"role": "assistant", "content": out.model_dump_json()}})


def test_e2e_default_vault_auto_picks_model_and_sends_resolved_values(vault, monkeypatch):
    install_real_llm(monkeypatch, vault)
    fake = two_model_fake()
    with respx.mock(assert_all_called=False) as m:
        fake.install(m)
        route = m.post(f"{OLLAMA}/api/chat").mock(side_effect=chat)
        res = run_cli("run", "--vault", str(vault))
        assert res.exit_code == 0, res.output
        assert route.call_count > 0
        bodies = [json.loads(c.request.content) for c in route.calls]
        assert {b["model"] for b in bodies} == {"mid"}
        # mid fits until 8 GB + 100_000 * n <= 20 GB, i.e. n = 120000 -> 117 * 1024 = 119808, ceiling 40960, less one step of margin -> 39936
        assert {b["options"]["num_ctx"] for b in bodies} == {39936}
        assert "summarized 2" in next((vault / "digests").glob("*.md")).read_text().splitlines()[0]
        probes_after_first = len(fake.probes())
        assert probes_after_first > 0
        assert (vault / "cache" / "llm_fit.json").exists()
        res = run_cli("run", "--vault", str(vault))
        assert res.exit_code == 0, res.output
        assert len(fake.probes()) == probes_after_first


def test_e2e_pinned_model_with_auto_context_probes_only_that_model(vault, monkeypatch):
    install_real_llm(monkeypatch, vault)
    assert run_cli("model", "set", "mid", "--vault", str(vault)).exit_code == 0
    fake = two_model_fake()
    with respx.mock(assert_all_called=False) as m:
        fake.install(m)
        m.post(f"{OLLAMA}/api/chat").mock(side_effect=chat)
        res = run_cli("run", "--vault", str(vault))
        assert res.exit_code == 0, res.output
    assert fake.probes() and {p["model"] for p in fake.probes()} == {"mid"}


def test_e2e_only_embedding_models_still_writes_digest_and_names_cause(vault, monkeypatch):
    install_real_llm(monkeypatch, vault)
    fake = FakeOllama({"emb": {"size": 1, "params": 1000, "ctx": None, "caps": ["embedding"]}})
    with respx.mock(assert_all_called=False) as m:
        fake.install(m)
        res = run_cli("run", "--vault", str(vault))
    assert res.exit_code == 0, res.output
    first = next((vault / "digests").glob("*.md")).read_text().splitlines()[0]
    assert "ollama pull" in first and "summar" in first


def test_model_fit_prints_choice_and_writes_cache_and_show_reports_it(vault):
    assert run_cli("model", "set-url", OLLAMA, "--vault", str(vault)).exit_code == 0
    before = run_cli("model", "show", "--vault", str(vault)).output
    assert "not probed yet" in before and "rpipe model fit" in before
    fake = two_model_fake()
    with respx.mock(assert_all_called=False) as m:
        fake.install(m)
        res = run_cli("model", "fit", "--vault", str(vault))
        assert res.exit_code == 0, res.output
        assert "Using model mid with a context of 39936 tokens" in res.output
        assert "big" in res.output  # what was tried
        n = len(fake.probes())
        run_cli("model", "fit", "--vault", str(vault))
        assert len(fake.probes()) > n  # fit always re-probes
    assert (vault / "cache" / "llm_fit.json").exists()
    with respx.mock(assert_all_called=False) as m:
        fake.install(m)
        after = run_cli("model", "show", "--vault", str(vault)).output
        fake.models["mid"]["digest"] = "new"  # the installed models changed: the cache is stale
        stale = run_cli("model", "show", "--vault", str(vault)).output
    assert "not probed yet" in stale and "last choice: mid" not in stale
    assert "mid" in after and "39936" in after and "not probed yet" not in after


def test_model_fit_with_no_models_exits_1_with_pull_hint(vault):
    assert run_cli("model", "set-url", OLLAMA, "--vault", str(vault)).exit_code == 0
    with respx.mock(assert_all_called=False) as m:
        FakeOllama({}).install(m)
        res = run_cli("model", "fit", "--vault", str(vault))
    assert res.exit_code == 1 and "ollama pull" in res.output and "Traceback" not in res.output


def test_run_releases_the_embedder_before_the_llm_is_resolved(vault, monkeypatch):
    import gc
    import sys
    import types
    import weakref

    events: list[str] = []
    made: list[weakref.ref] = []

    def make_embedder():
        e = FakeEmbedder()
        made.append(weakref.ref(e))
        return e

    fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(
        is_available=lambda: True, empty_cache=lambda: events.append("empty_cache")))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    def llm():
        gc.collect()
        events.append("embedder gone" if made[0]() is None else "embedder alive")
        events.append("llm resolved")
        return AlwaysSummary([])

    arxiv = parse_arxiv_atom((FIXTURES / "arxiv_atom.xml").read_text())
    monkeypatch.setattr(
        cli, "build_pipeline",
        lambda config: Pipeline([FixtureSource("arxiv", arxiv)], make_embedder, seed_fetcher, llm, fetch_text),
    )
    assert run_cli("run", "--vault", str(vault)).exit_code == 0
    assert events == ["empty_cache", "embedder gone", "llm resolved"]


def test_release_gpu_memory_does_not_import_torch(monkeypatch):
    import sys

    monkeypatch.delitem(sys.modules, "torch", raising=False)
    cli._release_gpu_memory()
    assert "torch" not in sys.modules


def test_model_fit_progress_goes_to_stderr_and_result_to_stdout(vault):
    assert run_cli("model", "set-url", OLLAMA, "--vault", str(vault)).exit_code == 0
    with respx.mock(assert_all_called=False) as m:
        two_model_fake().install(m)
        res = runner.invoke(cli.app, ["model", "fit", "--vault", str(vault)])
    assert res.exit_code == 0
    assert "trying big at 16384 tokens" in res.stderr
    assert "trying" not in res.stdout and "Using model mid" in res.stdout


def test_model_fit_with_both_pinned_says_pinned_and_checks_installed(vault):
    assert run_cli("model", "set-url", OLLAMA, "--vault", str(vault)).exit_code == 0
    assert run_cli("model", "set", "mid", "--vault", str(vault)).exit_code == 0
    assert run_cli("model", "set-context", "16384", "--vault", str(vault)).exit_code == 0
    fake = two_model_fake()
    with respx.mock(assert_all_called=False) as m:
        fake.install(m)
        res = run_cli("model", "fit", "--vault", str(vault))
    assert res.exit_code == 0, res.output
    assert "pinned: mid at 16384 tokens" in res.output
    assert "probing" not in res.output.lower() and fake.probes() == []
    assert "installed" in res.output


def test_model_fit_with_both_pinned_and_model_missing_exits_1(vault):
    assert run_cli("model", "set-url", OLLAMA, "--vault", str(vault)).exit_code == 0
    assert run_cli("model", "set", "ghost", "--vault", str(vault)).exit_code == 0
    assert run_cli("model", "set-context", "16384", "--vault", str(vault)).exit_code == 0
    with respx.mock(assert_all_called=False) as m:
        two_model_fake().install(m)
        res = run_cli("model", "fit", "--vault", str(vault))
    assert res.exit_code == 1 and "ghost" in res.output and "Traceback" not in res.output


def test_model_fit_and_check_turn_odd_failures_into_one_clean_line(vault, monkeypatch):
    assert run_cli("model", "set-url", OLLAMA, "--vault", str(vault)).exit_code == 0
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{OLLAMA}/api/tags").mock(return_value=httpx.Response(200, content=b"<html>"))
        for cmd in ("fit", "check"):
            res = run_cli("model", cmd, "--vault", str(vault))
            assert res.exit_code == 1 and "Traceback" not in res.output and res.output.strip()
    with respx.mock(assert_all_called=False) as m:
        two_model_fake().install(m)

        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(cli.ollama_fit, "_write_cache", boom)
        res = run_cli("model", "fit", "--vault", str(vault))
    assert res.exit_code == 1 and "disk full" in res.output and "Traceback" not in res.output


def test_model_check_help_mentions_probing():
    res = run_cli("model", "check", "--help")
    assert "probe" in res.output.lower()


def test_health_line_counts_truncated_summaries():
    line = cli.health_line(date(2026, 11, 21), {"summarized": 3, "truncated": 2}, [])
    assert "truncated 2" in line
    assert "truncated" not in cli.health_line(date(2026, 11, 21), {"summarized": 3}, [])
