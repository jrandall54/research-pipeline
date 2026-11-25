"""Loading the embedding model: no library progress bars or HF_TOKEN nag, and always on the CPU
(no network, no model)."""

import sys
import types

from research_pipeline.embed import SentenceTransformerEmbedder, quiet_model_load

KEYS = ("HF_HUB_DISABLE_PROGRESS_BARS", "TRANSFORMERS_VERBOSITY", "HF_HUB_VERBOSITY")


def test_sets_switches(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    quiet_model_load()
    import os

    assert os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] == "1"
    assert os.environ["TRANSFORMERS_VERBOSITY"] == "error"
    assert os.environ["HF_HUB_VERBOSITY"] == "error"


def test_user_setting_wins(monkeypatch):
    import os

    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HF_HUB_VERBOSITY", "info")
    quiet_model_load()
    assert os.environ["HF_HUB_VERBOSITY"] == "info"


def test_embedder_runs_on_cpu(monkeypatch):
    """The embedding model must never take GPU memory from the summarizing model (triage.md)."""
    calls = []

    class FakeST:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=FakeST))
    SentenceTransformerEmbedder("some/model")
    assert calls == [(("some/model",), {"device": "cpu"})]
