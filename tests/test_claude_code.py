import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
from pydantic import BaseModel

from research_pipeline.backends.claude_code import (
    CALL_TIMEOUT,
    ClaudeCodeClient,
    find_claude,
    logged_in,
    resolve_model,
)
from research_pipeline.llm import LlmError, LlmLimitReached, LlmTimeout


class Out(BaseModel):
    ok: bool


def ok(stdout: str, code: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, stdout, stderr)


GOOD = '{"structured_output": {"ok": true}, "is_error": false}'


class FakeRunner:
    """Records calls and replays canned results (the last repeats); an Exception is raised."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []
        self.cwd_was_empty = None

    def __call__(self, argv, stdin, timeout, cwd, env):
        self.calls.append((argv, stdin, timeout, cwd, env))
        self.cwd_was_empty = Path(cwd).is_dir() and not any(Path(cwd).iterdir())
        r = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(r, Exception):
            raise r
        return r


def client(run, model="sonnet"):
    return ClaudeCodeClient(model, "/opt/claude", runner=run)


def test_argv_pins_every_safety_flag():
    argv = ClaudeCodeClient("auto", "/opt/claude").argv("SYS", Out)
    for flag in ("-p", "--no-session-persistence", "--strict-mcp-config"):
        assert flag in argv
    assert argv[0] == "/opt/claude"
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--system-prompt") + 1] == "SYS"
    assert argv[argv.index("--output-format") + 1] == "json"
    assert json.loads(argv[argv.index("--json-schema") + 1]) == Out.model_json_schema()
    assert "--bare" not in argv


def test_prompt_goes_on_stdin_in_empty_temp_dir_without_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    run = FakeRunner(ok(GOOD))
    ClaudeCodeClient("haiku", "/opt/claude", runner=run).generate("S", "U" * 200_000, Out)
    argv, stdin, timeout, cwd, env = run.calls[0]
    assert stdin == "U" * 200_000 and "U" * 1000 not in " ".join(argv)
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-test"
    assert run.cwd_was_empty and not cwd.exists()
    assert timeout == CALL_TIMEOUT


def test_returns_validated_structured_output():
    assert client(FakeRunner(ok(GOOD))).generate("S", "U", Out) == Out(ok=True)


def test_banner_line_before_json_is_tolerated():
    run = FakeRunner(ok("Update available\n" + GOOD + "\n"))
    assert client(run).generate("S", "U", Out) == Out(ok=True)


def test_invalid_output_retried_once_then_llmerror():
    bad = ok('{"structured_output": {"nope": 1}, "is_error": false}')
    run = FakeRunner(bad)
    with pytest.raises(LlmError, match="invalid output twice"):
        client(run).generate("S", "U", Out)
    assert len(run.calls) == 2
    assert "not valid" in run.calls[1][1]


def test_invalid_output_then_good_succeeds():
    run = FakeRunner(ok("not json at all"), ok(GOOD))
    assert client(run).generate("S", "U", Out) == Out(ok=True)
    assert len(run.calls) == 2


@pytest.mark.parametrize("code", [0, 1])
@pytest.mark.parametrize(
    "text", ["You've hit your session limit", "weekly limit reached", "out of credits"]
)
def test_limit_text_raises_limit_reached(text, code):
    env = json.dumps({"is_error": True, "result": text})
    with pytest.raises(LlmLimitReached):
        client(FakeRunner(ok(env, code))).generate("S", "U", Out)


def test_not_logged_in_says_how_to_log_in():
    env = json.dumps({"is_error": True, "result": "Not logged in"})
    with pytest.raises(LlmError, match="run `claude` once and log in"):
        client(FakeRunner(ok(env, 1))).generate("S", "U", Out)


def test_timeout_raises_llmtimeout():
    run = FakeRunner(subprocess.TimeoutExpired("claude", 1))
    with pytest.raises(LlmTimeout):
        client(run).generate("S", "U", Out)


def test_missing_binary_names_model_setup():
    with pytest.raises(LlmError, match="rpipe model setup"):
        client(FakeRunner(FileNotFoundError("claude"))).generate("S", "U", Out)


def test_old_cli_rejecting_tools_flag_says_update():
    run = FakeRunner(ok("", 1, "error: unknown option '--tools'"))
    with pytest.raises(LlmError, match="update Claude Code"):
        client(run).generate("S", "U", Out)


def test_other_failure_is_llmerror():
    with pytest.raises(LlmError, match="Claude Code failed: boom"):
        client(FakeRunner(ok("", 2, "boom"))).generate("S", "U", Out)


def test_resolve_model():
    assert resolve_model("auto") == "sonnet"
    assert resolve_model("haiku") == "haiku"
    assert resolve_model("claude-sonnet-5-5") == "claude-sonnet-5-5"
    with pytest.raises(ValueError, match="sonnet, haiku, opus"):
        resolve_model("gpt-4")


def _exe(p: Path) -> str:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/bin/sh\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


def test_find_claude_prefers_recorded_then_path_then_local_bin(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert find_claude(None) is None
    local = _exe(home / ".local" / "bin" / "claude")
    assert find_claude(None) == local
    on_path = _exe(tmp_path / "bin" / "claude")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert find_claude(None) == on_path
    recorded = _exe(tmp_path / "rec" / "claude")
    assert find_claude(recorded) == recorded
    assert find_claude(str(tmp_path / "gone")) == on_path


def test_logged_in_uses_auth_status_exit_code():
    run = FakeRunner(ok("", 0))
    assert logged_in("/opt/claude", run) is True
    assert run.calls[0][0] == ["/opt/claude", "auth", "status"]
    assert logged_in("/opt/claude", FakeRunner(ok("", 1))) is False
    assert logged_in("/opt/claude", FakeRunner(FileNotFoundError())) is False


def test_check_requires_login_then_one_tiny_call():
    with pytest.raises(LlmError, match="log in"):
        client(FakeRunner(ok("", 1))).check()
    run = FakeRunner(ok("", 0), ok(GOOD))
    assert (
        client(run).check()
        == "Claude Code is logged in at /opt/claude and model sonnet answered."
    )
    assert len(run.calls) == 2


def test_check_with_missing_or_moved_binary_names_model_setup():
    with pytest.raises(LlmError, match="not found at /opt/claude.*rpipe model setup"):
        client(FakeRunner(FileNotFoundError())).check()


def test_check_when_logged_out_still_says_log_in():
    with pytest.raises(LlmError, match="not logged in"):
        client(FakeRunner(ok("", 1))).check()


def test_argv_defaults_to_low_effort_and_honours_the_setting():
    assert _flag(ClaudeCodeClient("auto", "/opt/claude").argv("S", Out), "--effort") == "low"
    assert _flag(ClaudeCodeClient("auto", "/opt/claude", effort="high").argv("S", Out), "--effort") == "high"


def test_effort_does_not_remove_any_safety_flag():
    argv = ClaudeCodeClient("haiku", "/opt/claude", effort="medium").argv("SYS", Out)
    for flag in ("-p", "--no-session-persistence", "--strict-mcp-config", "--tools",
                 "--setting-sources", "--permission-mode", "--system-prompt", "--json-schema"):
        assert flag in argv
    assert _flag(argv, "--permission-mode") == "dontAsk" and _flag(argv, "--tools") == ""


def _flag(argv, name):
    return argv[argv.index(name) + 1]
