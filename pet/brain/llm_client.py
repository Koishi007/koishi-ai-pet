"""LLM 客户端：维护首选 / 备选两套方案，每套方案持有一个 provider 实例。"""

import logging
import threading

from pet.config import config
from pet.brain.llm_errors import ProviderUnavailable
from pet.brain.providers import LLMConnection
from pet.brain.providers.registry import (build_provider, normalize_provider,
                                          reasoning_efforts)
from pet.brain.reasoning import effective_efforts
from pet.brain.reasoning import normalize as normalize_effort
from pet.brain.tool_channel import ToolChannelState

logger = logging.getLogger(__name__)

PROFILE_PRIMARY = "primary"
PROFILE_ALTERNATIVE = "alternative"

# ollama 档复用 OpenAI 兼容通道，区别只在地址与占位 key
OLLAMA_API_KEY = "ollama"
OLLAMA_DEFAULT_MODEL = "llama3.2"


def normalize_profile(profile: str | None) -> str:
    """将任意取值归一化为合法的方案名。"""
    return PROFILE_ALTERNATIVE if profile == PROFILE_ALTERNATIVE else PROFILE_PRIMARY


def active_llm_profile() -> str:
    """用户在设置中选择的首选方案。"""
    return normalize_profile(config.LLM_ACTIVE_PROFILE)


def other_profile(profile: str) -> str:
    """返回指定方案之外的另一个方案。"""
    return PROFILE_ALTERNATIVE if normalize_profile(profile) == PROFILE_PRIMARY else PROFILE_PRIMARY


def is_complete(key: str, url: str, model: str) -> bool:
    """地址、Key 与模型名都填写，才算一套独立连接。"""
    return all((value or "").strip() for value in (key, url, model))


def has_own_connection(profile: str) -> bool:
    """该方案是否配齐了独立连接；备选缺任一项时整个方案沿用首选。"""
    if normalize_profile(profile) != PROFILE_ALTERNATIVE:
        return True
    return is_complete(config.LLM_KEY_ALT, config.LLM_URL_ALT, config.LLM_MODEL_ALT)


def resolve_llm_profile(profile: str) -> tuple[str, str, str]:
    """返回指定方案实际生效的 (key, url, model)；备选未配齐时整方案沿用首选。"""
    if normalize_profile(profile) == PROFILE_ALTERNATIVE and has_own_connection(profile):
        return (config.LLM_KEY_ALT, config.LLM_URL_ALT, config.LLM_MODEL_ALT)
    return (config.LLM_KEY, config.LLM_URL, config.LLM_MODEL)


def resolve_provider_name(profile: str) -> str:
    """指定方案实际生效的通道名；备选未配齐或未选通道时沿用首选，未知值回落到默认通道。"""
    primary = normalize_provider(config.LLM_PROVIDER)
    if not has_own_connection(profile):
        return primary
    alt = (config.LLM_PROVIDER_ALT or "").strip().lower()
    return normalize_provider(alt) if alt else primary


def resolve_reasoning_efforts(profile: str) -> tuple[str, ...]:
    """该方案端点可用的档位：配置清单优先，其次模型预设，否则通道声明。"""
    if not has_own_connection(profile):
        return resolve_reasoning_efforts(PROFILE_PRIMARY)
    declared = reasoning_efforts(resolve_provider_name(profile))
    if not declared:
        return ()
    alternative = normalize_profile(profile) == PROFILE_ALTERNATIVE
    override = (config.LLM_REASONING_EFFORTS_ALT if alternative
                else config.LLM_REASONING_EFFORTS)
    model = resolve_llm_profile(profile)[2]
    return effective_efforts(declared, override, model)


def resolve_reasoning_effort(profile: str) -> str:
    """指定方案实际生效的推理强度；备选未配齐时跟随首选，端点不支持的取值不下发。"""
    target = normalize_profile(profile) if has_own_connection(profile) else PROFILE_PRIMARY
    alternative = target == PROFILE_ALTERNATIVE
    value = normalize_effort(config.LLM_REASONING_EFFORT_ALT if alternative
                             else config.LLM_REASONING_EFFORT)
    return value if value in resolve_reasoning_efforts(target) else ""


def resolve_connection(profile: str) -> LLMConnection:
    """把配置组装成一套连接参数；BRAIN=ollama 时两套方案都走 OpenAI 通道。"""
    brain = config.BRAIN or "local"
    key, url, model = resolve_llm_profile(profile)
    if brain == "ollama":
        return LLMConnection(provider="openai", key=OLLAMA_API_KEY,
                             url=config.OLLAMA_BASE_URL,
                             model=model or OLLAMA_DEFAULT_MODEL)
    return LLMConnection(provider=resolve_provider_name(profile), key=key or "",
                         url=url or "", model=model or "")


class LLMClient:

    def __init__(self):
        self._providers: dict[str, object | None] = {}
        self._effective: str = active_llm_profile()
        self._lock = threading.RLock()
        self._build()
        # 工具通道降档缓存按生效方案隔离，回退后键随之改变
        self.tool_channel = ToolChannelState(self._scope_key, self._native_tools)

    def _scope_key(self) -> tuple:
        """工具通道降档缓存的键：当前生效方案的通道名与模型名。"""
        provider = self.provider
        if provider is None:
            model = resolve_llm_profile(self._effective)[2]
            return (resolve_provider_name(self._effective), model or "")
        return (getattr(provider, "name", ""), getattr(provider, "model", "") or "")

    def _native_tools(self) -> bool:
        """当前生效通道是否声明支持原生工具。"""
        caps = self.capabilities
        return bool(caps and caps.native_tools)

    def provider_name(self, profile: str | None = None) -> str:
        """指定方案声明的通道名；未构建时按配置推断。"""
        target = normalize_profile(profile) if profile else self._effective
        provider = self._providers.get(target)
        if provider is not None:
            return getattr(provider, "name", "") or ""
        return resolve_provider_name(target)

    def _build_profile(self, profile: str):
        """按当前配置构建指定方案的 provider。"""
        if (config.BRAIN or "local") not in ("api", "ollama"):
            return None
        connection = resolve_connection(profile)
        if not connection.key and not connection.url and not connection.model:
            return None
        try:
            return build_provider(connection, config.LLM_TIMEOUT,
                                  reasoning_effort=resolve_reasoning_effort(profile))
        except ProviderUnavailable as e:
            logger.warning(f"[LLMClient] {profile} 方案不可用，{e}；该方案退回本地兜底")
            return None

    def _close_all(self):
        for provider in self._providers.values():
            close = getattr(provider, "close", None)
            if close is None:
                continue
            try:
                close()
            except Exception:
                logger.debug("[LLMClient] provider close failed", exc_info=True)

    def _build(self):
        brain = config.BRAIN or "local"
        self._close_all()
        for profile in (PROFILE_PRIMARY, PROFILE_ALTERNATIVE):
            self._providers[profile] = self._build_profile(profile)
        self._effective = active_llm_profile()

        primary = self._providers.get(PROFILE_PRIMARY)
        alternative = self._providers.get(PROFILE_ALTERNATIVE)
        logger.debug(
            f"[LLMClient] _build: BRAIN={brain}, preferred={self._effective}, "
            f"primary={_describe(primary)}, alternative={_describe(alternative)}"
        )
        if primary is None and alternative is None:
            logger.warning(f"[LLMClient] No usable provider (BRAIN={brain}) → local fallback")

    def rebuild(self):
        """运行时重建 provider（设置界面修改连接配置后调用）。"""
        with self._lock:
            self._build()
        self.tool_channel.reset()
        logger.info(f"[LLMClient] rebuild: preferred={active_llm_profile()}, "
                    f"effective={self._effective}, {_describe(self.provider)}")

    def close(self):
        """释放两套方案的连接池；退出流程调用。"""
        with self._lock:
            self._close_all()
            self._providers = {profile: None
                               for profile in (PROFILE_PRIMARY, PROFILE_ALTERNATIVE)}

    def activate_fallback(self) -> bool:
        """重试前切换到备选方案；已处于备选或两套连接相同时返回 False。"""
        with self._lock:
            preferred = active_llm_profile()
            fallback = other_profile(preferred)
            if self._effective == fallback:
                return False
            if self._providers.get(fallback) is None:
                return False
            if resolve_connection(fallback) == resolve_connection(preferred):
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
    def provider(self):
        """当前生效的 provider；未配置可用通道时为 None。"""
        with self._lock:
            return self._providers.get(self._effective)

    @property
    def model(self) -> str | None:
        provider = self.provider
        return getattr(provider, "model", None) if provider else None

    @property
    def capabilities(self):
        """当前生效通道的能力声明。"""
        provider = self.provider
        return getattr(provider, "capabilities", None)

    @property
    def has_vision(self) -> bool:
        caps = self.capabilities
        return bool(caps and caps.vision and config.VISION_ENABLED)

    def __bool__(self):
        return self.provider is not None


def _describe(provider) -> str:
    """日志用的 provider 描述。"""
    if provider is None:
        return "None (local)"
    return f"{type(provider).__name__}(model={getattr(provider, 'model', None)})"
