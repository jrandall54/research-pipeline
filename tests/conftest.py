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


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


class FakeLLM:
    """Scripted stand-in for the local model: pops one response per generate call."""

    model_name = "fake-llm"

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
        return summary() if schema.__name__ == "Summary" else critique()
