import json

import httpx
import pytest
import respx

from research_pipeline.llm import LlmError
from research_pipeline.ollama_fit import (
    Fit,
    ModelInfo,
    choose,
    fits,
    list_models,
    max_context,
    resolve,
)

URL = "http://ollama.test:11434"
GB = 10**9


class FakeOllama:
    """A tiny Ollama: a model fits fully in VRAM when size + kv_per_token * ctx <= free."""

    def __init__(self, models: dict[str, dict], free: int = 20 * GB, kv_per_token: int = 100_000):
        self.models = models
        self.free = free
        self.kv = kv_per_token
        self.loaded: dict[str, int] = {}  # name -> num_ctx
        self.generate_calls: list[dict] = []
        self.fail_generate: set[str] = set()
        self.timeout_generate: set[str] = set()

    def tags(self, request):
        return httpx.Response(200, json={"models": [
            {"name": n, "digest": m.get("digest", "d-" + n)} for n, m in self.models.items()
        ]})

    def show(self, request):
        name = json.loads(request.content)["model"]
        m = self.models[name]
        info = {"general.parameter_count": m["params"]}
        if m.get("ctx") is not None:
            info["qwen3.context_length"] = m["ctx"]
        return httpx.Response(200, json={
            "model_info": info, "capabilities": m.get("caps", ["completion"])})

    def generate(self, request):
        body = json.loads(request.content)
        self.generate_calls.append(body)
        name = body["model"]
        if body["keep_alive"] == 0:
            self.loaded.pop(name, None)
            return httpx.Response(200, json={"done": True})
        if name in self.timeout_generate:
            raise httpx.ReadTimeout("slow")
        if name in self.fail_generate:
            return httpx.Response(200, json={"error": "out of memory"})
        self.loaded[name] = body["options"]["num_ctx"]
        return httpx.Response(200, json={"done": True})

    def ps(self, request):
        out = []
        for name, ctx in self.loaded.items():
            size = self.models[name]["size"] + self.kv * ctx
            vram = size if size <= self.free else size // 2
            out.append({"name": name, "size": size, "size_vram": vram})
        return httpx.Response(200, json={"models": out})

    def probes(self) -> list[dict]:
        return [c for c in self.generate_calls if c["keep_alive"] != 0]

    def install(self, mock: respx.MockRouter) -> None:
        mock.get(f"{URL}/api/tags").mock(side_effect=self.tags)
        mock.post(f"{URL}/api/show").mock(side_effect=self.show)
        mock.post(f"{URL}/api/generate").mock(side_effect=self.generate)
        mock.get(f"{URL}/api/ps").mock(side_effect=self.ps)


def boundary_fake(boundary: int, size: int = 8 * GB) -> FakeOllama:
    """One model "m" that fits up to exactly `boundary` tokens."""
    kv = 100_000
    return FakeOllama({"m": {"size": size, "params": 8 * GB}}, free=size + kv * boundary, kv_per_token=kv)


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as m:
        yield m


def info(name, params, ctx=40960, caps=("completion",), digest=None):
    return ModelInfo(name=name, digest=digest or "d-" + name, params=params,
                     max_context=ctx, capabilities=list(caps))


def three_models() -> FakeOllama:
    # sizes in bytes; free 20 GB. big never fits at 16K, mid fits up to ~24K.
    return FakeOllama({
        "big": {"size": 25 * GB, "params": 27 * GB, "ctx": 262144},
        "mid": {"size": 18 * GB, "params": 14 * GB, "ctx": 40960},
        "small": {"size": 4 * GB, "params": 4 * GB, "ctx": 40960},
    }, free=20 * GB, kv_per_token=70_000)


def test_list_models_reads_params_context_capabilities_digest(mock):
    FakeOllama({
        "a:latest": {"size": 1, "params": 14_768_307_200, "ctx": 40960,
                     "caps": ["completion", "tools"], "digest": "abc"},
        "emb": {"size": 1, "params": 137_000_000, "ctx": None, "caps": ["embedding"]},
    }).install(mock)
    models = {m.name: m for m in list_models(URL)}
    a = models["a:latest"]
    assert (a.digest, a.params, a.max_context) == ("abc", 14_768_307_200, 40960)
    assert a.capabilities == ["completion", "tools"]
    assert models["emb"].max_context is None
    assert models["emb"].capabilities == ["embedding"]


def test_list_models_unreachable_raises_llmerror(mock):
    mock.get(f"{URL}/api/tags").mock(side_effect=httpx.ConnectError("no"))
    with pytest.raises(LlmError, match="Ollama"):
        list_models(URL)


def test_fits_true_when_fully_in_vram_and_unloads_after(mock):
    fake = boundary_fake(30000)
    fake.install(mock)
    assert fits(URL, "m", 16384) is True
    last = fake.generate_calls[-1]
    assert last["keep_alive"] == 0 and last["model"] == "m"
    assert fake.loaded == {}
    first = fake.generate_calls[0]
    assert first["prompt"] == "" and first["keep_alive"] == "5m"
    assert first["options"] == {"num_ctx": 16384}


def test_fits_unloads_even_when_probe_errored(mock):
    fake = boundary_fake(30000)
    fake.fail_generate.add("m")
    fake.install(mock)
    assert fits(URL, "m", 16384) is False
    assert fake.generate_calls[-1]["keep_alive"] == 0


def test_fits_false_on_error_field_timeout_and_partial_offload(mock):
    fake = boundary_fake(10000)
    fake.install(mock)
    assert fits(URL, "m", 20000) is False  # partial offload
    fake.fail_generate.add("m")
    assert fits(URL, "m", 8192) is False  # error field
    fake.fail_generate.clear()
    fake.timeout_generate.add("m")
    assert fits(URL, "m", 8192) is False  # timeout
    assert fake.generate_calls[-1]["keep_alive"] == 0


def test_fits_false_when_model_not_listed_in_ps(mock):
    fake = boundary_fake(30000)
    fake.install(mock)
    mock.get(f"{URL}/api/ps").mock(return_value=httpx.Response(200, json={"models": []}))
    assert fits(URL, "m", 8192) is False


def test_max_context_finds_boundary(mock):
    fake = boundary_fake(22528)
    fake.install(mock)
    assert max_context(URL, "m", ceiling=40960) == 22528
    assert len(fake.probes()) < 12


def test_max_context_none_when_floor_does_not_fit(mock):
    fake = boundary_fake(4096)
    fake.install(mock)
    assert max_context(URL, "m", ceiling=40960) is None
    assert len(fake.probes()) == 1


def test_max_context_never_exceeds_ceiling(mock):
    fake = boundary_fake(100000)
    fake.install(mock)
    assert max_context(URL, "m", ceiling=40960) == 40960
    assert max(p["options"]["num_ctx"] for p in fake.probes()) <= 40960
    assert max_context(URL, "m", ceiling=30000) == 29696  # multiple of the step


def test_choose_picks_largest_model_that_fits_at_16k(mock):
    fake = three_models()
    fake.install(mock)
    models = list_models(URL)
    logs: list[str] = []
    fit = choose(URL, models, None, None, log=logs.append)
    assert fit.model == "mid"
    # mid: 18 GB + 70k*ctx <= 20 GB -> ctx <= 28571 -> 27648 at step 1024
    assert fit.num_ctx == 27648
    assert any("big" in line for line in logs)


def test_choose_skips_embedding_only_models(mock):
    fake = FakeOllama({
        "emb": {"size": 1 * GB, "params": 30 * GB, "caps": ["embedding"]},
        "gen": {"size": 1 * GB, "params": 1 * GB, "caps": ["completion"], "ctx": 16384},
    })
    fake.install(mock)
    fit = choose(URL, list_models(URL), None, None)
    assert fit == Fit(model="gen", num_ctx=16384)
    assert all(p["model"] != "emb" for p in fake.generate_calls)


def test_choose_no_models_raises_llmerror_naming_ollama_pull():
    with pytest.raises(LlmError, match="ollama pull"):
        choose(URL, [], None, None)
    with pytest.raises(LlmError, match="ollama pull"):
        choose(URL, [info("e", 1, caps=("embedding",))], None, None)


def test_choose_pinned_model_only_tries_it(mock):
    fake = three_models()
    fake.install(mock)
    fit = choose(URL, list_models(URL), "small", None)
    assert fit.model == "small"
    assert {p["model"] for p in fake.generate_calls} == {"small"}


def test_choose_pinned_model_without_fit_raises(mock):
    fake = three_models()
    fake.install(mock)
    with pytest.raises(LlmError, match="big") as e:
        choose(URL, list_models(URL), "big", None)
    assert "rpipe model set" in str(e.value)


def test_choose_pinned_model_not_installed_raises(mock):
    three_models().install(mock)
    with pytest.raises(LlmError, match="nope"):
        choose(URL, list_models(URL), "nope", None)


def test_choose_pinned_ctx_picks_model_that_fits_at_that_ctx(mock):
    fake = three_models()
    fake.install(mock)
    # 40000 tokens: mid needs 18+2.8 GB > 20; small needs 4+2.8 -> fits
    fit = choose(URL, list_models(URL), None, 40000)
    assert fit == Fit(model="small", num_ctx=40000)


def test_choose_all_fail_error_lists_tried_models_and_rpipe_model_set(mock):
    fake = three_models()
    fake.free = 1 * GB  # another program holds the GPU
    fake.install(mock)
    with pytest.raises(LlmError) as e:
        choose(URL, list_models(URL), None, None)
    msg = str(e.value)
    assert "big" in msg and "mid" in msg and "small" in msg
    assert "rpipe model set" in msg


def test_choose_falls_through_when_small_model_context_below_16k(mock):
    fake = FakeOllama({
        "tiny-ctx": {"size": 1 * GB, "params": 9 * GB, "ctx": 8192},
        "ok": {"size": 1 * GB, "params": 1 * GB, "ctx": 32768},
    })
    fake.install(mock)
    assert choose(URL, list_models(URL), None, None).model == "ok"


def test_resolve_explicit_values_make_no_requests(mock, tmp_path):
    route = mock.route().mock(return_value=httpx.Response(500))
    assert resolve(URL, tmp_path, "qwen3:14b", 22000) == Fit(model="qwen3:14b", num_ctx=22000)
    assert not route.called
    assert not (tmp_path / "cache" / "llm_fit.json").exists()


def test_resolve_uses_cache_and_reprobes_on_key_change(mock, tmp_path):
    fake = three_models()
    fake.install(mock)
    first = resolve(URL, tmp_path, "auto", "auto")
    assert first.model == "mid"
    assert (tmp_path / "cache" / "llm_fit.json").exists()

    fake.generate_calls.clear()
    assert resolve(URL, tmp_path, "auto", "auto") == first
    assert fake.generate_calls == []

    fake.models["mid"]["digest"] = "new"
    resolve(URL, tmp_path, "auto", "auto")
    assert fake.probes()

    fake.generate_calls.clear()
    resolve(URL, tmp_path, "auto", "auto", force=True)
    assert fake.probes()


def test_resolve_cached_model_no_longer_installed_is_not_used(mock, tmp_path):
    fake = three_models()
    fake.install(mock)
    resolve(URL, tmp_path, "auto", "auto")
    path = tmp_path / "cache" / "llm_fit.json"
    data = json.loads(path.read_text())
    # keep the key valid but point at a model that is gone
    data["model"] = "gone"
    path.write_text(json.dumps(data))
    fake.generate_calls.clear()
    assert resolve(URL, tmp_path, "auto", "auto").model == "mid"
    assert fake.probes()


def test_resolve_pinning_changes_the_cache_key(mock, tmp_path):
    fake = three_models()
    fake.install(mock)
    resolve(URL, tmp_path, "auto", "auto")
    fake.generate_calls.clear()
    fit = resolve(URL, tmp_path, "small", "auto")
    assert fit.model == "small" and fake.probes()


def test_resolve_cache_write_is_atomic(mock, tmp_path):
    three_models().install(mock)
    resolve(URL, tmp_path, "auto", "auto")
    assert [p.name for p in (tmp_path / "cache").iterdir()] == ["llm_fit.json"]


def test_resolve_corrupt_cache_is_ignored_and_rewritten(mock, tmp_path):
    three_models().install(mock)
    path = tmp_path / "cache" / "llm_fit.json"
    path.parent.mkdir()
    path.write_text("{not json")
    fit = resolve(URL, tmp_path, "auto", "auto")
    assert fit.model == "mid"
    assert json.loads(path.read_text())["model"] == "mid"


def test_resolve_error_is_not_cached(mock, tmp_path):
    fake = three_models()
    fake.free = 1 * GB
    fake.install(mock)
    with pytest.raises(LlmError):
        resolve(URL, tmp_path, "auto", "auto")
    assert not (tmp_path / "cache" / "llm_fit.json").exists()




def test_list_models_skips_a_model_with_a_broken_show_and_logs_it(mock):
    fake = FakeOllama({
        "good": {"size": 1, "params": 1000, "ctx": 40960},
        "bad": {"size": 1, "params": 1000},
        "worse": {"size": 1, "params": 1000},
    })
    fake.install(mock)
    good_show = fake.show

    def show(request):
        name = json.loads(request.content)["model"]
        if name == "bad":
            return httpx.Response(500, text="boom")
        if name == "worse":
            return httpx.Response(200, text="not json")
        return good_show(request)

    mock.post(f"{URL}/api/show").mock(side_effect=show)
    notes: list[str] = []
    models = list_models(URL, log=notes.append)
    assert [m.name for m in models] == ["good"]
    assert any("bad" in n for n in notes) and any("worse" in n for n in notes)


def test_choose_pinned_ctx_skips_models_trained_on_less_context(mock):
    fake = FakeOllama({
        "short": {"size": 4 * GB, "params": 9 * GB, "ctx": 8192},
        "long": {"size": 4 * GB, "params": 4 * GB, "ctx": 40960},
    }, free=20 * GB, kv_per_token=10_000)
    fake.install(mock)
    models = [info("short", 9 * GB, ctx=8192), info("long", 4 * GB, ctx=40960)]
    fit = choose(URL, models, None, 16384)
    assert fit == Fit(model="long", num_ctx=16384)
    assert all(c["model"] != "short" for c in fake.probes())
