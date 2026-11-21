"""Client for a local model server (Ollama) that returns validated, structured answers."""

from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

SERVE_HINT = "start it with `ollama serve` (and check the server address with `rpipe model show`; change it with `rpipe model set-url URL`)"


CHECK_TIMEOUT = 10.0  # seconds; listing models is quick, unlike generating


def _with_tag(name: str) -> str:
    """Ollama lists `llama3.2` as `llama3.2:latest`; compare names with the tag spelled out."""
    last = name.rsplit("/", 1)[-1]
    return name if ":" in last else f"{name}:latest"


class LlmError(Exception):
    """Any failure talking to, or getting a usable answer from, the local model."""


class LLM(Protocol):
    model_name: str

    def generate(self, system: str, user: str, schema: type[T]) -> T:
        """Ask the model and return its answer as `schema`; raises LlmError on any failure."""
        ...


class OllamaClient:
    """Talks to Ollama's /api/chat with JSON-schema-constrained output and thinking off."""

    def __init__(self, url: str, model: str, num_ctx: int, timeout: float = 600.0) -> None:
        self.url = url.rstrip("/")
        self.model_name = model
        self.num_ctx = num_ctx
        self.timeout = timeout

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
            raise LlmError(f"Ollama at {self.url} timed out after {timeout:g}s") from e
        except httpx.HTTPError as e:
            raise LlmError(f"Ollama request failed: {e}") from e
        if resp.status_code == 404:
            raise LlmError(self._pull_hint())
        if resp.status_code >= 400:
            raise LlmError(f"Ollama returned HTTP {resp.status_code}: {resp.text[:200]}")
        return resp

    def generate(self, system: str, user: str, schema: type[T]) -> T:
        error: ValidationError | ValueError | None = None
        for attempt in range(2):
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
                "options": {"num_ctx": self.num_ctx, "temperature": 0},
            }
            resp = self._request("POST", "/api/chat", json=body)
            try:
                content = resp.json()["message"]["content"]
                return schema.model_validate_json(content)
            except (ValidationError, ValueError, KeyError, TypeError) as e:
                error = e if isinstance(e, (ValidationError, ValueError)) else ValueError(str(e))
        raise LlmError(f"model returned invalid output twice: {error}")

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
