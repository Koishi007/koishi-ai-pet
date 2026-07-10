"""OpenAI 兼容的嵌入向量客户端。"""

import logging
import numpy as np
from openai import OpenAI

logger = logging.getLogger(__name__)


class EmbeddingError(Exception):
    """嵌入 API 调用失败时抛出。"""


class EmbeddingClient:

    def __init__(self, url: str, key: str, model: str, dim: int):
        self._client = OpenAI(base_url=url, api_key=key, timeout=30)
        self._model = model
        self._dim = dim

    def embed(self, texts: str | list[str]) -> list[list[float]]:
        """返回给定文本的 L2 归一化嵌入向量。"""
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

        # 按索引排序以匹配输入顺序
        sorted_data = sorted(resp.data, key=lambda d: d.index)
        vectors = [np.array(d.embedding, dtype=np.float32) for d in sorted_data]

        # L2 归一化，为 sqlite-vec 提供稳定的余弦距离
        norms = [np.linalg.norm(v) for v in vectors]
        vectors = [
            (v / norm).tolist() if norm > 0 else v.tolist()
            for v, norm in zip(vectors, norms)
        ]
        return vectors
