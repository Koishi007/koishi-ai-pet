import os
from dotenv import load_dotenv
from pet.settings import load_user_settings, save_user_setting, delete_user_settings

load_dotenv()


# key: (type, env_default, category, needs_restart)
# type: "str", "int", "float", "bool", "str_list"
# category: "connection", "behavior", "appearance", "personality"
_KEY_META = {
    # Connection
    "BRAIN":                     ("str",      "local",       "connection",  False),
    "LLM_MODEL":                 ("str",      "",            "connection",  False),
    "LLM_KEY":                   ("str",      "",            "connection",  False),
    "LLM_URL":                   ("str",      "",            "connection",  False),
    "OLLAMA_BASE_URL":           ("str",      "http://localhost:11434/v1", "connection", False),
    "LLM_TIMEOUT":               ("float",    "30",          "connection",  False),
    "LLM_MAX_RETRIES":           ("int",      "3",           "connection",  False),
    "LLM_RETRY_DELAY":           ("float",    "1",           "connection",  False),
    "LLM_RETRY_MAX_DELAY":       ("float",    "8",           "connection",  False),
    "LLM_CACHE_PROMPT":          ("bool",     False,         "connection",  False),
    # Behavior
    "SCHEDULER_FAST_MS":         ("int",      "1000",        "behavior",    False),
    "SCHEDULER_MID_MS":          ("int",      "300000",      "behavior",    False),
    "SCHEDULER_SLOW_MS":         ("int",      "300000",      "behavior",    False),
    "SCHEDULER_AUTO_START_FAST": ("bool",     True,          "behavior",    False),
    "SCHEDULER_AUTO_START_MID":  ("bool",     True,          "behavior",    False),
    "SCHEDULER_AUTO_START_SLOW": ("bool",     True,          "behavior",    False),
    "SCHEDULER_IDLE_TIMEOUT_MS": ("int",      "900000",      "behavior",    False),
    "ACTION_TIMEOUT_MS":         ("int",      "15000",       "behavior",    False),
    "SANITY_CRITICAL_THRESHOLD": ("int",      "20",          "behavior",    False),
    "INTERACT_GRABBED_PROMPT":      ("str",  "",  "behavior", False),
    "INTERACT_RELEASED_PROMPT":     ("str",  "",  "behavior", False),
    "INTERACT_WINDOW_DISAPPEARED_PROMPT": ("str", "", "behavior", False),
    # Appearance
    "VISION_ENABLED":     ("bool",  False,    "appearance",  False),
    "VISION_SCALE":       ("float", "1",      "appearance",  False),
    "SKILLS_ENABLED":     ("str_list", "",     "appearance",  True),
    "PET_WIDTH":          ("int",   "125",     "appearance",  True),
    "PET_HEIGHT":         ("int",   "125",     "appearance",  True),
    "PET_FPS":            ("int",   "15",      "appearance",  True),
    "BUBBLE_MAX_WIDTH":   ("int",   "300",     "appearance",  True),
    "BUBBLE_FONT_SIZE":   ("int",   "14",      "appearance",  True),
    "SHOW_TRAY":          ("bool",  True,      "appearance",  True),
    "HIDE_CONSOLE":       ("bool",  True,      "appearance",  True),
    "LOG_LEVEL":          ("str",   "DEBUG",   "appearance",  False),
    # Personality
    "PET_PERSONALITY":     ("str",   "",        "personality", False),
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
        self._load_env()
        self._load_user_settings()

    def _load_env(self):
        """Load defaults from environment variables into instance attributes."""
        self.BRAIN = os.getenv("BRAIN", "local")
        self.LLM_MODEL = os.getenv("LLM_MODEL", "")
        self.LLM_KEY = os.getenv("LLM_KEY", "")
        self.LLM_URL = os.getenv("LLM_URL", "")
        self.OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

        self.LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "30"))
        self.LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
        self.LLM_RETRY_DELAY = float(os.getenv("LLM_RETRY_DELAY", "1"))
        self.LLM_RETRY_MAX_DELAY = float(os.getenv("LLM_RETRY_MAX_DELAY", "8"))
        self.LLM_CACHE_PROMPT = os.getenv("LLM_CACHE_PROMPT", "").lower() in ("1", "true", "yes")

        self.VISION_ENABLED = os.getenv("VISION_ENABLED", "false").lower() == "true"
        self.VISION_SCALE = float(os.getenv("VISION_SCALE", "1"))

        self.PET_WIDTH = int(os.getenv("PET_WIDTH", "125"))
        self.PET_HEIGHT = int(os.getenv("PET_HEIGHT", "125"))
        self.PET_FPS = int(os.getenv("PET_FPS", "15"))
        self.BUBBLE_MAX_WIDTH = int(os.getenv("BUBBLE_MAX_WIDTH", "300"))
        self.BUBBLE_FONT_SIZE = int(os.getenv("BUBBLE_FONT_SIZE", "14"))
        self.LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")

        self.ACTION_TIMEOUT_MS = int(os.getenv("ACTION_TIMEOUT_MS", "15000"))

        self.SCHEDULER_AUTO_START_FAST = os.getenv("SCHEDULER_AUTO_START_FAST", "true").lower() == "true"
        self.SCHEDULER_AUTO_START_MID = os.getenv("SCHEDULER_AUTO_START_MID", "true").lower() == "true"
        self.SCHEDULER_AUTO_START_SLOW = os.getenv("SCHEDULER_AUTO_START_SLOW", "true").lower() == "true"
        self.SCHEDULER_FAST_MS = int(os.getenv("SCHEDULER_FAST_MS", "1000"))
        self.SCHEDULER_MID_MS = int(os.getenv("SCHEDULER_MID_MS", "300000"))
        self.SCHEDULER_SLOW_MS = int(os.getenv("SCHEDULER_SLOW_MS", "300000"))
        self.SCHEDULER_IDLE_TIMEOUT_MS = int(os.getenv("SCHEDULER_IDLE_TIMEOUT_MS", "900000"))

        self.PET_PERSONALITY = os.getenv("PET_PERSONALITY", "")

        self.INTERACT_GRABBED_PROMPT = os.getenv("INTERACT_GRABBED_PROMPT", "")
        self.INTERACT_RELEASED_PROMPT = os.getenv("INTERACT_RELEASED_PROMPT", "")
        self.INTERACT_WINDOW_DISAPPEARED_PROMPT = os.getenv("INTERACT_WINDOW_DISAPPEARED_PROMPT", "")
        self.INTERACT_FED_PROMPT = os.getenv("INTERACT_FED_PROMPT", "")

        self.SKILLS_ENABLED = os.getenv("SKILLS_ENABLED", "").split(",") if os.getenv("SKILLS_ENABLED") else []

        self.SANITY_CRITICAL_THRESHOLD = int(os.getenv("SANITY_CRITICAL_THRESHOLD", "20"))
        self.SHOW_TRAY = os.getenv("SHOW_TRAY", "true").lower() == "true"
        self.HIDE_CONSOLE = os.getenv("HIDE_CONSOLE", "true").lower() == "true"

    def _load_user_settings(self):
        """Read overrides from settings.json and update instance attributes."""
        data = load_user_settings()
        self._user_settings = data
        for key, value in data.items():
            if key not in _KEY_META:
                continue
            type_name = _KEY_META[key][0]
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
        type_name = _KEY_META[key][0]
        converted = _convert(value, type_name)
        setattr(self, key, converted)
        save_user_setting(key, converted)
        needs_restart = [key] if _KEY_META[key][3] else []
        return (True, needs_restart)

    def reset(self, keys: list[str]):
        """Remove specified keys from settings.json and fall back to .env defaults."""
        delete_user_settings(keys)
        # re-read defaults from environment variables
        self._load_env()
        self._load_user_settings()


config = Config()
