"""Shared test doubles (import with `from fakes import ...`)."""

import json

import httpx
import respx

from research_pipeline.models import PaperMeta

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


def paper(pid="2307.15043", title="Universal and Transferable Adversarial Attacks"):
    return PaperMeta(
        id=pid, title=title, abstract="An abstract.", authors=["Andy Zou", "Zifan Wang"],
        source="arxiv", url=f"https://arxiv.org/abs/{pid}", published="2023-07-27",
    )


class StubArxiv:
    def __init__(self, by_id=None, by_title=None, error=None):
        self.by_id, self.by_title, self.error = by_id or [], by_title or [], error
        self.calls = []

    def fetch_by_ids(self, ids):
        self.calls.append(("id", ids))
        if self.error:
            raise self.error
        return self.by_id

    def search_title(self, query, max_results=5):
        self.calls.append(("title", query))
        if self.error:
            raise self.error
        return self.by_title
