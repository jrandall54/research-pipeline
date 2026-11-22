"""The model load must not draw library progress bars or print the HF_TOKEN nag (no network, no model)."""

from research_pipeline.embed import quiet_model_load

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
