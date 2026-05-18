class Config:
    APP_NAME = "DeskPet"
    WINDOW_WIDTH = 200
    WINDOW_HEIGHT = 200
    FPS = 30
    BUBBLE_MAX_WIDTH = 300
    BUBBLE_FONT_SIZE = 14
    LOG_LEVEL = "DEBUG"

    # AI Brains
    BRAIN_TYPE = "openai"  # "openai" or "local"
    OPENAI_API_KEY = "sk-d1e27c7c262145e0b9674bd701ff3d1a"
    OPENAI_BASE_URL = "https://api.deepseek.com"
    OPENAI_MODEL = "deepseek-v4-pro"
    LOCAL_MODEL_PATH = ""

    # Skills
    SCREEN_READER_ENABLED = True
    SYSTEM_MONITOR_ENABLED = True


config = Config()
