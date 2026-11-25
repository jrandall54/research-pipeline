"""Text embedders: turn text into L2-normalised vectors. See docs/guide/triage.md."""

import os
from typing import Protocol

import numpy as np

# Library switches that keep the model load to our own stage line + heartbeat. setdefault, so a
# power user who exports one of these (e.g. HF_HUB_VERBOSITY=info) still wins.
_QUIET_LIBRARY_ENV = {
    "HF_HUB_DISABLE_PROGRESS_BARS": "1",  # huggingface_hub download bars and "Loading weights" bar
    "TRANSFORMERS_VERBOSITY": "error",  # transformers' own log lines
    "HF_HUB_VERBOSITY": "error",  # the "unauthenticated requests ... HF_TOKEN" warning
}


def quiet_model_load() -> None:
    """Silence library progress bars and nags; must run before the libraries are imported."""
    for key, value in _QUIET_LIBRARY_ENV.items():
        os.environ.setdefault(key, value)


class Embedder(Protocol):
    """Anything that maps a list of texts to an (n, d) array of unit-length vectors."""

    model_name: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


class SentenceTransformerEmbedder:
    """Real embedder backed by sentence-transformers (optional `embed` extra)."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        quiet_model_load()
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is not installed; reinstall with the extra: "
                "`uv tool install --editable '.[embed]' --force` (or `uv sync --extra embed` inside the repo)"
            ) from exc
        self.model_name = model_name
        # Always the CPU: on a GPU the process would keep a CUDA context (GPU memory that cannot be
        # handed back until the process exits) and the summarizing model would spill to RAM all
        # night. About 20 s per ~600 abstracts on a desktop CPU. See "Why it's built this way" in
        # docs/guide/triage.md.
        self._model = SentenceTransformer(model_name, device="cpu")

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._model.get_sentence_embedding_dimension()), np.float32)
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return np.asarray(vecs, dtype=np.float32)
