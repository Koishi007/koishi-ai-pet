"""工具通道选择：默认 native，端点明确拒绝时逐档降级并缓存结果。"""

import logging
import re
import threading

from pet.config import config
from pet.brain.projection import Profile

logger = logging.getLogger(__name__)

# 降档顺序：native → user-note → system-note → none
CHANNEL_ORDER = ("native", "user-note", "system-note", "none")

_PRESET_TO_CHANNEL = {
    "native": "native",
    "user-note": "user-note",
    "system-note": "system-note",
    "minimal": "none",
}

# 错误信息要同时命中「工具字段」与「明确拒绝」，才判定为通道不支持
_TOOL_FIELD = re.compile(r"tool_call_id|tool_calls|\brole\b|\bfunction\b|\btool\b", re.I)
_REJECTED = re.compile(
    r"not support|unsupported|does not support|not permitted|extra inputs|"
    r"invalid role|unknown role|unexpected keyword|不支持|无法识别", re.I)

_lock = None
_verified: dict[tuple, str] = {}


def _module_lock():
    """惰性创建锁，避免模块导入时的副作用。"""
    global _lock
    if _lock is None:
        _lock = threading.Lock()
    return _lock


def is_tool_rejection(exc: BaseException) -> bool:
    """400/422 且明确表示不支持工具字段，才算通道被拒。"""
    status = getattr(exc, "status_code", None)
    if status not in (400, 422):
        return False
    text = f"{getattr(exc, 'body', '')}{exc}"
    return bool(_TOOL_FIELD.search(text) and _REJECTED.search(text))


def next_channel(channel: str) -> str:
    """降一档；已到末档原样返回。"""
    try:
        index = CHANNEL_ORDER.index(channel)
    except ValueError:
        return "user-note"
    return CHANNEL_ORDER[min(index + 1, len(CHANNEL_ORDER) - 1)]


def _scope() -> tuple:
    return (config.LLM_URL or "", config.LLM_MODEL or "")


def channel_for(mode: str | None = None) -> str:
    """按配置与进程内探测结果给出当前通道。"""
    mode = (mode or config.LLM_TOOL_ROLE_MODE or "auto").strip().lower()
    if mode in _PRESET_TO_CHANNEL:
        return _PRESET_TO_CHANNEL[mode]
    with _module_lock():
        return _verified.get(_scope(), "native")


def mark_downgraded(current: str) -> str:
    """记录一次降档；缓存只活到进程结束。"""
    downgraded = next_channel(current)
    if downgraded != current:
        with _module_lock():
            _verified[_scope()] = downgraded
        logger.warning(f"[ToolChannel] downgraded {current} → {downgraded} for {_scope()}")
    return downgraded


def reset_cache():
    """测试与切换模型时清空探测结果。"""
    with _module_lock():
        _verified.clear()


def current_profile() -> Profile:
    """当前投影使用的 Profile。"""
    channel = resolve_channel()
    return Profile(name=channel, tool_channel=channel,
                   replay_args_mode=replay_args_mode())


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


def resolve_channel(mode: str | None = None) -> str:
    """覆盖 > 探测缓存 > 配置档位。"""
    override = override_for()
    if override:
        return override
    return channel_for(mode)
