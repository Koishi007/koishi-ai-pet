"""嵌入向量客户端：按 OpenAI 兼容接口构建 provider，对结果做 L2 归一化。"""

import logging

import numpy as np

from pet.brain.llm_errors import ProviderUnavailable
from pet.brain.providers import LLMConnection
from pet.brain.providers.registry import build_provider

logger = logging.getLogger(__name__)


class EmbeddingError(Exception):
    """嵌入 API 调用失败时抛出。"""


class EmbeddingClient:
    """按嵌入通道构建 provider。"""

    def __init__(self, url: str, key: str, model: str, dim: int):
        self._model = model
        self._dim = dim
        connection = LLMConnection(provider="openai", key=key, url=url, model=model)
        try:
            self._provider = build_provider(connection, timeout=30.0)
        except ProviderUnavailable as e:
            raise EmbeddingError(f"嵌入接口不可用：{e}") from e
        # 嵌入客户端与 LLMClient 同寿命，不按次构建

    def close(self) -> None:
        """释放底层连接；由持有者在退出时调用。"""
        close = getattr(self._provider, "close", None)
        if close is not None:
            close()

    def embed(self, texts: str | list[str]) -> list[list[float]]:
        """返回给定文本的 L2 归一化嵌入向量。"""
        if isinstance(texts, str):
            texts = [texts]

        try:
            raw = self._provider.embed(list(texts), self._dim)
        except Exception as e:
            raise EmbeddingError(f"Embedding API call failed: {e}") from e

        if len(raw) != len(texts):
            raise EmbeddingError(
                f"Expected {len(texts)} embeddings, got {len(raw)}"
            )

        vectors = [np.array(v, dtype=np.float32) for v in raw]

        # L2 归一化，为 sqlite-vec 提供稳定的余弦距离
        norms = [np.linalg.norm(v) for v in vectors]
        vectors = [
            (v / norm).tolist() if norm > 0 else v.tolist()
            for v, norm in zip(vectors, norms)
        ]
        return vectors
