"""工具通道选择：默认 native，端点明确拒绝时逐档降级并缓存结果。

降档缓存由 ToolChannelState 实例持有，LLMClient 为每套方案建一个；本模块只放判据与降档顺序。
"""

import logging
import re
import threading
from functools import lru_cache

from pet.brain.llm_errors import ProviderBadRequest
from pet.brain.projection import Profile
from pet.brain.providers.registry import normalize_provider
from pet.config import config

logger = logging.getLogger(__name__)

# 降档顺序：native → user-note → system-note → none
CHANNEL_ORDER = ("native", "user-note", "system-note", "none")

_PRESET_TO_CHANNEL = {
    "native": "native",
    "user-note": "user-note",
    "system-note": "system-note",
    "minimal": "none",
}

# 工具字段词表按通道分开。OpenAI 兼容端点用 tool_calls 与 role=tool 表达工具历史；
# Gemini 用 function_declarations 与 role=model。model 是通用词，「模型不支持某参数」
# 也会命中，因此 Gemini 侧要求它与角色列举或函数同现。
_TOOL_FIELD = {
    "openai": re.compile(
        r"tool_call_id|tool_calls|\brole\b|\bfunction\b|\btool\b", re.I),
    "gemini": re.compile(
        r"function_declaration|function_call|function_response|parameters_json_schema"
        r"|role[^.]{0,40}\bmodel\b", re.I),
}
_REJECTED = re.compile(
    r"not support|unsupported|does not support|not permitted|extra inputs|"
    r"invalid role|unknown role|unexpected keyword|unknown field|invalid field|"
    r"not supported for|不支持|无法识别", re.I)


class ToolChannelState:
    """一套方案的降档缓存；缓存键由 scope_key 给出 (通道名, 模型名)。"""

    def __init__(self, scope_key, native_tools=None):
        self._scope_key = scope_key
        self._native_tools = native_tools
        self._verified: dict[tuple, str] = {}
        self._lock = threading.Lock()

    def scope(self) -> tuple:
        """当前生效方案的缓存键。"""
        try:
            return tuple(self._scope_key())
        except Exception:
            logger.debug("[ToolChannel] 方案来源读取失败，按空键缓存", exc_info=True)
            return ()

    def initial(self) -> str:
        """未探测过时的初始档位：通道声明不支持原生工具时从 user-note 起步。"""
        if self._native_tools is None:
            return "native"
        try:
            supported = bool(self._native_tools())
        except Exception:
            logger.debug("[ToolChannel] 能力读取失败，按 native 起步", exc_info=True)
            return "native"
        return "native" if supported else next_channel("native")

    def channel(self, mode: str | None = None) -> str:
        """按显式档位、探测缓存或初始档位给出通道。"""
        mode = (mode or config.LLM_TOOL_ROLE_MODE or "auto").strip().lower()
        if mode in _PRESET_TO_CHANNEL:
            return _PRESET_TO_CHANNEL[mode]
        # 缓存键与初始档位都可能在锁外读配置/能力，先算好再进锁
        scope, initial = self.scope(), self.initial()
        with self._lock:
            return self._verified.get(scope, initial)

    def resolve(self, mode: str | None = None) -> str:
        """覆盖 > 探测缓存 > 初始档位。"""
        override = override_for()
        if override:
            return override
        return self.channel(mode)

    def mark_downgraded(self, current: str) -> str:
        """记录一次降档；缓存只活到进程结束。"""
        downgraded = next_channel(current)
        if downgraded != current:
            scope = self.scope()
            with self._lock:
                self._verified[scope] = downgraded
            logger.warning(f"[ToolChannel] downgraded {current} → {downgraded} for {scope}")
        return downgraded

    def reset(self):
        """切换连接配置后清空探测结果。"""
        with self._lock:
            self._verified.clear()

    def profile(self) -> Profile:
        """当前投影使用的 Profile。"""
        channel = self.resolve()
        return Profile(name=channel, tool_channel=channel,
                       replay_args_mode=replay_args_mode())


# 未建 provider 的路径（本地兜底、测试替身）用一份模块级状态，键落在配置上
_fallback_state: ToolChannelState | None = None


@lru_cache(maxsize=1)
def _fallback_lock() -> threading.Lock:
    """默认状态的创建锁；lru_cache 保证只建一把。"""
    return threading.Lock()


def default_state() -> ToolChannelState:
    """没有 LLMClient 时的降档状态。"""
    global _fallback_state
    with _fallback_lock():
        if _fallback_state is None:
            _fallback_state = ToolChannelState(_config_scope)
        return _fallback_state


def _config_scope() -> tuple:
    """按配置推断缓存键：通道名与模型名。"""
    return (normalize_provider(config.LLM_PROVIDER), config.LLM_MODEL or "")


def is_tool_rejection(exc: BaseException, provider: str = "") -> bool:
    """400/422 且明确表示不支持工具字段，才算通道被拒。

    词表按 provider 选取；通道未知时两张表同时参与匹配。
    """
    if not isinstance(exc, ProviderBadRequest):
        return False
    if exc.status_code not in (400, 422):
        return False
    text = f"{exc.body}{exc}"
    if not _REJECTED.search(text):
        return False
    patterns = ([_TOOL_FIELD[provider]] if provider in _TOOL_FIELD
                else list(_TOOL_FIELD.values()))
    return any(pattern.search(text) for pattern in patterns)


def next_channel(channel: str) -> str:
    """降一档；已到末档原样返回。"""
    try:
        index = CHANNEL_ORDER.index(channel)
    except ValueError:
        return "user-note"
    return CHANNEL_ORDER[min(index + 1, len(CHANNEL_ORDER) - 1)]


def replay_args_mode() -> str:
    return "empty" if (config.NATIVE_REPLAY_ARGS or "minimal") == "empty" else "minimal"


def override_for(model: str | None = None) -> str | None:
    """用户覆盖：`模型前缀=预设,模型前缀=预设` 形式的隐藏配置。"""
    raw = config.LLM_TOOL_ROLE_OVERRIDE or ""
    model = model if model is not None else (config.LLM_MODEL or "")
    for item in raw.split(","):
        if "=" not in item:
            continue
        prefix, _, preset = item.partition("=")
        prefix = prefix.strip().lower()
        preset = preset.strip().lower()
        if not prefix or preset not in _PRESET_TO_CHANNEL:
            continue
        if model.lower().startswith(prefix):
            return _PRESET_TO_CHANNEL[preset]
    return None
