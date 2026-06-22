import json
import logging
import os
from pet.settings import load_user_settings, save_user_setting, delete_user_settings, settings_path

logger = logging.getLogger(__name__)


# key: dict with keys: type, default, category, needs_restart, hidden, description[, enum, placeholder, minimum, maximum]
# type: "str", "int", "float", "bool", "str_list"
# category: "connection", "behavior", "appearance", "personality"
# hidden: False = shown in UI, True = advanced (settings.json only)
_KEY_META = {
    # ── Connection ──
    "BRAIN":                     {"type": "str",      "default": "local",       "category": "connection", "needs_restart": False, "hidden": False, "description": "LLM 调用模式",                           "enum": ["local", "api", "ollama"]},
    "LLM_MODEL":                 {"type": "str",      "default": "",            "category": "connection", "needs_restart": False, "hidden": False, "description": "LLM 模型名称",                           "placeholder": "gpt-4o"},
    "LLM_KEY":                   {"type": "str",      "default": "",            "category": "connection", "needs_restart": False, "hidden": False, "description": "API Key"},
    "LLM_URL":                   {"type": "str",      "default": "",            "category": "connection", "needs_restart": False, "hidden": False, "description": "API 地址(需兼容 OpenAI 格式)"},
    "OLLAMA_BASE_URL":           {"type": "str",      "default": "http://localhost:11434/v1", "category": "connection", "needs_restart": False, "hidden": False, "description": "Ollama 服务地址"},
    "LLM_TIMEOUT":               {"type": "float",    "default": 30,            "category": "connection", "needs_restart": False, "hidden": False, "description": "LLM 请求超时(秒)"},
    "LLM_MAX_RETRIES":           {"type": "int",      "default": 3,             "category": "connection", "needs_restart": False, "hidden": False, "description": "LLM 最大重试次数"},
    "LLM_RETRY_DELAY":           {"type": "float",    "default": 1,             "category": "connection", "needs_restart": False, "hidden": True,  "description": "重试延迟(秒)"},
    "LLM_RETRY_MAX_DELAY":       {"type": "float",    "default": 8,             "category": "connection", "needs_restart": False, "hidden": True,  "description": "最大重试延迟(秒)"},
    "LLM_CACHE_PROMPT":          {"type": "bool",     "default": False,         "category": "connection", "needs_restart": False, "hidden": False, "description": "启用 Prompt 缓存"},
    # ── Behavior ──
    "SCHEDULER_FAST_MS":         {"type": "int",      "default": 1000,          "category": "behavior",   "needs_restart": False, "hidden": True,  "description": "fast_tick 间隔(毫秒)"},
    "SCHEDULER_MID_MS":          {"type": "int",      "default": 300000,        "category": "behavior",   "needs_restart": False, "hidden": False, "description": "自主决策间隔(毫秒)"},
    "SCHEDULER_SLOW_MS":         {"type": "int",      "default": 300000,        "category": "behavior",   "needs_restart": False, "hidden": True,  "description": "slow_tick 间隔(毫秒)"},
    "SCHEDULER_AUTO_START_FAST": {"type": "bool",     "default": True,          "category": "behavior",   "needs_restart": False, "hidden": True,  "description": "自动启动 fast_tick"},
    "SCHEDULER_AUTO_START_MID":  {"type": "bool",     "default": True,          "category": "behavior",   "needs_restart": False, "hidden": False, "description": "自动启动 mid_tick(自主决策)"},
    "SCHEDULER_AUTO_START_SLOW": {"type": "bool",     "default": True,          "category": "behavior",   "needs_restart": False, "hidden": True,  "description": "自动启动 slow_tick"},
    "SCHEDULER_IDLE_TIMEOUT_MS": {"type": "int",      "default": 900000,        "category": "behavior",   "needs_restart": False, "hidden": True,  "description": "空闲超时(毫秒)，超过后进入休眠"},
    "ACTION_TIMEOUT_MS":         {"type": "int",      "default": 15000,         "category": "behavior",   "needs_restart": False, "hidden": True,  "description": "单个动作超时(毫秒)"},
    "SANITY_CRITICAL_THRESHOLD": {"type": "int",      "default": 20,            "category": "behavior",   "needs_restart": False, "hidden": False, "description": "理智临界值，低于该值导致异常行为"},
    "INTERACT_GRABBED_PROMPT":      {"type": "str",   "default": "",            "category": "behavior",   "needs_restart": False, "hidden": False, "description": "被抓取时的自定义回复 prompt"},
    "INTERACT_RELEASED_PROMPT":     {"type": "str",   "default": "",            "category": "behavior",   "needs_restart": False, "hidden": False, "description": "被放下时的自定义回复 prompt"},
    "INTERACT_WINDOW_DISAPPEARED_PROMPT": {"type": "str", "default": "",        "category": "behavior",   "needs_restart": False, "hidden": False, "description": "窗口消失时的自定义回复 prompt"},
    "INTERACT_FED_PROMPT":          {"type": "str",   "default": "",            "category": "behavior",   "needs_restart": False, "hidden": True,  "description": "喂食交互的自定义 prompt 模板"},
    # ── Appearance ──
    "VISION_ENABLED":            {"type": "bool",     "default": False,         "category": "appearance", "needs_restart": False, "hidden": False, "description": "启用视觉理解(需多模态模型支持)"},
    "VISION_SCALE":              {"type": "float",    "default": 1.0,           "category": "appearance", "needs_restart": False, "hidden": False, "description": "截图缩放比例(0.1~1.0)"},
    "SKILLS_ENABLED":            {"type": "str_list", "default": [],            "category": "appearance", "needs_restart": True,  "hidden": False, "description": "启用的技能插件(逗号分隔, *=全部)"},
    "PET_WIDTH":                 {"type": "int",      "default": 125,           "category": "appearance", "needs_restart": True,  "hidden": False, "description": "宠物窗口宽度(px)"},
    "PET_HEIGHT":                {"type": "int",      "default": 125,           "category": "appearance", "needs_restart": True,  "hidden": False, "description": "宠物窗口高度(px)"},
    "PET_FPS":                   {"type": "int",      "default": 15,            "category": "appearance", "needs_restart": True,  "hidden": True,  "description": "动画帧率"},
    "BUBBLE_MAX_WIDTH":          {"type": "int",      "default": 300,           "category": "appearance", "needs_restart": True,  "hidden": False, "description": "气泡最大宽度(px)"},
    "BUBBLE_FONT_SIZE":          {"type": "int",      "default": 14,            "category": "appearance", "needs_restart": True,  "hidden": False, "description": "气泡字号"},
    "SHOW_TRAY":                 {"type": "bool",     "default": True,          "category": "appearance", "needs_restart": True,  "hidden": False, "description": "显示托盘图标"},
    "HIDE_CONSOLE":              {"type": "bool",     "default": True,          "category": "appearance", "needs_restart": True,  "hidden": True,  "description": "启动时隐藏控制台窗口"},
    "LOG_LEVEL":                 {"type": "str",      "default": "DEBUG",       "category": "appearance", "needs_restart": False, "hidden": True,  "description": "日志级别(DEBUG/INFO/WARNING/ERROR)"},
    # ── Personality ──
    "PET_PERSONALITY":           {"type": "str",      "default": "",            "category": "personality", "needs_restart": False, "hidden": False, "description": "宠物人格描述(注入 system prompt)"},
}


def _convert(raw, type_name):
    """Convert string/raw value by type."""
    if type_name == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).lower() in ("1", "true", "yes")
    if type_name == "int":
        return int(raw)
    if type_name == "float":
        return float(raw)
    if type_name == "str_list":
        if isinstance(raw, list):
            return raw
        return [s.strip() for s in str(raw).split(",") if s.strip()] if raw else []
    return str(raw)


class Config:

    def __init__(self):
        self._load_defaults()
        self._load_user_settings()
        self._generate_schema()

    def _load_defaults(self):
        """Load all default values from _KEY_META into instance attributes."""
        for key, meta in _KEY_META.items():
            default = meta["default"]
            if meta["type"] == "str_list" and isinstance(default, list):
                default = list(default)
            setattr(self, key, default)

    _TYPE_MAP = {"str": "string", "int": "integer", "float": "number", "bool": "boolean", "str_list": "array"}

    def _generate_schema(self):
        """Write settings-schema.json to the same directory as settings.json."""
        schema = {
            "$schema": "https://json-schema.org/draft-07/schema#",
            "title": "DeskPet Settings",
            "description": "DeskPet 桌面宠物全部配置项说明。\nhidden: true 的字段不在 UI 显示，可直接编辑 settings.json。\nneeds_restart: true 的字段修改后需重启生效。",
            "properties": {},
        }
        for key, meta in _KEY_META.items():
            entry = {
                "type": self._TYPE_MAP.get(meta["type"], "string"),
                "default": meta["default"],
                "description": meta.get("description", ""),
                "category": meta["category"],
                "needs_restart": meta["needs_restart"],
                "hidden": meta["hidden"],
            }
            if meta.get("enum"):
                entry["enum"] = meta["enum"]
            if meta.get("minimum") is not None:
                entry["minimum"] = meta["minimum"]
            if meta.get("maximum") is not None:
                entry["maximum"] = meta["maximum"]
            schema["properties"][key] = entry

        path = os.path.join(os.path.dirname(settings_path()), "settings-schema.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(schema, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning(f"[Config] Failed to write schema: {e}")

    def _load_user_settings(self):
        """Read overrides from settings.json and update instance attributes."""
        data = load_user_settings()
        self._user_settings = data
        for key, value in data.items():
            if key not in _KEY_META:
                continue
            type_name = _KEY_META[key]["type"]
            try:
                setattr(self, key, _convert(value, type_name))
            except (ValueError, TypeError):
                pass  # skip entries that fail type conversion

    def save(self, key: str, value) -> tuple[bool, list[str]]:
        """Save a single setting to settings.json and update the instance attribute.

        Returns (applied, needs_restart):
          applied: whether the current instance was updated
          needs_restart: list of keys that require restart (may include this key)
        """
        type_name = _KEY_META[key]["type"]
        converted = _convert(value, type_name)
        setattr(self, key, converted)
        save_user_setting(key, converted)
        needs_restart = [key] if _KEY_META[key]["needs_restart"] else []
        return (True, needs_restart)

    def reset(self, keys: list[str]):
        """Remove specified keys from settings.json and fall back to _KEY_META defaults."""
        delete_user_settings(keys)
        self._load_defaults()
        self._load_user_settings()


config = Config()
