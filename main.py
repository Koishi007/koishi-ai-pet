import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
from PySide6.QtWidgets import QApplication
from pet.ui.pet_window import PetWindow
from pet.ui.system_tray import SystemTrayManager
from pet.ui.bubble import SpeechBubble
from pet.ui.emotion import EmotionBubble
from pet.ui.chat_bubble import ChatBubble
from pet.agent import PetAgent
from pet.skills import load_skills
from pet.skills.context import SKILL_CTX
from config import config

logger = logging.getLogger(__name__)


def main():
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="[%(name)s] %(message)s",
    )
    # 静默 HTTP 库的 DEBUG 日志（它们会打印完整的 base64 图片数据）
    for _lib in ("httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(_lib).setLevel(logging.WARNING)

    # 文件日志：按天切分，保留 7 天
    _log_dir = "logs"
    os.makedirs(_log_dir, exist_ok=True)
    _file_handler = TimedRotatingFileHandler(
        filename=os.path.join(_log_dir, "deskpet.log"),
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8",
    )
    _file_handler.setFormatter(logging.Formatter(
        "[%(asctime)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    _file_handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(_file_handler)

    logger.info("===== DeskPet 启动 =====")
    logger.info(f"BRAIN={config.BRAIN}, MODEL={config.LLM_MODEL}")

    # 启动时加载技能插件
    load_skills(config.SKILLS_ENABLED)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    agent = PetAgent()
    SKILL_CTX.bind(agent)
    window = PetWindow()
    window.set_agent(agent)
    window.set_app(app)
    agent.set_pet_window(window)  # 供窗口坐标探测用
    bubble = SpeechBubble(window)
    emotion_bubble = EmotionBubble(window)

    chat_bubble = ChatBubble(window)
    window.set_chat_bubble(chat_bubble)
    chat_bubble.chat_submitted.connect(
        lambda text: agent.trigger("chat", message=text)
    )

    agent.action_requested.connect(window.queue_enqueue_action)
    agent.emotion_requested.connect(emotion_bubble.show_emotion)
    agent.speak_requested.connect(bubble.show_text)
    agent.speak_stream_start.connect(bubble.start_stream)
    agent.speak_stream_chunk.connect(bubble.append_stream)
    agent.speak_stream_end.connect(bubble.end_stream)
    agent.state_changed.connect(
        lambda s: chat_bubble.set_busy(s in ("autonomous", "interacting"))
    )

    window.show()
    agent.start()

    tray = SystemTrayManager(app, window)
    logger.info("SystemTrayManager ready")

    def _shutdown():
        logger.info("shutting down...")
        try:
            agent.stop()
            window.shutdown()
            window.close()
            tray.hide()
        except RuntimeError:
            pass  # C++ 对象可能已被 Qt 提前销毁

    app.aboutToQuit.connect(_shutdown)

    logger.info("Entering event loop")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
