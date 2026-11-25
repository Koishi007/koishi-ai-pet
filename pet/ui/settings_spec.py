"""设置界面的纯数据：页签归属、推理强度档位与单位换算"""

from __future__ import annotations

# 推理强度：(显示文案, 存储值)。空串表示不下发，备选方案下表示沿用首选
EFFORT_CHOICES: tuple[tuple[str, str], ...] = (
    ("由模型决定", ""),
    ("最省 (minimal)", "minimal"),
    ("低 (low)", "low"),
    ("中 (medium)", "medium"),
    ("高 (high)", "high"),
    ("极高 (xhigh)", "xhigh"),
    ("最高 (max)", "max"),
)

EFFORT_HINT = "仅对开启思考的请求生效；下拉只列当前端点接受的档位"


def effort_items(efforts, empty_label: str) -> list[tuple[str, str]]:
    """按可用档位生成下拉项；首项为空值，文案由调用方给出。"""
    items = [(empty_label, "")]
    items += [(label, value) for label, value in EFFORT_CHOICES if value in efforts]
    return items


def effort_label(value: str) -> str:
    """档位文案；未知值返回空串。"""
    for label, stored in EFFORT_CHOICES:
        if stored == value:
            return label
    return ""


# 页签顺序即界面顺序；每个键按在页签里出现的先后排列。
TAB_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("连接", (
        "BRAIN", "LLM_PROVIDER", "LLM_URL", "OLLAMA_BASE_URL", "LLM_KEY",
        "LLM_MODEL", "LLM_REASONING_EFFORT",
        "LLM_TEMPERATURE", "LLM_TOOLS_ENABLED",         "LLM_TOOL_MAX_ROUNDS",
        "LLM_THINKING_DISABLED", "LLM_FALLBACK_ENABLED",
        "LLM_PROVIDER_ALT", "LLM_URL_ALT", "LLM_KEY_ALT", "LLM_MODEL_ALT",
        "LLM_REASONING_EFFORT_ALT",
    )),
    ("行为", (
        "SCHEDULER_MID_MS", "SCHEDULER_AUTO_START_MID", "CONTEXT_HISTORY_ENTRIES",
        "VISION_ENABLED", "VISION_SCALE", "SCREENSHOT_FORMAT",
        "SANITY_CRITICAL_THRESHOLD", "EMBEDDING_ENABLED", "EMBEDDING_URL",
        "EMBEDDING_KEY", "EMBEDDING_MODEL", "EMBEDDING_DIM",
        "MEMORY_MAX_CAPACITY", "MEMORY_RECALL_COUNT",
        "MEMORY_EVENT_RECALL_COUNT", "MEMORY_EVENT_MERGE_ENABLED",
        "MEMORY_MERGE_URL", "MEMORY_MERGE_KEY", "MEMORY_MERGE_MODEL",
    )),
    ("提示词", (
        "PET_PERSONALITY", "PET_PERSONALITY_EXAMPLES",
        "INTERACT_GRABBED_PROMPT", "INTERACT_RELEASED_PROMPT",
        "INTERACT_WINDOW_DISAPPEARED_PROMPT",
    )),
    ("通用", (
        "PET_SCALE", "BUBBLE_MAX_WIDTH", "BUBBLE_FONT_SIZE", "TOOLS_ENABLED",
        "SHOW_TRAY", "NOTIFY_ENABLED", "AUTO_START_ON_BOOT",
        "GRAB_SWING_ENABLED", "EDGE_DOCK_ENABLED", "TERMINAL_ENABLED",
        "TERMINAL_MODE", "TERMINAL_ALLOW", "TERMINAL_SANDBOX_ROOT",
        "VOICE_INPUT_ENABLED", "VOICE_HOTKEY", "XF_APPID", "XF_API_KEY",
        "XF_API_SECRET",
    )),
)

# 界面按秒显示、配置按毫秒存储的键
_UI_UNIT_SCALE = {"SCHEDULER_MID_MS": 1000}


def display_value(key: str, value):
    """配置值 → 界面值。"""
    scale = _UI_UNIT_SCALE.get(key)
    if not scale or not isinstance(value, int):
        return value
    return value // scale


def stored_value(key: str, value):
    """界面值 → 配置值。"""
    scale = _UI_UNIT_SCALE.get(key)
    return value * scale if scale else value
