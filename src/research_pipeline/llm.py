"""Client for a local model server (Ollama) that returns validated, structured answers."""

import json
from collections.abc import Callable
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

SERVE_HINT = "start it with `ollama serve` (and check the server address with `rpipe model show`; change it with `rpipe model set-url URL`)"


CHECK_TIMEOUT = 10.0  # seconds; listing models is quick, unlike generating
CALL_TIMEOUT = 600.0  # seconds one Ollama model call may take (a slow CPU can exceed it on a long paper)
# Output token limit sent with every Ollama call: a guard against a model that never stops
# writing. A summary or critique is about 2,000 tokens at most, so this is a wide margin.
NUM_PREDICT = 4096
_STAT_FIELDS = ("prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration")


def _with_tag(name: str) -> str:
    """Ollama lists `llama3.2` as `llama3.2:latest`; compare names with the tag spelled out."""
    last = name.rsplit("/", 1)[-1]
    return name if ":" in last else f"{name}:latest"


class LlmError(Exception):
    """Any failure talking to, or getting a usable answer from, the local model."""


class LlmTimeout(LlmError):
    """The model server did not answer in time (a run stops calling the model after one)."""


class LlmLimitReached(LlmError):
    """A subscription usage limit was hit; a run stops calling the model without counting a failure."""


class PrivacyError(LlmError):
    """Text that must stay on this machine was about to go to a backend that is not local."""


NO_MODEL = "No summary model is set up (abstracts-only mode). Run `rpipe model setup` to choose one."


class LLM(Protocol):
    name: str  # backend name, e.g. "ollama"
    local: bool  # True only if prompts never leave this machine
    model_name: str
    num_ctx: int

    def generate(self, system: str, user: str, schema: type[T]) -> T:
        """Ask the model and return its answer as `schema`; raises LlmError on any failure."""
        ...


def require_local(llm: LLM, what: str) -> None:
    """Raise PrivacyError unless `llm` is local; a missing `local` attribute counts as not local."""
    if getattr(llm, "local", False) is not True:
        name = getattr(llm, "name", "unknown")
        raise PrivacyError(
            f"{what} never leaves this machine, but the {name} backend is not local; nothing was sent"
        )


class OllamaClient:
    """Talks to Ollama's /api/chat with JSON-schema-constrained output and thinking off."""

    name = "ollama"
    local = True

    def __init__(
        self, url: str, model: str, num_ctx: int, timeout: float = CALL_TIMEOUT, num_predict: int = NUM_PREDICT
    ) -> None:
        self.url = url.rstrip("/")
        self.model_name = model
        self.num_ctx = num_ctx
        self.timeout = timeout
        self.num_predict = num_predict
        # Timing numbers from the last reply (tokens read and written, nanoseconds), or None.
        self.last_stats: dict[str, int] | None = None

    def _pull_hint(self) -> str:
        return f"model {self.model_name!r} is not installed: run `ollama pull {self.model_name}`"

    def _request(
        self, method: str, path: str, timeout: float | None = None, **kwargs
    ) -> httpx.Response:
        timeout = self.timeout if timeout is None else timeout
        try:
            resp = httpx.request(method, f"{self.url}{path}", timeout=timeout, **kwargs)
        except httpx.ConnectError as e:
            raise LlmError(f"cannot reach Ollama at {self.url}: {SERVE_HINT}") from e
        except httpx.TimeoutException as e:
            raise LlmTimeout(f"Ollama at {self.url} timed out after {timeout:g}s") from e
        except httpx.HTTPError as e:
            raise LlmError(f"Ollama request failed: {e}") from e
        if resp.status_code == 404:
            raise LlmError(self._pull_hint())
        if resp.status_code >= 400:
            raise LlmError(f"Ollama returned HTTP {resp.status_code}: {resp.text[:200]}")
        return resp

    def generate(self, system: str, user: str, schema: type[T]) -> T:
        error: ValidationError | ValueError | None = None
        for _attempt in range(2):
            content_user = user
            if error is not None:
                content_user = (
                    f"{user}\n\nYour previous answer was not valid JSON for the required schema "
                    f"({error}). Answer again with valid JSON only."
                )
            body = {
                "model": self.model_name,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": content_user},
                ],
                "stream": False,
                "think": False,
                "format": schema.model_json_schema(),
                "options": {"num_ctx": self.num_ctx, "temperature": 0, "num_predict": self.num_predict},
            }
            resp = self._request("POST", "/api/chat", json=body)
            try:
                self._record_stats(resp.json())
            except ValueError:
                pass  # the content check below reports the bad body
            try:
                content = resp.json()["message"]["content"]
                return schema.model_validate_json(content)
            except (ValidationError, ValueError, KeyError, TypeError) as e:
                error = e if isinstance(e, (ValidationError, ValueError)) else ValueError(str(e))
        raise LlmError(f"model returned invalid output twice: {error}")

    def _record_stats(self, data: object) -> None:
        stats = (
            {k: data[k] for k in _STAT_FIELDS if isinstance(data.get(k), int) and not isinstance(data.get(k), bool)}
            if isinstance(data, dict) else {}
        )
        self.last_stats = stats or None

    def ping(self) -> None:
        """Raise LlmError (with a fix) unless the server answers; says nothing about models."""
        self._request("GET", "/api/tags", timeout=CHECK_TIMEOUT)

    def check(self) -> str:
        """Status line if the server is up and the model installed; LlmError with a fix otherwise."""
        resp = self._request("GET", "/api/tags", timeout=CHECK_TIMEOUT)
        try:
            names = [m["name"] for m in resp.json().get("models", [])]
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise LlmError(f"unexpected reply from Ollama at {self.url}: {e}") from e
        if _with_tag(self.model_name) not in {_with_tag(n) for n in names}:
            raise LlmError(self._pull_hint())
        return f"Ollama is running at {self.url} and model {self.model_name} is installed."


def pull_model(url: str, tag: str, progress: Callable[[str], None]) -> None:
    """Download model `tag` through Ollama's /api/pull, streaming status lines to `progress`.

    `progress` is called when the status text or the whole-percent changes (not on every
    chunk). Raises LlmError on an HTTP error, an unreachable server or a streamed error."""
    base = url.rstrip("/")
    last: tuple[str, int | None] | None = None
    try:
        with httpx.stream("POST", f"{base}/api/pull", json={"model": tag},
                          timeout=httpx.Timeout(30.0, read=600.0)) as resp:
            if resp.status_code >= 400:
                resp.read()
                raise LlmError(f"Ollama returned HTTP {resp.status_code} while pulling {tag}: {resp.text[:200]}")
            for line in resp.iter_lines():
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if "error" in msg:
                    raise LlmError(f"pulling {tag} failed: {msg['error']}")
                status = str(msg.get("status", ""))
                total, done = msg.get("total"), msg.get("completed")
                pct = int(done * 100 / total) if isinstance(total, int) and total > 0 and isinstance(done, int) else None
                if (status, pct) != last:
                    last = (status, pct)
                    progress(f"{status} {pct}%" if pct is not None else status)
    except httpx.ConnectError as e:
        raise LlmError(f"cannot reach Ollama at {base}: {SERVE_HINT}") from e
    except httpx.HTTPError as e:
        raise LlmError(f"pulling {tag} failed: {e}") from e
