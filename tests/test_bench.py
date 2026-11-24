import json
from datetime import datetime

import httpx
import pytest
import respx
from typer.testing import CliRunner

from research_pipeline import bench, cli, ollama_fit, registry
from research_pipeline.db import open_db, save_fulltext
from research_pipeline.llm import LlmError
from research_pipeline.models import Section
from research_pipeline.vault import init_vault

runner = CliRunner()
URL = "http://localhost:11434"
NOW = datetime(2026, 11, 24, 12, 0, 0)


def make_bench(model="m", **kw):
    base = dict(model=model, num_ctx=32768, read_tps=1000.0, write_tps=50.0,
                seconds_per_paper=72.0, paper_tokens=22000, measured_at=NOW)
    base.update(kw)
    return bench.Bench(**base)


def test_estimate_seconds():
    assert bench.estimate_seconds(1000.0, 50.0, 22000) == 72.0


def test_run_bench_reads_ollama_stats():
    reply = {"prompt_eval_count": 3000, "prompt_eval_duration": 3_000_000_000,
             "eval_count": 200, "eval_duration": 4_000_000_000}
    with respx.mock() as m:
        route = m.post(f"{URL}/api/generate").mock(return_value=httpx.Response(200, json=reply))
        b = bench.run_bench(URL, "m", 32768, 22000, now=lambda: NOW)
    body = json.loads(route.calls[0].request.content)
    assert body["stream"] is False and body["keep_alive"] == 0
    assert body["options"]["num_predict"] == bench.BENCH_OUTPUT_TOKENS
    assert b.read_tps == 1000 and b.write_tps == 50
    assert b.seconds_per_paper == 72.0 and b.measured_at == NOW


def test_run_bench_missing_stats_raises():
    with respx.mock() as m:
        m.post(f"{URL}/api/generate").mock(return_value=httpx.Response(200, json={"response": "x"}))
        with pytest.raises(LlmError, match="did not report timing"):
            bench.run_bench(URL, "m", 32768, 22000)


def test_prompt_has_nonce_first():
    p = bench.bench_prompt("abc123")
    assert p.startswith("abc123")
    assert bench.bench_prompt("zzz") != p


def test_median_paper_tokens_default_when_few(tmp_path):
    conn = open_db(tmp_path / "v.sqlite")
    assert bench.median_paper_tokens(conn) == bench.DEFAULT_PAPER_TOKENS


def test_median_paper_tokens_from_texts(tmp_path):
    conn = open_db(tmp_path / "v.sqlite")
    for i, n in enumerate([4000, 8000, 12000, 16000, 40000]):
        conn.execute("INSERT INTO papers (id, title, source, url, status, created_at, updated_at) "
                     "VALUES (?, 't', 'arxiv', 'u', 'new', 'x', 'x')", (f"p{i}",))
        save_fulltext(conn, f"p{i}", [Section(title="s", text="x" * n)])
    assert bench.median_paper_tokens(conn) == 3000


def test_save_and_load_bench_roundtrip(tmp_path):
    bench.save_bench(tmp_path, make_bench("a"))
    bench.save_bench(tmp_path, make_bench("b", read_tps=5.0))
    bench.save_bench(tmp_path, make_bench("a", read_tps=2000.0))
    got = bench.load_benches(tmp_path)
    assert set(got) == {"a", "b"} and got["a"].read_tps == 2000.0 and got["b"].read_tps == 5.0


def test_load_benches_corrupt_file_is_empty(tmp_path):
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / bench.BENCH_NAME).write_text("{not json")
    assert bench.load_benches(tmp_path) == {}


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "_entry_points", lambda group: [])
    path = tmp_path / "vault"
    init_vault(path, topics=["LLM security"])
    return path


def test_bench_command_refuses_cloud_backend(vault):
    cfg = vault / "config.yaml"
    cfg.write_text(cfg.read_text().replace("llm_backend: ollama", "llm_backend: claude-code"))
    r = runner.invoke(cli.app, ["model", "bench", "--vault", str(vault)])
    assert r.exit_code == 1
    assert "measures local models" in r.output


def test_bench_command_saves(vault, monkeypatch):
    monkeypatch.setattr(ollama_fit, "resolve", lambda *a, **k: ollama_fit.Fit(model="m", num_ctx=32768))
    monkeypatch.setattr(bench, "run_bench", lambda *a, **k: make_bench("m"))
    r = runner.invoke(cli.app, ["model", "bench", "--vault", str(vault)])
    assert r.exit_code == 0, r.output
    assert "reads 1000 tokens/s, writes 50 tokens/s" in r.output
    assert "72 s per paper" in r.output
    assert "m" in bench.load_benches(vault)
    assert "llm_model: m" not in (vault / "config.yaml").read_text()
