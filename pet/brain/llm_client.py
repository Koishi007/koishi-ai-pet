"""OpenAI-compatible LLM client — 封装连接逻辑，参考 embedding_client 结构。"""

import logging
import threading

from openai import OpenAI
from pet.config import config

logger = logging.getLogger(__name__)


class LLMClient:
    """LLM 客户端，封装 OpenAI 兼容 API 连接。

    支持两种模式：
    - ollama: 本地 Ollama 服务
    - api:   通用 OpenAI 兼容 API
    若 BRAIN=local 或缺少 API Key，client 为 None（走本地 fallback）。
    """

    def __init__(self):
        self._client: OpenAI | None = None
        self._model: str | None = None
        self._lock = threading.RLock()
        self._build()

    # ── 构建 / 重建 ──

    def _build(self):
        brain = config.BRAIN or "local"
        key = config.LLM_KEY
        url = config.LLM_URL
        model = config.LLM_MODEL

        logger.debug(
            f"[LLMClient] _build: BRAIN={brain}, model={model}, "
            f"key={'***' if key else 'EMPTY'}, URL={url or '(empty)'}"
        )

        if brain == "ollama":
            self._client = OpenAI(
                api_key="ollama",
                base_url=config.OLLAMA_BASE_URL,
                timeout=config.LLM_TIMEOUT,
            )
            self._model = model or "llama3.2"
        elif brain == "api" and key:
            self._client = OpenAI(
                api_key=key,
                base_url=url or "",
                timeout=config.LLM_TIMEOUT,
            )
            self._model = model
        else:
            self._client = None
            logger.warning(
                f"[LLMClient] No client (BRAIN={brain}, key empty={not bool(key)}) → local fallback"
            )

    def rebuild(self):
        """运行时重建客户端（设置界面修改连接配置后调用）。"""
        with self._lock:
            self._build()
        client_type = (
            "None (local)"
            if self._client is None
            else f"{type(self._client).__name__}(model={self._model})"
        )
        logger.info(f"[LLMClient] rebuild: {client_type}")

    # ── 属性 ──

    @property
    def client(self) -> OpenAI | None:
        return self._client

    @property
    def model(self) -> str | None:
        return self._model

    @property
    def has_vision(self) -> bool:
        return self._client is not None and config.VISION_ENABLED

    def __bool__(self):
        return self._client is not None
