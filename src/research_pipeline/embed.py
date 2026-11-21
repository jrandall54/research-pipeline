"""Text embedders: turn text into L2-normalised vectors. See docs/guide/triage.md."""

from typing import Protocol

import numpy as np


class Embedder(Protocol):
    """Anything that maps a list of texts to an (n, d) array of unit-length vectors."""

    model_name: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


class SentenceTransformerEmbedder:
    """Real embedder backed by sentence-transformers (optional `embed` extra)."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is not installed; run `uv sync --extra embed`"
            ) from exc
        self.model_name = model_name
        self._model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._model.get_sentence_embedding_dimension()), np.float32)
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return np.asarray(vecs, dtype=np.float32)
