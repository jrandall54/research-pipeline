"""The recommender: which catalog models fit this machine, where they run, and in what order."""
import math
from datetime import date, datetime

import pytest
from typer.testing import CliRunner

from research_pipeline import bench as bench_mod
from research_pipeline import catalog as catalog_mod
from research_pipeline import cli, ollama_fit, registry
from research_pipeline.bench import Bench
from research_pipeline.catalog import Catalog, CatalogEntry
from research_pipeline.machine import Gpu, MachineInfo, OllamaServer
from research_pipeline.recommender import (
    CPU_CTX,
    TOP_N,
    band_for,
    dense_equivalent_b,
    kv_factor,
    place,
    rank,
    uncatalogued,
)
from research_pipeline.vault import init_vault, load_config

runner = CliRunner()
FA_Q8 = {"OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0"}


def entry(id, pull, gb, params, arch="dense", active=None, kv=64.0, bands=("mid",), trained=262144, notes=""):
    return CatalogEntry(id=id, family="Test", pull=pull, arch=arch, params_b=params, active_b=active,
                        quant="Q4_K_M", download_gb=gb, trained_ctx=trained, kv_mib_per_1k=kv,
                        bands=list(bands), notes=notes)


D27 = entry("d27", "hf.co/test/Dense-27B-GGUF:IQ3", 12.0, 27.0, bands=("mid", "high"), notes="Strong writer.")
D14 = entry("d14", "test:14b", 9.0, 14.0, bands=("cpu", "mid"))
MOE = entry("moe35", "hf.co/test/MoE-35B-A3B-GGUF:IQ3", 13.7, 35.0, arch="moe", active=3.0, bands=("high",))
D8 = entry("d8", "test:8b", 5.0, 8.0, bands=("cpu", "small"))
D32 = entry("d32", "test:32b", 20.0, 32.0, bands=("high",))
CATALOG = Catalog(version=1, updated=date(2026, 11, 24), entries=[D27, D14, MOE, D8, D32])


def server(env=None, host_matches=True, how="systemd"):
    return OllamaServer("0.13.0", how, dict(env or {}), [], host_matches)


def machine(vram=None, gpus=None, ram=32.0, free=None, env=None, models=(), host_matches=True, **kw):
    gpu_list = gpus if gpus is not None else ([Gpu("Test GPU", vram, "nvidia")] if vram else [])
    base = dict(os="Linux", ram_gb=ram, gpu=max(gpu_list, key=lambda g: g.vram_gb) if gpu_list else None,
                ollama_installed=True, ollama_running=True, ollama_models=list(models),
                claude_path=None, claude_logged_in=False, gpus=gpu_list, ram_free_gb=free,
                disk_free_gb=100.0, ollama=server(env, host_matches))
    base.update(kw)
    return MachineInfo(**base)


def make_bench(model, seconds=72.0):
    return Bench(model=model, num_ctx=32768, read_tps=1000.0, write_tps=50.0, seconds_per_paper=seconds,
                 paper_tokens=22000, measured_at=datetime(2026, 11, 24, 12))


# ---- pure functions ----

def test_band_boundaries():
    assert band_for(machine()) == "cpu"
    assert band_for(machine(vram=6.0)) == "small"
    assert band_for(machine(vram=15.92)) == "mid"
    assert band_for(machine(vram=24.0)) == "high"
    two = [Gpu("A", 24.0, "nvidia"), Gpu("B", 24.0, "nvidia")]
    assert band_for(machine(gpus=two)) == "very_large"


def test_kv_factor():
    assert kv_factor(FA_Q8) == 0.5
    assert kv_factor({"OLLAMA_FLASH_ATTENTION": "true", "OLLAMA_KV_CACHE_TYPE": "q4_0"}) == 0.25
    assert kv_factor({"OLLAMA_KV_CACHE_TYPE": "q8_0"}) == 1.0
    assert kv_factor({}) == 1.0


def test_place_gpu_context_estimate():
    info = machine(vram=15.92, env=FA_Q8)
    # no free-memory reading: usable = total - 0.5 - 2.0 GiB; 12 GB of weights = 12e9 / 2^30 GiB
    usable = 15.92 - 0.5 - 2.0
    expected = math.floor((usable - 12e9 / 2**30) / (64 * 0.5 / 1000 / 1024)) // 1024 * 1024
    assert place(D27, info) == ("gpu", expected)
    short = D27.model_copy(update={"trained_ctx": 32768})
    assert place(short, info) == ("gpu", 32768)


def test_place_gpu_context_capped_at_ceiling():
    small = entry("tiny", "tiny:1b", 1.0, 1.0, kv=1.0)
    assert place(small, machine(vram=24.0)) == ("gpu", ollama_fit.CONTEXT_CEILING)


def test_place_moe_spills_when_ram_allows():
    info = machine(vram=16.0, ram=29.0, free=20.0)
    assert place(MOE, info) == ("spill", CPU_CTX)
    assert place(MOE, machine(vram=16.0, ram=29.0, free=4.0)) is None


def test_dense_never_spills():
    info = machine(vram=16.0, ram=128.0, free=120.0)
    assert place(D32, info) is None


def test_cpu_only_ranks_small_models_by_ram():
    ranked = rank(machine(ram=16.0), CATALOG, {})
    assert [c.entry.id for c in ranked] == ["d14", "d8"]
    assert all(c.placement == "cpu" and c.est_ctx == CPU_CTX for c in ranked)
    assert "runs on the CPU: slow but fine overnight" in ranked[0].reasons
    big = [c.entry.id for c in rank(machine(ram=32.0), CATALOG, {})]
    assert "moe35" in big and "d32" not in big and "d27" not in big


def test_small_gpu_with_ram_gets_cpu_models():
    ranked = rank(machine(vram=4.0, ram=16.0, free=12.0), CATALOG, {})
    assert [(c.entry.id, c.placement) for c in ranked] == [("d8", "cpu"), ("d14", "cpu")]  # small band first


def test_small_gpu_with_shipped_catalog_offers_a_cpu_model():
    from research_pipeline.machine import recommend
    info = machine(vram=4.0, ram=16.0, free=12.0)
    shipped = catalog_mod.shipped_catalog()
    ranked = rank(info, shipped, {})
    assert ranked and all(c.placement == "cpu" for c in ranked)
    assert any(c.entry.pull == "qwen3:8b" for c in ranked)
    rec = recommend(info, shipped)
    assert rec.backend == "ollama" and rec.pull is not None
    assert rec.reasons == ["no catalog model fits in the GPU memory free now; a small model runs on the CPU overnight (slow)"]


def test_none_reason_does_not_blame_ram_it_did_not_check():
    from research_pipeline.machine import recommend
    rec = recommend(machine(ram=4.0), CATALOG)
    assert rec.backend == "none"
    assert rec.reasons[0].startswith("no model in the catalog fits this computer's GPU memory or its memory (RAM)")


def test_dense_equivalent_b():
    assert dense_equivalent_b(D27) == 27.0
    assert dense_equivalent_b(MOE) == pytest.approx(math.sqrt(35 * 3))  # about 10.2
    no_active = MOE.model_copy(update={"active_b": None})
    assert dense_equivalent_b(no_active) == 35.0


def test_rank_puts_dense_27b_above_moe_35b_a3b():
    moe = MOE.model_copy(update={"bands": ["mid"], "download_gb": 10.0})
    cat = Catalog(version=1, updated=date(2026, 11, 24), entries=[moe, D27])
    ranked = rank(machine(vram=16.0, env=FA_Q8), cat, {})
    assert [(c.entry.id, c.placement) for c in ranked] == [("d27", "gpu"), ("moe35", "gpu")]


def test_cpu_only_tiny_ram_gets_nothing():
    assert rank(machine(ram=4.0), CATALOG, {}) == []
    assert rank(machine(ram=None), CATALOG, {}) == []


def test_rank_orders_gpu_before_spill_and_bigger_first():
    ranked = rank(machine(vram=16.0, ram=64.0, free=40.0), CATALOG, {})
    ids = [(c.entry.id, c.placement) for c in ranked]
    # mid-band GPU entries first (27B before 14B), then the other GPU fit, then the MoE spill;
    # the dense 32B fits nowhere
    assert ids == [("d27", "gpu"), ("d14", "gpu"), ("d8", "gpu"), ("moe35", "spill")]
    # with the leaner KV cache the MoE fits on the GPU itself, ahead of the smaller 8B
    lean = rank(machine(vram=16.0, ram=64.0, free=40.0, env=FA_Q8), CATALOG, {})
    assert [(c.entry.id, c.placement) for c in lean][2:] == [("moe35", "gpu"), ("d8", "gpu")]
    top = ranked[0]
    assert any(r.startswith("fits fully in the 13.5 GB of GPU memory free for models with about") and "rpipe model fit" in r
               for r in top.reasons)
    assert "Strong writer." in top.reasons


def test_rank_keeps_at_most_top_n():
    many = [entry(f"m{i}", f"m:{i}b", 1.0 + i / 10, float(i + 1)) for i in range(8)]
    cat = Catalog(version=1, updated=date(2026, 11, 24), entries=many)
    ranked = rank(machine(vram=24.0), cat, {})
    assert len(ranked) == TOP_N and ranked[0].entry.id == "m7"


def test_rank_spill_reason_in_words():
    ranked = rank(machine(vram=16.0, ram=29.0, free=20.0), CATALOG, {})
    spill = next(c for c in ranked if c.placement == "spill")
    assert any("mixture of experts: 3B of 35B parameters work per token" in r for r in spill.reasons)


def test_rank_marks_installed_and_attaches_bench():
    info = machine(vram=16.0, env=FA_Q8, models=["hf.co/test/Dense-27B-GGUF:IQ3", "test:8b"])
    benches = {"hf.co/test/Dense-27B-GGUF:IQ3": make_bench("hf.co/test/Dense-27B-GGUF:IQ3", 95.0)}
    ranked = {c.entry.id: c for c in rank(info, CATALOG, benches)}
    d27, d14 = ranked["d27"], ranked["d14"]
    assert d27.installed and d27.bench is not None and "installed" in d27.reasons
    assert any("95 s per paper" in r for r in d27.reasons)
    assert not d14.installed and d14.bench is None
    assert "download 9 GB" in d14.reasons
    assert "speed not measured yet: rpipe model bench" in d14.reasons


def test_installed_name_without_tag_matches_latest():
    e = entry("plain", "plain", 3.0, 3.0)
    cat = Catalog(version=1, updated=date(2026, 11, 24), entries=[e])
    (c,) = rank(machine(vram=16.0, models=["plain:latest"]), cat, {})
    assert c.installed


def test_uncatalogued_lists_other_installed_models():
    info = machine(vram=16.0, models=["test:8b", "my-own:7b", "nomic-embed-text:latest", "test:14b"])
    assert uncatalogued(info, CATALOG) == ["my-own:7b"]


# ---- rpipe model recommend ----

@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "_entry_points", lambda group: [])
    path = tmp_path / "vault"
    init_vault(path, topics=["LLM security"])
    return path


def recommend_run(vault, monkeypatch, info, input="", tty=True, fit=None):
    monkeypatch.setattr(cli, "detect_machine", lambda config: info)
    monkeypatch.setattr(catalog_mod, "load_catalog", lambda v: CATALOG)
    monkeypatch.setattr(cli, "_is_tty", lambda: tty)
    pulled = []
    monkeypatch.setattr(cli, "pull_model", lambda url, tag, progress: (progress("pulling 50%"), pulled.append(tag)))
    fits = []

    def resolve(url, v, model, num_ctx, force=False, log=lambda s: None, **kw):
        fits.append((model, num_ctx, force))
        return fit or ollama_fit.Fit(model=model, num_ctx=57344)

    monkeypatch.setattr(ollama_fit, "resolve", resolve)
    monkeypatch.setattr(ollama_fit, "cached_fit", lambda *a, **k: fit or ollama_fit.Fit(model="m", num_ctx=57344))
    result = runner.invoke(cli.app, ["model", "recommend", "--vault", str(vault)], input=input)
    return result, pulled, fits


def test_recommend_command_lists_and_keeps_current_on_enter(vault, monkeypatch):
    before = (vault / "config.yaml").read_text()
    info = machine(vram=16.0, env={}, models=["test:8b", "my-own:7b"], claude_path="/c/claude", claude_logged_in=True)
    result, pulled, fits = recommend_run(vault, monkeypatch, info, "\n")
    assert result.exit_code == 0, result.output
    out = result.output
    assert "1. hf.co/test/Dense-27B-GGUF:IQ3" in out
    assert "Run rpipe model tune to apply them" in out
    assert "installed, not in the catalog: my-own:7b (rpipe model fit measures any installed model)" in out
    assert "cloud: Claude Code is logged in; rpipe model set-backend claude-code uses it" in out
    assert "Use one? number, or Enter to keep your current model" in out
    assert (vault / "config.yaml").read_text() == before and pulled == [] and fits == []


def test_recommend_pick_installed_gpu_model_pins_and_fits(vault, monkeypatch):
    info = machine(vram=16.0, env=FA_Q8, models=["hf.co/test/Dense-27B-GGUF:IQ3"])
    result, pulled, fits = recommend_run(vault, monkeypatch, info, "1\nn\n")
    assert result.exit_code == 0, result.output
    cfg = load_config(vault / "config.yaml")
    assert cfg.llm_model == "hf.co/test/Dense-27B-GGUF:IQ3" and cfg.llm_num_ctx == "auto"
    assert fits == [("hf.co/test/Dense-27B-GGUF:IQ3", "auto", True)] and pulled == []
    assert "57,344 tokens" in result.output
    assert "rpipe model set auto" in result.output and "rpipe model set-context auto" in result.output


def test_recommend_pick_cpu_model_pins_context(vault, monkeypatch):
    info = machine(ram=16.0, models=["test:14b"])
    result, pulled, fits = recommend_run(vault, monkeypatch, info, "1\nn\n")
    assert result.exit_code == 0, result.output
    cfg = load_config(vault / "config.yaml")
    assert cfg.llm_model == "test:14b" and cfg.llm_num_ctx == 32768
    assert fits == [] and "pinned" in result.output


def test_recommend_pick_downloads_after_yes(vault, monkeypatch):
    info = machine(ram=16.0)
    result, pulled, _ = recommend_run(vault, monkeypatch, info, "1\ny\nn\n")
    assert result.exit_code == 0, result.output
    assert "download 9 GB; free disk where Ollama keeps models: 100 GB" in result.output
    assert pulled == ["test:14b"] and load_config(vault / "config.yaml").llm_model == "test:14b"


def test_recommend_declined_download_changes_nothing(vault, monkeypatch):
    before = (vault / "config.yaml").read_text()
    result, pulled, _ = recommend_run(vault, monkeypatch, machine(ram=16.0), "1\nn\n")
    assert result.exit_code == 0, result.output
    assert pulled == [] and (vault / "config.yaml").read_text() == before


def test_recommend_refuses_download_without_disk(vault, monkeypatch):
    before = (vault / "config.yaml").read_text()
    info = machine(vram=16.0, env=FA_Q8, disk_free_gb=10.0)
    result, pulled, _ = recommend_run(vault, monkeypatch, info, "1\n")
    assert "download 12 GB; free disk where Ollama keeps models: 10 GB" in result.output
    assert "not enough free disk" in result.output
    assert pulled == [] and (vault / "config.yaml").read_text() == before


def test_recommend_pick_offers_bench(vault, monkeypatch):
    info = machine(vram=16.0, env=FA_Q8, models=["hf.co/test/Dense-27B-GGUF:IQ3"])
    monkeypatch.setattr(bench_mod, "run_bench", lambda url, model, ctx, tokens: make_bench(model))
    result, _, _ = recommend_run(vault, monkeypatch, info, "1\ny\n")
    assert result.exit_code == 0, result.output
    assert "Measure its speed now (about 1-5 minutes)?" in result.output
    assert "72 s per paper" in result.output
    assert "hf.co/test/Dense-27B-GGUF:IQ3" in bench_mod.load_benches(vault)


def test_recommend_pick_switches_backend_and_says_how_to_go_back(vault, monkeypatch):
    from research_pipeline import profile_edit
    profile_edit.set_llm_backend(vault, "claude-code", {"ollama", "claude-code", "none"}, "/c/claude")
    info = machine(vram=16.0, env=FA_Q8, models=["hf.co/test/Dense-27B-GGUF:IQ3"])
    result, _, _ = recommend_run(vault, monkeypatch, info, "1\nn\n")
    assert result.exit_code == 0, result.output
    cfg = load_config(vault / "config.yaml")
    assert cfg.llm_backend == "ollama" and cfg.llm_model == "hf.co/test/Dense-27B-GGUF:IQ3"
    assert "rpipe model set-backend claude-code" in result.output


def test_recommend_non_tty_prints_only(vault, monkeypatch):
    before = (vault / "config.yaml").read_text()
    result, pulled, fits = recommend_run(vault, monkeypatch, machine(vram=16.0), "1\n", tty=False)
    assert result.exit_code == 0, result.output
    assert "1. hf.co/test/Dense-27B-GGUF:IQ3" in result.output
    assert "Use one?" not in result.output
    assert (vault / "config.yaml").read_text() == before and pulled == [] and fits == []


def test_recommend_nothing_fits_says_so(vault, monkeypatch):
    result, _, _ = recommend_run(vault, monkeypatch, machine(ram=4.0), "")
    assert result.exit_code == 0, result.output
    assert "No model in the catalog fits this computer's GPU memory or its memory (RAM)." in result.output
    assert "Use one?" not in result.output


# ---- final-review fixes: calibration, quant tie-break, host mismatch ----

def test_place_matches_owner_measurement():
    """Measured 2026-11-24 on a 16 GB RTX 5070 Ti, 15364 MiB free: the 13.7 GB Qwen3.6-35B-A3B IQ3_S fit
    fully at 16K and spilled at 49K. The estimate must land between."""
    card = Gpu("RTX 5070 Ti", 16303 / 1024, "nvidia", free_gb=15364 / 1024)
    qwen = entry("q35", "q35", 13.7, 35.0, arch="moe", active=3.0, kv=19.5, bands=("mid",))
    placement, ctx = place(qwen, machine(gpus=[card], env=FA_Q8))
    assert placement == "gpu" and ctx == 25600
    assert 16384 < ctx < 49152


def test_place_uses_free_memory_when_known():
    busy = Gpu("Busy", 16.0, "nvidia", free_gb=10.0)  # another app holds 6 GB
    assert place(D27, machine(gpus=[busy], env=FA_Q8)) is None
    assert place(D27, machine(gpus=[Gpu("Idle", 16.0, "nvidia")], env=FA_Q8))[0] == "gpu"


def test_rank_prefers_bigger_quant_of_same_model():
    iq3 = entry("q27-iq3", "q27:iq3", 12.0, 27.0, bands=("high",))
    q4 = entry("q27-q4", "q27:q4", 16.8, 27.0, bands=("high",))
    cat = Catalog(version=1, updated=date(2026, 11, 24), entries=[iq3, q4])
    ranked = rank(machine(vram=24.0, env=FA_Q8, models=["q27:iq3"]), cat, {})
    assert [(c.entry.id, c.placement) for c in ranked] == [("q27-q4", "gpu"), ("q27-iq3", "gpu")]


def test_mismatched_server_ignores_service_kv_settings():
    from research_pipeline.recommender import server_kv_factor
    assert server_kv_factor(machine(vram=16.0, env=FA_Q8)) == 0.5
    assert server_kv_factor(machine(vram=16.0, env=FA_Q8, host_matches=False)) == 1.0
    lean = place(D27, machine(vram=16.0, env=FA_Q8))
    plain = place(D27, machine(vram=16.0, env=FA_Q8, host_matches=False))
    assert plain[1] < lean[1]


def test_recommend_prints_host_note(vault, monkeypatch):
    info = machine(vram=16.0, env={**FA_Q8, "OLLAMA_HOST": "127.0.0.1:11436"}, host_matches=False)
    result, _, _ = recommend_run(vault, monkeypatch, info, tty=False)
    assert "rpipe could not read the settings of the server it uses" in result.output


def test_recommend_says_when_it_cannot_read_settings(vault, monkeypatch):
    info = machine(vram=16.0, ollama=server({}, host_matches=None, how="manual"))
    result, _, _ = recommend_run(vault, monkeypatch, info, tty=False)
    assert "rpipe cannot read this server's settings" in result.output
    assert "now: not set" not in result.output


def test_recommend_explains_the_longer_call_limit_on_a_slow_machine(vault, monkeypatch):
    slow = Bench(model="test:14b", num_ctx=32768, read_tps=20.0, write_tps=3.0, seconds_per_paper=1933.0,
                 paper_tokens=22000, measured_at=datetime(2026, 11, 24, 12))
    bench_mod.save_bench(vault, slow)
    result, _, _ = recommend_run(vault, monkeypatch, machine(ram=16.0, models=["test:14b"]), "1\nn\n")
    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())
    assert "1,100 s" in out and "longer than the usual 600-second limit" in out
    assert "Warning" not in out and "time out" not in out


def test_recommend_no_limit_note_for_fast_model(vault, monkeypatch):
    bench_mod.save_bench(vault, make_bench("test:14b"))  # 1000 tokens/s: 22 s to read
    result, _, _ = recommend_run(vault, monkeypatch, machine(ram=16.0, models=["test:14b"]), "1\nn\n")
    assert "600-second limit" not in result.output


def test_8gb_card_with_little_free_gets_cpu_models():
    """An 8 GiB card with only 5 GiB free fits no shipped model on the GPU: fall back to RAM and CPU."""
    info = machine(gpus=[Gpu("RTX 4060", 8.0, "nvidia", 5.0)], ram=32.0, free=24.0)
    ranked = rank(info, catalog_mod.shipped_catalog(), {}, top=None)
    assert ranked
    assert {c.placement for c in ranked} <= {"cpu", "spill"}
    assert any(c.placement == "cpu" for c in ranked)


def test_mid_band_with_gpu_fits_gets_no_cpu_models():
    info = machine(gpus=[Gpu("RTX 5070 Ti", 16.0, "nvidia", 15.0)], ram=32.0, free=24.0)
    ranked = rank(info, catalog_mod.shipped_catalog(), {}, top=None)
    assert any(c.placement == "gpu" for c in ranked)
    assert all(c.placement != "cpu" for c in ranked)


def test_placement_reason_names_usable_memory():
    info = machine(gpus=[Gpu("Test GPU", 16.0, "nvidia", 12.0)])  # usable = 12.0 - 2.0 = 10.0 GiB
    top = rank(info, CATALOG, {})[0]
    assert top.placement == "gpu"
    reason = top.reasons[0]
    assert reason.startswith("fits fully in the 10.0 GB of GPU memory free for models with about")
    assert "16.0" not in reason
