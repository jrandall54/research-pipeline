import json

import httpx
import pytest
import respx
from pydantic import BaseModel, ValidationError
from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.llm import LlmError, OllamaClient, PrivacyError, require_local
from research_pipeline.models import Config
from research_pipeline.vault import init_vault, load_config

URL = "http://localhost:11434"
runner = CliRunner()


class Answer(BaseModel):
    text: str


def client() -> OllamaClient:
    return OllamaClient(URL, "qwen3:14b", 32768)


def reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})


@respx.mock
def test_generate_sends_required_options():
    route = respx.post(f"{URL}/api/chat").mock(return_value=reply('{"text": "hi"}'))
    out = client().generate("sys", "usr", Answer)
    assert out == Answer(text="hi")
    body = json.loads(route.calls[0].request.content)
    assert body["stream"] is False and body["think"] is False
    assert body["options"]["num_ctx"] == 32768 and body["options"]["temperature"] == 0
    assert body["format"] == Answer.model_json_schema()
    assert body["model"] == "qwen3:14b"
    assert body["messages"][0] == {"role": "system", "content": "sys"}


@respx.mock
def test_generate_sends_num_predict():
    route = respx.post(f"{URL}/api/chat").mock(return_value=reply('{"text": "hi"}'))
    client().generate("s", "u", Answer)
    assert json.loads(route.calls[0].request.content)["options"]["num_predict"] == 4096
    respx.post(f"{URL}/api/chat").mock(return_value=reply('{"text": "hi"}'))
    OllamaClient(URL, "m", 8192, num_predict=512).generate("s", "u", Answer)
    assert json.loads(respx.calls[-1].request.content)["options"]["num_predict"] == 512


@respx.mock
def test_last_stats_records_timing_fields():
    stats = {"prompt_eval_count": 21000, "prompt_eval_duration": 9_000_000_000, "eval_count": 400, "eval_duration": 22_000_000_000}
    respx.post(f"{URL}/api/chat").mock(return_value=httpx.Response(
        200, json={"message": {"role": "assistant", "content": '{"text": "hi"}'}, **stats, "model": "m"}))
    c = client()
    assert c.last_stats is None
    c.generate("s", "u", Answer)
    assert c.last_stats == stats


@respx.mock
def test_last_stats_none_without_fields():
    respx.post(f"{URL}/api/chat").mock(return_value=reply('{"text": "hi"}'))
    c = client()
    c.generate("s", "u", Answer)
    assert c.last_stats is None


@respx.mock
def test_output_cut_by_the_cap_raises_llm_error():
    respx.post(f"{URL}/api/chat").mock(side_effect=[reply('{"text": "cut off mid'), reply('{"text": "cut off mid')])
    with pytest.raises(LlmError, match="invalid output twice"):
        client().generate("s", "u", Answer)


@respx.mock
def test_invalid_json_retries_once_then_raises():
    respx.post(f"{URL}/api/chat").mock(side_effect=[reply("nope"), reply('{"wrong": 1}')])
    with pytest.raises(LlmError):
        client().generate("s", "u", Answer)


@respx.mock
def test_invalid_json_then_good_returns_and_second_request_has_error():
    route = respx.post(f"{URL}/api/chat").mock(side_effect=[reply("nope"), reply('{"text": "ok"}')])
    assert client().generate("s", "u", Answer) == Answer(text="ok")
    second = json.loads(route.calls[1].request.content)
    user = second["messages"][-1]["content"]
    assert user.startswith("u") and "not valid" in user.lower() and "json" in user.lower()
    assert len(route.calls) == 2


@respx.mock
def test_connection_refused_raises_llmerror_with_hint():
    respx.post(f"{URL}/api/chat").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LlmError, match="ollama serve"):
        client().generate("s", "u", Answer)


@respx.mock
def test_timeout_raises_llmerror():
    respx.post(f"{URL}/api/chat").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(LlmError, match="timed out"):
        client().generate("s", "u", Answer)


@respx.mock
def test_model_not_found_hint():
    respx.post(f"{URL}/api/chat").mock(return_value=httpx.Response(404, json={"error": "model not found"}))
    with pytest.raises(LlmError, match="ollama pull qwen3:14b"):
        client().generate("s", "u", Answer)


@respx.mock
def test_server_error_raises_llmerror():
    respx.post(f"{URL}/api/chat").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(LlmError, match="500"):
        client().generate("s", "u", Answer)


@respx.mock
def test_check_ok_lists_model():
    respx.get(f"{URL}/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen3:14b"}]})
    )
    assert "qwen3:14b" in client().check()


@respx.mock
def test_check_missing_model_raises():
    respx.get(f"{URL}/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "llama3:8b"}]})
    )
    with pytest.raises(LlmError, match="ollama pull qwen3:14b"):
        client().check()


@respx.mock
def test_check_unreachable_raises_with_hint():
    respx.get(f"{URL}/api/tags").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LlmError, match="ollama serve"):
        client().check()


def test_config_defaults_and_num_ctx_validation():
    cfg = Config(vault_path="/x")
    assert (cfg.llm_model, cfg.llm_url, cfg.llm_num_ctx) == ("auto", URL, "auto")
    for bad in (100, "big"):
        with pytest.raises(ValidationError, match="auto"):
            Config(vault_path="/x", llm_num_ctx=bad)
    assert Config(vault_path="/x", llm_num_ctx=22000).llm_num_ctx == 22000


def test_old_vault_config_with_explicit_values_loads_unchanged(vault):
    cfg = vault / "config.yaml"
    text = cfg.read_text().replace("llm_model: auto", "llm_model: qwen3:14b").replace(
        "llm_num_ctx: auto", "llm_num_ctx: 32768")
    cfg.write_text(text)
    loaded = load_config(cfg)
    assert (loaded.llm_model, loaded.llm_num_ctx) == ("qwen3:14b", 32768)


def test_init_writes_auto_defaults(vault):
    text = (vault / "config.yaml").read_text()
    assert "llm_model: auto" in text and "llm_num_ctx: auto" in text


@pytest.fixture
def vault(tmp_path):
    path = tmp_path / "vault"
    init_vault(path, topics=["LLM security"])
    return path


def model(vault, *args):
    return runner.invoke(cli.app, ["model", *args, "--vault", str(vault)])


def test_model_set_show_preserves_comments(vault):
    cfg = vault / "config.yaml"
    comments = [ln for ln in cfg.read_text().splitlines() if ln.startswith("#")]
    res = model(vault, "set", "qwen3:8b")
    assert res.exit_code == 0 and "qwen3:8b" in res.output
    assert load_config(cfg).llm_model == "qwen3:8b"
    text = cfg.read_text()
    assert all(c in text for c in comments)
    res = model(vault, "show")
    assert res.exit_code == 0 and "qwen3:8b" in res.output and URL in res.output


def test_model_set_empty_exits_1_and_leaves_file(vault):
    cfg = vault / "config.yaml"
    before = cfg.read_text()
    res = model(vault, "set", "")
    assert res.exit_code == 1 and "Traceback" not in res.output
    assert cfg.read_text() == before


def test_model_check_cli(vault, monkeypatch):
    class Ok:
        def check(self):
            return "ready"

    monkeypatch.setattr(cli, "build_llm", lambda cfg, vault, log=None: Ok())
    res = model(vault, "check")
    assert res.exit_code == 0 and "ready" in res.output

    class Bad:
        def check(self):
            raise LlmError("start it with `ollama serve`")

    monkeypatch.setattr(cli, "build_llm", lambda cfg, vault, log=None: Bad())
    res = model(vault, "check")
    assert res.exit_code == 1 and "ollama serve" in res.output


@respx.mock
def test_check_matches_untagged_name_against_latest():
    respx.get(f"{URL}/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "llama3.2:latest"}]})
    )
    assert "llama3.2" in OllamaClient(URL, "llama3.2", 32768).check()


@respx.mock
def test_check_uses_short_timeout():
    route = respx.get(f"{URL}/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen3:14b"}]})
    )
    client().check()
    assert route.calls[0].request.extensions["timeout"]["read"] == 10.0


def test_model_set_url_and_context(vault):
    cfg = vault / "config.yaml"
    assert model(vault, "set-url", "http://gpu-box:11434").exit_code == 0
    assert model(vault, "set-context", "16384").exit_code == 0
    loaded = load_config(cfg)
    assert (loaded.llm_url, loaded.llm_num_ctx) == ("http://gpu-box:11434", 16384)
    out = model(vault, "show").output
    assert "gpu-box" in out and "16384" in out and "model " in out


def test_model_set_url_and_context_reject_bad_values(vault):
    cfg = vault / "config.yaml"
    before = cfg.read_text()
    for args in (("set-url", "gpu-box:11434"), ("set-url", "  "), ("set-context", "100")):
        res = model(vault, *args)
        assert res.exit_code == 1 and "Traceback" not in res.output
        assert res.output.strip()
        assert cfg.read_text() == before


def test_connect_error_message_names_set_url():
    from research_pipeline.llm import SERVE_HINT

    assert "rpipe model set-url" in SERVE_HINT


def test_model_set_auto_and_set_context_auto_keep_comments(vault):
    cfg = vault / "config.yaml"
    assert model(vault, "set", "qwen3:8b").exit_code == 0
    assert model(vault, "set-context", "16384").exit_code == 0
    comments = [ln for ln in cfg.read_text().splitlines() if ln.startswith("#")]
    assert model(vault, "set", "auto").exit_code == 0
    assert model(vault, "set-context", "auto").exit_code == 0
    loaded = load_config(cfg)
    assert (loaded.llm_model, loaded.llm_num_ctx) == ("auto", "auto")
    assert all(c in cfg.read_text() for c in comments)


def test_set_context_rejects_bad_values_and_leaves_file(vault):
    cfg = vault / "config.yaml"
    before = cfg.read_text()
    for arg in ("big", "100", "1.5"):
        res = model(vault, "set-context", arg)
        assert res.exit_code == 1 and "Traceback" not in res.output and "auto" in res.output
    assert cfg.read_text() == before


def test_model_show_reports_unknown_choice_when_probing_times_out(vault, monkeypatch):
    from research_pipeline.ollama_fit import ProbeTimeout

    def hung(*args, **kwargs):
        raise ProbeTimeout("probing Ollama took longer than 10 seconds")

    monkeypatch.setattr(cli.ollama_fit, "cached_fit", hung)
    model(vault, "set", "auto")
    res = model(vault, "show")
    assert res.exit_code == 0
    # The configured settings are printed first; the timeout only replaces the last-choice line.
    assert res.output.index("model  ") < res.output.index("server  ") < res.output.index("context window")
    assert "last choice: unknown. Ollama did not answer in time" in res.output


def test_ollama_client_is_named_and_local():
    c = OllamaClient("http://x", "m", 8192)
    assert (c.name, c.local) == ("ollama", True)


def test_require_local_blocks_non_local_and_unknown():
    class Cloud:
        name, local = "claude-code", False

    class Unknown:
        name = "x"

    require_local(OllamaClient("http://x", "m", 8192), "your shelf")  # no raise
    for llm in (Cloud(), Unknown()):
        with pytest.raises(PrivacyError, match="your shelf never leaves this machine"):
            require_local(llm, "your shelf")


@respx.mock
def test_gpu_share_from_ps():
    llm = OllamaClient(URL, "m", num_ctx=32768)
    route = respx.get(f"{URL}/api/ps")
    route.mock(return_value=httpx.Response(200, json={"models": [
        {"name": "other:latest", "size": 1000, "size_vram": 0},
        {"name": "m:latest", "size": 1000, "size_vram": 930},
    ]}))
    assert llm.gpu_share() == 0.93
    route.mock(return_value=httpx.Response(200, json={"models": [
        {"name": "other:latest", "size": 1000, "size_vram": 1000}]}))
    assert llm.gpu_share() is None
    route.mock(return_value=httpx.Response(500, text="boom"))
    assert llm.gpu_share() is None
    route.mock(side_effect=httpx.ConnectError("down"))
    assert llm.gpu_share() is None


def _bench(read_tps: float, write_tps: float, model: str = "m:latest"):
    from datetime import datetime

    from research_pipeline.bench import Bench
    return Bench(model=model, num_ctx=32768, read_tps=read_tps, write_tps=write_tps,
                 seconds_per_paper=1.0, paper_tokens=22000, measured_at=datetime(2026, 11, 24))


def test_call_timeout_cpu_bench():
    from research_pipeline.llm import call_timeout
    assert call_timeout(32768, _bench(80, 18)) == pytest.approx(1.5 * (409.6 + 227.6), rel=1e-3)


def test_call_timeout_fast_gpu_keeps_600():
    from research_pipeline.llm import call_timeout
    assert call_timeout(32768, _bench(3000, 40)) == 600.0


def test_call_timeout_no_bench():
    from research_pipeline.llm import call_timeout
    assert call_timeout(32768, None) == 600.0


def test_call_timeout_other_model_or_unusable_speeds_keep_600():
    from research_pipeline.llm import call_timeout
    assert call_timeout(32768, _bench(80, 18, "other:7b"), model="m") == 600.0
    assert call_timeout(32768, _bench(80, 18, "m"), model="m:latest") > 600.0  # same model, tag spelled out
    assert call_timeout(32768, _bench(0, 18)) == 600.0
    assert call_timeout(32768, _bench(80, 0)) == 600.0


@respx.mock
def test_load_sends_empty_prompt_with_context_and_keep_alive():
    route = respx.post(f"{URL}/api/generate").mock(return_value=httpx.Response(200, json={"done": True}))
    client().load()
    body = json.loads(route.calls[-1].request.content)
    assert body["model"] == "qwen3:14b" and body["prompt"] == ""
    assert body["options"]["num_ctx"] == 32768 and body["keep_alive"]


@respx.mock
def test_load_waits_at_most_one_normal_call():
    from research_pipeline.llm import CALL_TIMEOUT

    route = respx.post(f"{URL}/api/generate").mock(return_value=httpx.Response(200, json={"done": True}))
    OllamaClient(URL, "qwen3:14b", 32768, timeout=5000.0).load()  # a slow model's raised limit
    assert route.calls[-1].request.extensions["timeout"]["read"] == CALL_TIMEOUT
    OllamaClient(URL, "qwen3:14b", 32768, timeout=30.0).load()
    assert route.calls[-1].request.extensions["timeout"]["read"] == 30.0


@respx.mock
def test_load_failure_raises_llm_error():
    respx.post(f"{URL}/api/generate").mock(return_value=httpx.Response(500, json={"error": "out of memory"}))
    with pytest.raises(LlmError):
        client().load()
