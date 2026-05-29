import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    
    APP_NAME = "DeskPet"
    PET_WIDTH = 125
    PET_HEIGHT = 125
    PET_FPS = 15
    BUBBLE_MAX_WIDTH = 300
    BUBBLE_FONT_SIZE = 14
    LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")

    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

    BRAIN = os.getenv("BRAIN", "local")                  # local / llm / ollama
    LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-pro") # 模型名
    LLM_KEY = os.getenv("LLM_KEY", "")                   # API Key
    LLM_URL = os.getenv("LLM_URL", "")                   # API 地址

    VISION_ENABLED = os.getenv("VISION_ENABLED", "false").lower() == "true"  # 模型是否支持 vision
    VISION_SCALE = float(os.getenv("VISION_SCALE", "1"))                    # 截图缩放比例（1=不缩放，下限锁1536px，0.5=缩一半）

    OCR_ENABLED = os.getenv("OCR_ENABLED", "true").lower() == "true"         # 是否启用 OCR 屏幕文字提取
    OCR_GPU = os.getenv("OCR_GPU", "false").lower() == "true"                # OCR 是否使用 GPU 加速
    OCR_LANGUAGES = os.getenv("OCR_LANGUAGES", "ch_sim,en").split(",")
    OCR_MAX_CHARS = int(os.getenv("OCR_MAX_CHARS", "500"))                  # OCR 文字送 LLM 的最大字符数

    NON_VISION_PROMPT_EXTRA = os.getenv("NON_VISION_PROMPT_EXTRA", "")

    VIEW_PROMPT_SYSTEM = os.getenv(
        "VIEW_PROMPT_SYSTEM",
        "你是桌面宠物的视觉模块，负责分析屏幕截图内容。"
        "用中文回答，简洁有帮助。",
    )
    VIEW_PROMPT_VISION = os.getenv(
        "VIEW_PROMPT_VISION",
        "请描述这张截图中的内容，用户可能在做什么？",
    )

    VISION_PROMPT_EXTRA = os.getenv("VISION_PROMPT_EXTRA", "")

    # LLM 超时与重试
    LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "30"))
    LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
    LLM_RETRY_DELAY = float(os.getenv("LLM_RETRY_DELAY", "1"))
    LLM_RETRY_MAX_DELAY = float(os.getenv("LLM_RETRY_MAX_DELAY", "8"))

    SCHEDULER_AUTO_START = os.getenv("SCHEDULER_AUTO_START", "false").lower() == "true"
    SCHEDULER_FAST_MS = int(os.getenv("SCHEDULER_FAST_MS", "1000"))
    SCHEDULER_MID_MS = int(os.getenv("SCHEDULER_MID_MS", "30000"))
    SCHEDULER_SLOW_MS = int(os.getenv("SCHEDULER_SLOW_MS", "300000"))

    PET_PERSONALITY = os.getenv("PET_PERSONALITY", "")

    SKILLS_ENABLED = os.getenv("SKILLS_ENABLED", "").split(",") if os.getenv("SKILLS_ENABLED") else []


config = Config()
