"""DeskPet 桌面宠物 — 主入口"""

import ctypes
import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler

from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from PySide6.QtGui import QIcon

from pet.ui.log_window import _LogRelay, LogWindowHandler
from pet.ui.styles import ICON_PATH
from pet.ui.pet_window import PetWindow
from pet.ui.system_tray import SystemTrayManager
from pet.ui.speech_bubble import SpeechBubble
from pet.ui.emotion import EmotionBubble
from pet.ui.chat_bubble import ChatBubble
from pet.ui.feed_bubble import FeedBubble
from pet.agent import PetAgent
from pet.brain.prompts import interact_fed_prompt
from pet.tools import load_tools
from pet.tools.context import TOOL_CTX
from pet.config import config

logger = logging.getLogger(__name__)


def main():
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="[%(name)s] %(message)s",
    )
    # 根 logger 降到 DEBUG，让各级 handler 自己过滤（GUI 热切换依赖这个）
    logging.getLogger().setLevel(logging.DEBUG)
    # basicConfig 的 StreamHandler 默认 NOTSET，会继承 root level → 显式设为 LOG_LEVEL
    _console_level = getattr(logging, config.LOG_LEVEL, logging.INFO)
    for h in logging.getLogger().handlers:
        if isinstance(h, logging.StreamHandler) and h.level == logging.NOTSET:
            h.setLevel(_console_level)
    # 静默 HTTP 库的 DEBUG 日志（它们会打印完整的 base64 图片数据）
    for _lib in ("httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(_lib).setLevel(logging.WARNING)

    # 文件日志：按天切分，保留 3 天
    _log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(_log_dir, exist_ok=True)
    _file_handler = TimedRotatingFileHandler(
        filename=os.path.join(_log_dir, "deskpet.log"),
        when="midnight",
        interval=1,
        backupCount=3,
        encoding="utf-8",
    )
    _file_handler.setFormatter(logging.Formatter(
        "[%(asctime)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    _file_handler.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    logging.getLogger().addHandler(_file_handler)

    # GUI 日志桥接 (INFO 级)
    _log_relay = _LogRelay()
    _log_handler = LogWindowHandler(_log_relay, level=logging.INFO)
    _log_relay.set_handler(_log_handler)
    logging.getLogger().addHandler(_log_handler)

    if sys.platform == "win32" and config.HIDE_CONSOLE:
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
            else:
                ctypes.windll.kernel32.FreeConsole()
        except Exception:
            pass

    logger.info("===== DeskPet 启动 =====")
    logger.info(f"BRAIN={config.BRAIN}, MODEL={config.LLM_MODEL}")

    # 启动时加载工具插件
    load_tools(config.TOOLS_ENABLED)

    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DeskPet.App.1")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    try:
        app.setWindowIcon(QIcon(ICON_PATH))
    except Exception:
        pass

    agent = PetAgent()
    TOOL_CTX.bind(agent)
    window = PetWindow()
    window.set_agent(agent)
    window.set_app(app)
    window.set_log_relay(_log_relay)
    agent.set_pet_window(window)
    bubble = SpeechBubble(window)
    emotion_bubble = EmotionBubble(window)

    chat_bubble = ChatBubble(window)
    window.set_chat_bubble(chat_bubble)
    chat_bubble.chat_submitted.connect(
        lambda text: agent.trigger("chat", message=text)
    )

    feed_bubble = FeedBubble(window)
    window.set_feed_bubble(feed_bubble)
    feed_bubble.feed_submitted.connect(
        lambda text: agent.trigger("interact", hint=interact_fed_prompt(text),
                                    record_context=True, context_hint=f"用户投喂了{text}")
    )

    agent.action_requested.connect(window.queue_enqueue_action)
    agent.emotion_requested.connect(emotion_bubble.show_emotion)
    agent.emotion_requested.connect(
        lambda e, d: window.particles.spawn("hearts") if e == "love" else None
    )
    agent.mood.affection_increased.connect(
        lambda: window.particles.spawn("hearts")
    )
    agent.speak_requested.connect(bubble.show_text)
    agent.speak_stream_start.connect(bubble.start_stream)
    agent.speak_stream_chunk.connect(bubble.append_stream)
    agent.speak_stream_end.connect(bubble.end_stream)
    agent.llm_loading.connect(
        lambda loading: window.particles.start_loading() if loading else window.particles.stop_loading()
    )
    agent.state_changed.connect(
        lambda s: chat_bubble.set_busy(s in ("autonomous", "interacting"))
    )
    agent.state_changed.connect(
        lambda s: feed_bubble.set_busy(s in ("autonomous", "interacting"))
    )

    # ── 语音输入 ──
    _voice_session = None
    _hotkey_mgr = None

    if config.VOICE_INPUT_ENABLED and config.XF_APPID:
        from pet.voice.voice_session import VoiceSession
        from pet.voice.hotkey_manager import HotkeyManager

        _voice_session = VoiceSession()
        agent._voice_session = _voice_session
        _hotkey_mgr = HotkeyManager()

        _hotkey_mgr.voice_start.connect(_voice_session.start_recording)
        _hotkey_mgr.voice_stop.connect(_voice_session.stop_recording)

        _voice_session.partial_text.connect(chat_bubble.set_voice_text)
        _voice_session.transcription_done.connect(chat_bubble.finalize_voice_text)

        chat_bubble.enter_intercept.connect(_hotkey_mgr.set_intercept_enter)
        _hotkey_mgr.enter_pressed.connect(chat_bubble._on_submit)

        _voice_session.recording_started.connect(chat_bubble.show_voice_input)
        _voice_session.recording_started.connect(lambda: chat_bubble.set_recording_icon(True))

        _voice_session.recording_stopped.connect(lambda: chat_bubble.set_recording_icon(False))
        _voice_session.transcription_done.connect(lambda _: chat_bubble.set_recording_icon(False))

        _voice_session.error.connect(lambda msg: logger.error(f"[Voice] {msg}"))

        _hotkey_mgr.start()
        logger.info("[Main] voice input initialized")

    window.show()
    agent.start()

    tray = SystemTrayManager(app, window)
    logger.info("SystemTrayManager ready")

    agent.notify_requested.connect(
        lambda t, m, d: tray.tray_icon.showMessage(t, m, QSystemTrayIcon.MessageIcon.Information, d)
        if tray.tray_icon else None
    )

    tray.set_agent(agent)

    def _shutdown():
        logger.info("shutting down...")
        if _hotkey_mgr:
            try:
                _hotkey_mgr.stop()
            except Exception as e:
                logger.warning(f"shutdown: hotkey stop failed: {e}")
        if _voice_session:
            try:
                _voice_session.disconnect()
            except Exception as e:
                logger.warning(f"shutdown: voice disconnect failed: {e}")
        try:
            agent.behavior.llm_stats.save()
            agent.behavior.llm_stats.close()
        except Exception as e:
            logger.warning(f"shutdown: llm_stats save failed: {e}")
        try:
            agent.behavior._save_context(record_shutdown=True)
        except Exception as e:
            logger.warning(f"shutdown: context save failed: {e}")
        logging.getLogger().removeHandler(_log_handler)
        try:
            from pet.ui.settings_window import SettingsWindow
            if SettingsWindow._instance:
                SettingsWindow._instance.close()
        except Exception:
            pass
        try:
            agent.stop()
            window.shutdown()
            window.close()
            tray.hide()
        except RuntimeError:
            pass

    app.aboutToQuit.connect(_shutdown)

    logger.info("Entering event loop")
    sys.exit(app.exec())
