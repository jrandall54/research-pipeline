"""Shared test fixtures."""

import hashlib
import re

import numpy as np
import pytest

from research_pipeline.models import Section
from research_pipeline.summarize import Claim, Critique, Summary


class FakeEmbedder:
    """Deterministic hashed bag-of-words embedder: texts sharing words are closer."""

    model_name = "fake-bow"
    DIM = 256

    def __init__(self) -> None:
        self.calls = 0  # number of texts embedded so far

    def embed(self, texts: list[str]) -> np.ndarray:
        self.calls += len(texts)
        out = np.zeros((len(texts), self.DIM), dtype=np.float32)
        for i, text in enumerate(texts):
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                h = int.from_bytes(hashlib.md5(word.encode()).digest()[:4], "big")
                out[i, h % self.DIM] += 1.0
            norm = np.linalg.norm(out[i])
            if norm > 0:
                out[i] /= norm
        return out


@pytest.fixture(autouse=True)
def _no_real_services(request, tmp_path, monkeypatch):
    """No test may touch the owner's real systemd units (the app service, the nightly timer).

    Commands such as `rpipe profile add-topic` restart the background app when it is installed;
    without this, running the tests restarted the real app until systemd gave up. Tests that
    check service handling set their own unit dir and runner on top of this.
    """
    from research_pipeline import schedule

    monkeypatch.setattr(schedule, "default_unit_dir", lambda: tmp_path / "no-real-units")

    def refuse(cmd: list[str]) -> int:
        raise AssertionError(f"a test tried to run a real system command: {cmd}")

    if "real_system_runner" not in request.keywords:  # a test of system_runner itself fakes subprocess instead
        monkeypatch.setattr(schedule, "system_runner", refuse)


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


class FakeLLM:
    """Scripted stand-in for the local model: pops one response per generate call."""

    model_name = "fake-llm"
    num_ctx = 32768

    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []  # (system, user) per call

    def generate(self, system: str, user: str, schema):
        self.calls.append((system, user))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


SECTIONS = [
    Section(title="Abstract", text="We study X."),
    Section(title="1 Introduction", text="X matters because Y."),
    Section(title="2 Method", text="We train a classifier."),
    Section(title="3 Results", text="Accuracy is 91%."),
]


def summary(cite: str = "2 Method") -> Summary:
    return Summary(
        one_line="A classifier for X.",
        problem=Claim(text="X is hard.", sections=["1 Introduction"]),
        approach=Claim(text="They train a classifier.", sections=[cite]),
        results=Claim(text="91% accuracy.", sections=["3 results "]),  # case/space ignored
        limitations=Claim(text="Not stated in the paper.", sections=["Abstract"]),
    )


def critique(cite: str = "3 Results") -> Critique:
    return Critique(
        strengths=[Claim(text="Clear method.", sections=["2 Method"])],
        weaknesses=[Claim(text="One dataset only.", sections=[cite])],
        questions=["Does it generalise?"],
    )


class AlwaysSummary(FakeLLM):
    """Answers every request with a valid summary or critique, whatever the order."""

    def generate(self, system, user, schema):
        self.calls.append((system, user))
        if schema.__name__ == "DiagramAnswer":
            return schema(diagram=None)  # no clear shape
        return summary() if schema.__name__ == "Summary" else critique()


@pytest.fixture(autouse=True)
def no_real_model_or_arxiv(monkeypatch):
    """Never reach a real Ollama or arXiv: onboarding finds the model unavailable and any
    unstubbed arXiv client fails loudly. Tests that need them monkeypatch these again."""
    from research_pipeline import cli
    from research_pipeline.llm import LlmError

    def down(config):
        raise LlmError("no model in tests")

    def no_arxiv(config):
        raise AssertionError("test reached the real arXiv: stub cli.make_arxiv")

    monkeypatch.setattr(cli, "_onboarding_llm", down)
    monkeypatch.setattr(cli, "make_arxiv", no_arxiv)


@pytest.fixture(autouse=True)
def pinned_machine_and_os(monkeypatch):
    """Never probe this computer: no GPU, no Ollama, no Claude Code, too little memory (4 GB) for a
    model on the CPU (so the walkthrough recommends `none`), and the OS is Linux."""
    from research_pipeline import cli
    from research_pipeline.machine import MachineInfo

    monkeypatch.setattr(cli, "detect_machine", lambda config: MachineInfo(
        os="Linux", ram_gb=4.0, gpu=None, ollama_installed=False, ollama_running=False,
        ollama_models=[], claude_path=None, claude_logged_in=False))
    monkeypatch.setattr(cli, "_platform", lambda: "Linux", raising=False)


@pytest.fixture(autouse=True)
def _no_real_machine_probe(monkeypatch):
    """The fit cache key asks about the machine (nvidia-smi, systemctl); tests never run those."""
    monkeypatch.setattr("research_pipeline.ollama_fit.machine_key", lambda url: {})
