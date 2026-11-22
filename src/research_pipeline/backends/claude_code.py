"""Backend that calls the Claude Code CLI in headless mode (`claude -p`) on the user's own login."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ValidationError

from research_pipeline.llm import LlmError, LlmLimitReached, LlmTimeout, T

CONTEXT_BUDGET = 150_000  # tokens we allow ourselves to send per call
CALL_TIMEOUT = 600.0  # seconds for one generate call
STATUS_TIMEOUT = 15.0  # seconds for `claude auth status`
DEFAULT_MODEL = "sonnet"
ALIASES = ("sonnet", "haiku", "opus")

# (argv, stdin text, timeout seconds, working directory, environment)
Runner = Callable[[list[str], str, float, Path, dict[str, str]], subprocess.CompletedProcess[str]]

LOGIN_HINT = "run `claude` once and log in, then try again"
SETUP_HINT = "run `rpipe model setup` to find or choose the Claude Code install"


def subprocess_runner(
    argv: list[str], stdin: str, timeout: float, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, input=stdin, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env
    )


def resolve_model(name: str) -> str:
    if name == "auto":
        return DEFAULT_MODEL
    if name in ALIASES or name.startswith("claude-"):
        return name
    raise ValueError(
        f"unknown Claude model {name!r}: give sonnet, haiku, opus or a full claude-... model name"
    )


def _is_executable_file(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def find_claude(recorded: str | None) -> str | None:
    """The recorded path if it still works, else PATH, else ~/.local/bin/claude, else None."""
    if recorded and _is_executable_file(Path(recorded)):
        return recorded
    found = shutil.which("claude")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "claude"
    return str(local) if _is_executable_file(local) else None


def _child_env() -> dict[str, str]:
    """The user's environment minus the API key, so the child cannot silently bill the API."""
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    return env


def _auth_problem(cli_path: str, runner: Runner) -> str | None:
    """Why `claude auth status` did not succeed (naming the fix), or None if it did."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            done = runner([cli_path, "auth", "status"], "", STATUS_TIMEOUT, Path(tmp), _child_env())
        except subprocess.TimeoutExpired:
            return f"Claude Code did not answer `claude auth status` within {STATUS_TIMEOUT:g}s; try again"
        except OSError:  # missing or moved binary (the nightly unit may lack ~/.local/bin on PATH)
            return f"Claude Code was not found at {cli_path}: {SETUP_HINT}"
    if done.returncode != 0:
        return f"Claude Code is not logged in: {LOGIN_HINT}"
    return None


def logged_in(cli_path: str, runner: Runner = subprocess_runner) -> bool:
    return _auth_problem(cli_path, runner) is None


def _parse_envelope(stdout: str) -> dict | None:
    """The JSON envelope: the whole output, else the last line that parses (banners come first)."""
    candidates = [stdout] + [ln for ln in reversed(stdout.splitlines()) if ln.strip()]
    for text in candidates:
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _classify(message: str) -> LlmError:
    low = message.lower()
    if re.search(r"limit|credit", low):
        return LlmLimitReached(f"Claude Code usage limit reached: {message[:200]}")
    if re.search(r"log ?in|logged|authenticat", low):
        return LlmError(f"Claude Code is not logged in: {LOGIN_HINT}")
    if re.search(r"unknown option|unrecognized", low):
        return LlmError(
            "this Claude Code does not know a flag rpipe needs: update Claude Code "
            f"(`claude update`). Detail: {message[:200]}"
        )
    return LlmError(f"Claude Code failed: {message[:200]}")


class ClaudeCodeClient:
    """Asks Claude Code (all tools off, no settings, no session saved) for schema-valid JSON."""

    name = "claude-code"
    local = False

    def __init__(
        self,
        model: str,
        cli_path: str,
        *,
        num_ctx: int = CONTEXT_BUDGET,
        timeout: float = CALL_TIMEOUT,
        runner: Runner = subprocess_runner,
    ) -> None:
        self.model_name = resolve_model(model)
        self.cli_path = cli_path
        self.num_ctx = num_ctx
        self.timeout = timeout
        self.runner = runner

    def argv(self, system: str, schema: type[BaseModel]) -> list[str]:
        return [
            self.cli_path, "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(schema.model_json_schema()),
            "--system-prompt", system,
            "--tools", "",
            "--setting-sources", "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--permission-mode", "dontAsk",
            "--model", self.model_name,
        ]  # fmt: skip

    def _call(self, system: str, user: str, schema: type[BaseModel]) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                done = self.runner(
                    self.argv(system, schema), user, self.timeout, Path(tmp), _child_env()
                )
            except subprocess.TimeoutExpired as e:
                raise LlmTimeout(f"Claude Code timed out after {self.timeout:g}s") from e
            except FileNotFoundError as e:
                raise LlmError(f"Claude Code was not found at {self.cli_path}: {SETUP_HINT}") from e
            except OSError as e:
                raise LlmError(f"could not start Claude Code: {e}; {SETUP_HINT}") from e
        envelope = _parse_envelope(done.stdout or "")
        failed = done.returncode != 0 or bool(envelope and envelope.get("is_error"))
        if failed:
            message = str((envelope or {}).get("result") or "")
            message = message or (done.stderr or "").strip() or (done.stdout or "").strip()
            raise _classify(message or f"exit code {done.returncode}")
        if envelope is None:
            raise ValueError("no JSON in Claude Code output")
        return envelope

    def generate(self, system: str, user: str, schema: type[T]) -> T:
        error: ValidationError | ValueError | None = None
        for _ in range(2):
            content_user = user
            if error is not None:
                content_user = (
                    f"{user}\n\nYour previous answer was not valid JSON for the required schema "
                    f"({error}). Answer again with valid JSON only."
                )
            try:
                envelope = self._call(system, content_user, schema)
                return schema.model_validate(envelope["structured_output"])
            except (ValidationError, ValueError, KeyError, TypeError) as e:
                error = e if isinstance(e, (ValidationError, ValueError)) else ValueError(str(e))
        raise LlmError(f"model returned invalid output twice: {error}")

    def check(self) -> str:
        problem = _auth_problem(self.cli_path, self.runner)
        if problem:
            raise LlmError(problem)
        self.generate("Answer with ok true.", "Say ok.", _Ping)
        return f"Claude Code is logged in at {self.cli_path} and model {self.model_name} answered."


class _Ping(BaseModel):
    ok: bool
