import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    APP_NAME = "DeskPet"
    WINDOW_WIDTH = 200
    WINDOW_HEIGHT = 200
    FPS = 30
    BUBBLE_MAX_WIDTH = 300
    BUBBLE_FONT_SIZE = 14
    LOG_LEVEL = "DEBUG"

    # ── 各用途 brain 配置 ──
    # 每项包含：BRAIN（"local" 或 "llm"）、MODEL、MODEL_KEY、MODEL_URL、PROMPT

    # ACTION：行为决策（动画、空闲动作）
    ACTION_BRAIN = os.getenv("ACTION_BRAIN", "local")
    ACTION_MODEL = os.getenv("ACTION_MODEL", "")
    ACTION_MODEL_KEY = os.getenv("ACTION_MODEL_KEY", "")
    ACTION_MODEL_URL = os.getenv("ACTION_MODEL_URL", "")
    ACTION_PROMPT_DECIDE = os.getenv(
        "ACTION_PROMPT_DECIDE",
        "You are a virtual desktop pet. Based on the context, "
        "choose ONE action from: {actions}.\n"
        "Context: {context}\n"
        "Respond with only the action name, nothing else.",
    )

    # VIEW：屏幕分析 / 视觉模型（需支持 vision 的模型，如 GPT-4o）
    VIEW_BRAIN = os.getenv("VIEW_BRAIN", "local")
    VIEW_MODEL = os.getenv("VIEW_MODEL", "")
    VIEW_MODEL_KEY = os.getenv("VIEW_MODEL_KEY", "")
    VIEW_MODEL_URL = os.getenv("VIEW_MODEL_URL", "")
    VIEW_PROMPT_SYSTEM = os.getenv(
        "VIEW_PROMPT_SYSTEM",
        "你是桌面宠物的视觉模块，负责分析屏幕截图内容。"
        "用中文回答，简洁有帮助。",
    )
    VIEW_PROMPT_VISION = os.getenv(
        "VIEW_PROMPT_VISION",
        "请描述这张截图中的内容，用户可能在做什么？",
    )

    # CHAT：对话交互 —— 主交互 brain
    CHAT_BRAIN = os.getenv("CHAT_BRAIN", "llm")
    CHAT_MODEL = os.getenv("CHAT_MODEL", "deepseek-v4-pro")
    CHAT_MODEL_KEY = os.getenv("CHAT_MODEL_KEY", "")
    CHAT_MODEL_URL = os.getenv("CHAT_MODEL_URL", "https://api.deepseek.com")
    CHAT_PROMPT_SYSTEM = os.getenv(
        "CHAT_PROMPT_SYSTEM",
        "You are a cute desktop pet. "
        "Keep responses brief and playful.",
    )
    CHAT_PROMPT_GREET = os.getenv(
        "CHAT_PROMPT_GREET",
        "Say a short, friendly greeting!",
    )

    # 技能开关
    SCREEN_READER_ENABLED = True
    SYSTEM_MONITOR_ENABLED = True


config = Config()
