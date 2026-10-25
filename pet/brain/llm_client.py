"""OpenAI-compatible LLM client（支持首选/备选两套模型方案）"""

import logging
import threading

import httpx
from openai import OpenAI
from pet.config import config
from pet.brain.tool_channel import reset_cache

logger = logging.getLogger(__name__)

PROFILE_PRIMARY = "primary"
PROFILE_ALTERNATIVE = "alternative"


def normalize_profile(profile: str | None) -> str:
    """将任意取值归一化为合法的方案名。"""
    return PROFILE_ALTERNATIVE if profile == PROFILE_ALTERNATIVE else PROFILE_PRIMARY


def active_llm_profile() -> str:
    """用户在设置中选择的首选方案。"""
    return normalize_profile(config.LLM_ACTIVE_PROFILE)


def other_profile(profile: str) -> str:
    """返回指定方案之外的另一个方案（即备选）。"""
    return PROFILE_ALTERNATIVE if normalize_profile(profile) == PROFILE_PRIMARY else PROFILE_PRIMARY


def resolve_llm_profile(profile: str) -> tuple[str, str, str]:
    """返回指定方案实际生效的 (key, url, model)"""
    primary = (config.LLM_KEY, config.LLM_URL, config.LLM_MODEL)
    if normalize_profile(profile) == PROFILE_ALTERNATIVE:
        alt = (config.LLM_KEY_ALT, config.LLM_URL_ALT, config.LLM_MODEL_ALT)
        if not any(alt):
            return primary
        return (alt[0] or primary[0], alt[1] or primary[1], alt[2] or primary[2])
    return primary


class LLMClient:

    def __init__(self):
        self._clients: dict[str, OpenAI | None] = {}
        self._models: dict[str, str | None] = {}
        self._effective: str = active_llm_profile()
        self._lock = threading.RLock()
        self._build()

    @staticmethod
    def _make_timeout() -> httpx.Timeout:
        return httpx.Timeout(
            connect=10.0,
            read=config.LLM_TIMEOUT,
            write=10.0,
            pool=5.0,
        )

    @staticmethod
    def _sdk_retries() -> int:
        """SDK 内部重试禁用"""
        return 0

    def _build_profile(self, profile: str) -> tuple[OpenAI | None, str | None]:
        """按当前配置构建指定方案的客户端与模型名。"""
        brain = config.BRAIN or "local"
        key, url, model = resolve_llm_profile(profile)

        if brain == "ollama":
            client = OpenAI(
                api_key="ollama",
                base_url=config.OLLAMA_BASE_URL,
                timeout=self._make_timeout(),
                max_retries=self._sdk_retries(),
            )
            return client, (model or "llama3.2")
        if brain == "api" and key:
            client = OpenAI(
                api_key=key,
                base_url=url or "",
                timeout=self._make_timeout(),
                max_retries=self._sdk_retries(),
            )
            return client, model
        return None, None

    def _build(self):
        brain = config.BRAIN or "local"
        for profile in (PROFILE_PRIMARY, PROFILE_ALTERNATIVE):
            client, model = self._build_profile(profile)
            self._clients[profile] = client
            self._models[profile] = model
        self._effective = active_llm_profile()

        primary_model = self._models.get(PROFILE_PRIMARY)
        alt_model = self._models.get(PROFILE_ALTERNATIVE)
        logger.debug(
            f"[LLMClient] _build: BRAIN={brain}, preferred={self._effective}, "
            f"primary_model={primary_model or '(none)'}, alternative_model={alt_model or '(none)'}"
        )
        if primary_model is None and alt_model is None:
            logger.warning(
                f"[LLMClient] No usable client (BRAIN={brain}) → local fallback"
            )

    def rebuild(self):
        """运行时重建客户端（设置界面修改连接配置后调用）。"""
        with self._lock:
            self._build()
        reset_cache()
        client_type = (
            "None (local)"
            if self.client is None
            else f"{type(self.client).__name__}(model={self.model})"
        )
        logger.info(f"[LLMClient] rebuild: preferred={active_llm_profile()}, effective={self._effective}, {client_type}")

    def activate_fallback(self) -> bool:
        """重试前切换到备选方案；返回是否真正发生了切换。

        已处于备选、备选不可用或备选与首选配置相同时返回 False，
        避免在同一个失效配置上反复切换。
        """
        with self._lock:
            preferred = active_llm_profile()
            fallback = other_profile(preferred)
            if self._effective == fallback:
                return False
            if self._clients.get(fallback) is None:
                return False
            if resolve_llm_profile(fallback) == resolve_llm_profile(preferred):
                return False
            self._effective = fallback
            return True

    def reset_effective(self):
        """恢复到用户选择的方案（每次新的请求链开始时调用）。"""
        with self._lock:
            self._effective = active_llm_profile()

    @property
    def active_profile(self) -> str:
        """用户选择的方案名。"""
        return active_llm_profile()

    @property
    def effective_profile(self) -> str:
        """当前实际生效的方案名（回退时为备选）。"""
        with self._lock:
            return self._effective

    @property
    def using_fallback(self) -> bool:
        """当前是否正在使用备选方案。"""
        with self._lock:
            return self._effective != active_llm_profile()

    @property
    def client(self) -> OpenAI | None:
        with self._lock:
            return self._clients.get(self._effective)

    @property
    def model(self) -> str | None:
        with self._lock:
            return self._models.get(self._effective)

    @property
    def has_vision(self) -> bool:
        return self.client is not None and config.VISION_ENABLED

    def __bool__(self):
        return self.client is not None
