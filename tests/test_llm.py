import json

import httpx
import pytest
import respx
from pydantic import BaseModel, ValidationError
from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.llm import LlmError, OllamaClient
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
    assert (cfg.llm_model, cfg.llm_url, cfg.llm_num_ctx) == ("qwen3:14b", URL, 32768)
    with pytest.raises(ValidationError):
        Config(vault_path="/x", llm_num_ctx=100)


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

    monkeypatch.setattr(cli, "build_llm", lambda cfg: Ok())
    res = model(vault, "check")
    assert res.exit_code == 0 and "ready" in res.output

    class Bad:
        def check(self):
            raise LlmError("start it with `ollama serve`")

    monkeypatch.setattr(cli, "build_llm", lambda cfg: Bad())
    res = model(vault, "check")
    assert res.exit_code == 1 and "ollama serve" in res.output
