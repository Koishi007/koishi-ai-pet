import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    
    PET_WIDTH = 125
    PET_HEIGHT = 125
    PET_FPS = 15
    BUBBLE_MAX_WIDTH = 300
    BUBBLE_FONT_SIZE = 14
    LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")

    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

    BRAIN = os.getenv("BRAIN", "local")                  # local / llm / ollama
    LLM_MODEL = os.getenv("LLM_MODEL", "") # 模型名
    LLM_KEY = os.getenv("LLM_KEY", "")                   # API Key
    LLM_URL = os.getenv("LLM_URL", "")                   # API 地址

    VISION_ENABLED = os.getenv("VISION_ENABLED", "false").lower() == "true"  # 模型是否支持 vision
    VISION_SCALE = float(os.getenv("VISION_SCALE", "1"))                    # 截图缩放比例（1=不缩放，下限锁1536px，0.5=缩一半）

    # LLM 超时与重试
    LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "30"))
    LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
    LLM_RETRY_DELAY = float(os.getenv("LLM_RETRY_DELAY", "1"))
    LLM_RETRY_MAX_DELAY = float(os.getenv("LLM_RETRY_MAX_DELAY", "8"))
    LLM_CACHE_PROMPT = os.getenv("LLM_CACHE_PROMPT", "").lower() in ("1", "true", "yes")

    # 动作最大持续时间
    ACTION_TIMEOUT_MS = int(os.getenv("ACTION_TIMEOUT_MS", "15000"))

    SCHEDULER_AUTO_START_FAST = os.getenv("SCHEDULER_AUTO_START_FAST", "true").lower() == "true"
    SCHEDULER_AUTO_START_MID = os.getenv("SCHEDULER_AUTO_START_MID", "true").lower() == "true"
    SCHEDULER_AUTO_START_SLOW = os.getenv("SCHEDULER_AUTO_START_SLOW", "true").lower() == "true"
    SCHEDULER_FAST_MS = int(os.getenv("SCHEDULER_FAST_MS", "1000"))
    SCHEDULER_MID_MS = int(os.getenv("SCHEDULER_MID_MS", "300000"))
    SCHEDULER_SLOW_MS = int(os.getenv("SCHEDULER_SLOW_MS", "300000"))
    SCHEDULER_IDLE_TIMEOUT_MS = int(os.getenv("SCHEDULER_IDLE_TIMEOUT_MS", "900000"))  # 15min 无操作暂停调度

    PET_PERSONALITY = os.getenv("PET_PERSONALITY", "")

    INTERACT_GRABBED_PROMPT = os.getenv("INTERACT_GRABBED_PROMPT", "")
    INTERACT_RELEASED_PROMPT = os.getenv("INTERACT_RELEASED_PROMPT", "")
    INTERACT_WINDOW_DISAPPEARED_PROMPT = os.getenv("INTERACT_WINDOW_DISAPPEARED_PROMPT", "")

    SKILLS_ENABLED = os.getenv("SKILLS_ENABLED", "").split(",") if os.getenv("SKILLS_ENABLED") else []

    SHOW_TRAY = os.getenv("SHOW_TRAY", "true").lower() == "true"  # 是否显示系统托盘


config = Config()