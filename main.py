import logging
import sys
from datetime import datetime
from PySide6.QtWidgets import QApplication
from pet.ui.pet_window import PetWindow
from pet.ui.system_tray import SystemTrayManager
from pet.ui.bubble import SpeechBubble
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
    logger.info(f"===== DeskPet 启动 =====")
    logger.info(f"BRAIN={config.BRAIN}, MODEL={config.LLM_MODEL}")

    # 启动时加载技能插件
    load_skills(config.SKILLS_ENABLED)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    agent = PetAgent()
    SKILL_CTX.bind(agent)
    window = PetWindow()
    agent.set_pet_window(window)  # 供窗口坐标探测用
    bubble = SpeechBubble(window)

    chat_bubble = ChatBubble(window)
    window.set_chat_bubble(chat_bubble)
    chat_bubble.chat_submitted.connect(
        lambda text: agent.trigger("chat", message=text)
    )

    agent.action_requested.connect(window.queue_enqueue_action)
    agent.speak_requested.connect(bubble.show_text)

    window.show()
    agent.start()

    tray = SystemTrayManager(app, window, agent)
    logger.info(f"SystemTrayManager ready")

    def _shutdown():
        logger.info(f"shutting down...")
        try:
            agent.stop()
            window.shutdown()
            window.close()
            tray.tray_icon.hide()
        except RuntimeError:
            pass  # C++ 对象可能已被 Qt 提前销毁

    app.aboutToQuit.connect(_shutdown)

    logger.info(f"Entering event loop")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
