"""OpenAI-compatible embedding client."""

import logging
import numpy as np
from openai import OpenAI

logger = logging.getLogger(__name__)


class EmbeddingError(Exception):
    """Raised when embedding API call fails."""


class EmbeddingClient:

    def __init__(self, url: str, key: str, model: str, dim: int):
        self._client = OpenAI(base_url=url, api_key=key, timeout=30)
        self._model = model
        self._dim = dim

    def embed(self, texts: str | list[str]) -> list[list[float]]:
        """Return L2-normalized embeddings for the given text(s)."""
        if isinstance(texts, str):
            texts = [texts]

        try:
            resp = self._client.embeddings.create(
                model=self._model,
                input=texts,
                dimensions=self._dim,
            )
        except Exception as e:
            raise EmbeddingError(f"Embedding API call failed: {e}") from e

        if len(resp.data) != len(texts):
            raise EmbeddingError(
                f"Expected {len(texts)} embeddings, got {len(resp.data)}"
            )

        # Sort by index to match input order
        sorted_data = sorted(resp.data, key=lambda d: d.index)
        vectors = [np.array(d.embedding, dtype=np.float32) for d in sorted_data]

        # L2 normalize for stable cosine distance in sqlite-vec
        norms = [np.linalg.norm(v) for v in vectors]
        vectors = [
            (v / norm).tolist() if norm > 0 else v.tolist()
            for v, norm in zip(vectors, norms)
        ]
        return vectors
