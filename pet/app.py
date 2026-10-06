"""KoishiAI 桌面宠物 — 主入口"""

import atexit
import ctypes
import logging
import os
import sys
import threading
from logging.handlers import TimedRotatingFileHandler

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMessageBox
from PySide6.QtGui import QIcon
from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot

from pet.ui.log_window import _LogRelay, LogWindowHandler
from pet.ui.styles import ICON_PATH
from pet.ui.pet_window import PetWindow
from pet.ui.system_tray import SystemTrayManager
from pet.ui.speech_bubble import SpeechBubble
from pet.ui.emotion import EmotionBubble
from pet.ui.chat_bubble import ChatBubble
from pet.ui.feed_bubble import FeedBubble
from pet.ui.music_bubble import MusicBubble
from pet.ui.file_bubble import FileBubble
from pet.agent import PetAgent
from pet.brain.prompts import interact_fed_prompt, interact_take_a_bite_prompt
from pet.tools import load_tools
from pet.tools.context import TOOL_CTX
from pet.tools.registry import TOOL_REGISTRY
from pet.file_intake import KIND_LABELS, load_image, load_text
from pet.config import config
from pet.auto_start import set_auto_start
from pet.crash_reporter import get_guard
from pet.single_instance import SingleInstanceGuard
from pet.version_check import UpdateChecker

logger = logging.getLogger(__name__)

_FILE_META_MAX_NAMES = 5


class _FileActionDispatcher(QObject):
    """把后台读到的正文送回 GUI 线程再触发管线（trigger 会读窗口坐标，属 GUI 线程）。"""

    ready = Signal(str, object, object, str)

    def __init__(self, agent):
        super().__init__()
        self._agent = agent
        self.ready.connect(self._dispatch)

    @Slot(str, object, object, str)
    def _dispatch(self, action_id: str, refs, image, body: str) -> None:
        meta = _file_meta_text(refs)
        if action_id == "taste":
            names = "、".join(_file_meta_lines(refs))
            # 文件动作不做冷却：同一个文件重复拖入各触发一次
            self._agent.trigger("interact", hint=interact_take_a_bite_prompt(names),
                                attachment_text=body, attachment_image=image,
                                record_context=True, context_hint=meta,
                                delay_ms=150, cooldown_ms=0, is_play_loading=False,
                                thinking=False, enable_tools=False)
        else:
            self._agent.trigger("analyze", message=meta,
                                attachment_text=body, attachment_image=image)


def _file_meta_lines(refs) -> list[str]:
    """每条一项：名称、类型、大小；目录不带大小。"""
    lines = []
    for ref in refs[:_FILE_META_MAX_NAMES]:
        label = KIND_LABELS.get(ref.kind, ref.kind)
        size = "" if ref.kind == "dir" else f"，{ref.size} 字节"
        lines.append(f"{ref.name}（{label}{size}）")
    return lines


def _file_meta_text(refs) -> str:
    """元信息：进 message 与 context_hint 落库的那一份，不含正文。"""
    return "用户把文件交给了你：\n" + "\n".join(f"- {line}" for line in _file_meta_lines(refs))


def _read_file_payload(refs, limit: int) -> tuple[str, object]:
    """后台线程读取：返回（正文片段, 首张可用图片）；读不出的项只记日志。"""
    if not config.FILE_DROP_READ_CONTENT:
        return "", None
    chunks: list[str] = []
    image = None
    for ref in refs:
        if ref.kind == "text":
            text, reason = load_text(ref.path, limit)
            if text:
                chunks.append(f"【{ref.name}】\n{text}")
            elif reason:
                logger.info("[FileDrop] 读不出内容: %s %s", ref.name, reason)
        elif ref.kind == "image" and image is None and config.VISION_ENABLED:
            image = load_image(ref.path, config.FILE_DROP_MAX_PIXELS)
    return "\n\n".join(chunks), image


def _start_file_action(dispatcher: "_FileActionDispatcher", action_id: str, refs) -> None:
    """文件气泡选定动作：核心动作后台读内容，工具动作交给注册表的 handler。"""
    if action_id.startswith("tool:"):
        _run_tool_file_action(action_id, refs)
        return
    limit = config.FILE_DROP_TASTE_CHARS if action_id == "taste" else config.FILE_DROP_MAX_CHARS

    def worker() -> None:
        body, image = _read_file_payload(refs, limit)
        dispatcher.ready.emit(action_id, list(refs), image, body)

    threading.Thread(target=worker, daemon=True, name="file-read").start()


def _run_tool_file_action(action_id: str, refs) -> None:
    """工具动作：按 id 找 handler 在后台执行，播报由工具自己负责。"""
    _, _, rest = action_id.partition(":")
    tool_name, _, action_key = rest.partition(":")
    for action in TOOL_REGISTRY.file_actions():
        if action["tool"] == tool_name and action["id"] == action_key:
            threading.Thread(target=_call_tool_handler,
                             args=(action_id, action["handler"], list(refs)),
                             daemon=True, name="file-tool-action").start()
            return
    logger.warning("[FileDrop] 工具动作已不可用: %s", action_id)


def _call_tool_handler(action_id: str, handler, files) -> None:
    """执行工具动作；异常按失败处理，不让它冒到线程钩子。"""
    try:
        result = handler(files)
    except Exception:
        logger.exception(f"[FileDrop] 工具动作异常: {action_id}")
        _report_tool_result({"ok": False, "summary": "这个动作没能完成（错误已记入日志）"})
        return
    _report_tool_result(result)


def _report_tool_result(result) -> None:
    """失败结果按 summary 提示；成功由工具自己播报，非 dict 的返回值忽略。"""
    if not isinstance(result, dict) or result.get("ok"):
        return
    summary = str(result.get("summary") or "").strip()
    if summary:
        TOOL_CTX.speech(summary)


def _warn_already_running() -> None:
    """提示已有实例在运行（独立 QApplication，无控制台时也能弹出提示）。"""
    try:
        _app = QApplication.instance() or QApplication(sys.argv)
        _app.setQuitOnLastWindowClosed(False)
        _box = QMessageBox(
            QMessageBox.Icon.Warning,
            "Koishi AI Pet",
            "已检测到另一个 Koishi AI Pet 正在运行。\n"
            "请从系统托盘退出当前实例后重试。",
            QMessageBox.StandardButton.Ok,
        )
        _box.setWindowIcon(QIcon(ICON_PATH))
        _box.exec()
    except Exception as e:
        logger.error("[Main] 单实例提示失败: %s", e)


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

    # 单实例限制：架构不支持多开，已被占用时提示并退出
    _guard = SingleInstanceGuard()
    if not _guard.try_acquire():
        if _guard.is_locked_by_other():
            _warn_already_running()
            sys.exit(1)
        # 锁机制不可用（权限/未知错误）：放行并告警，避免误伤
        logger.warning("[Main] 无法建立单实例锁(%s)，本次启动不受单实例限制", _guard.error())
    else:
        # 正常退出由 aboutToQuit 释放；此处兜底覆盖初始化中途异常等场景
        atexit.register(_guard.release)

    # 应用上次更新遗留的 update.bat.new / update.sh.new
    from pet.self_update import apply_pending_update_scripts
    apply_pending_update_scripts()

    # 文件日志：按天切分，保留 3 天
    _log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(_log_dir, exist_ok=True)
    _file_handler = TimedRotatingFileHandler(
        filename=os.path.join(_log_dir, "koishiai.log"),
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

    logger.info("===== KoishiAI 启动 =====")
    logger.info(f"BRAIN={config.BRAIN}, MODEL={config.LLM_MODEL}")

    # 按用户配置开关崩溃信息收集
    get_guard().set_enabled(config.CRASH_REPORT_ENABLED)

    # 启动时加载工具插件
    load_tools(config.TOOLS_ENABLED)

    # 应用开机自启设置
    set_auto_start(config.AUTO_START_ON_BOOT)

    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("KoishiAI.App.1")
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
    speech_bubble = SpeechBubble(window)
    emotion_bubble = EmotionBubble(window)
    window.set_speech_bubble(speech_bubble)
    window.set_emotion_bubble(emotion_bubble)
    chat_bubble = ChatBubble(window)
    window.set_chat_bubble(chat_bubble)
    chat_bubble.chat_submitted.connect(
        lambda text: agent.trigger("chat", message=text)
    )

    feed_bubble = FeedBubble(window)
    window.set_feed_bubble(feed_bubble)
    feed_bubble.feed_submitted.connect(
        # 投喂不做冷却：同样的食物重复喂各触发一次
        lambda text: agent.trigger("interact", hint=interact_fed_prompt(text),
                                    record_context=True, context_hint=f"用户投喂了{text}",
                                    cooldown_ms=0, enable_tools=False)
    )

    music_bubble = MusicBubble(window)
    window.set_music_bubble(music_bubble)

    file_bubble = FileBubble(window)
    window.set_file_bubble(file_bubble)
    file_action = _FileActionDispatcher(agent)
    file_bubble.action_chosen.connect(
        lambda action_id, refs: _start_file_action(file_action, action_id, refs)
    )

    # 觅食窗口：装配层注入窗口工厂，food 层不依赖 UI
    from pet.food.food import FOOD
    from pet.ui.food_window import FoodWindow
    FOOD.set_window_factory(FoodWindow)

    # 游戏面板：由 game__play 跨线程驱动渲染
    from pet.ui.tic_tac_toe_panel import TicTacToePanel
    from pet.ui.rps_panel import RpsPanel
    from pet.ui.twenty_questions_panel import TwentyQuestionsPanel
    tictac_panel = TicTacToePanel()
    tictac_panel.set_pet_window(window)
    rps_panel = RpsPanel()
    rps_panel.set_pet_window(window)
    tq_panel = TwentyQuestionsPanel()
    tq_panel.set_pet_window(window)
    # 统一分发：按游戏名路由到对应面板，新游戏只需在此加一行映射
    _game_panel_handlers = {
        "tic_tac_toe": tictac_panel.render,
        "rps": rps_panel.render,
        "twenty_questions": tq_panel.render,
    }
    def _dispatch_game_board(game_name, payload):
        handler = _game_panel_handlers.get(game_name)
        if handler is not None:
            handler(game_name, payload)
    agent.game_board_requested.connect(_dispatch_game_board)

    agent.action_requested.connect(window.queue_enqueue_action)
    agent.action_batch_started.connect(window.on_action_batch_started)
    agent.emotion_requested.connect(
        lambda e, d: emotion_bubble.show_emotion(e, d) if window.isVisible() else None
    )
    agent.mood.affection_increased.connect(
        lambda: window.particles.spawn("hearts") if window.isVisible() else None
    )
    agent.speak_requested.connect(
        lambda text, duration=5000: speech_bubble.show_text(text, duration) if window.isVisible() else None
    )
    agent.speak_stream_start.connect(
        lambda: speech_bubble.start_stream() if window.isVisible() else None
    )
    agent.speak_stream_chunk.connect(
        lambda chunk: speech_bubble.append_stream(chunk) if window.isVisible() else None
    )
    agent.speak_stream_end.connect(
        lambda duration: speech_bubble.end_stream(duration) if window.isVisible() else None
    )
    agent.llm_loading.connect(
        lambda loading: window.particles.start_loading() if window.isVisible() and loading else window.particles.stop_loading() if not loading else None
    )
    agent.state_changed.connect(
        lambda s: chat_bubble.set_busy(s in ("autonomous", "interacting"))
    )
    agent.state_changed.connect(
        lambda s: feed_bubble.set_busy(s in ("autonomous", "interacting"))
    )
    # 自主/对话开始即收起文件气泡：打开期间未选的动作已过期，也避免打断在跑的脑线程
    agent.state_changed.connect(
        lambda s: file_bubble.hide_bubble() if s in ("autonomous", "interacting") else None
    )

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

    _updater = UpdateChecker()

    def _on_update_available(latest_tag: str, local_ver: str):
        logger.info(f"[VersionCheck] 发现新版本: v{latest_tag}（当前 v{local_ver}）")
        if tray.tray_icon:
            tray.tray_icon.showMessage(
                "发现新版本",
                f"Koishi AI Pet v{latest_tag} 已发布（当前 v{local_ver}）。\n"
                f"运行项目目录下的 update.bat / update.sh 即可更新。",
                QSystemTrayIcon.MessageIcon.Information,
                8000,
            )

    _updater.update_available.connect(_on_update_available, Qt.ConnectionType.QueuedConnection)
    QTimer.singleShot(5000, _updater.check)

    def _close_all_windows():
        """关闭所有顶层窗口（PetWindow 除外，它最后关）。"""
        # topLevelWidgets() 覆盖全部顶层窗口，含隐藏未销毁的
        for _w in app.topLevelWidgets():
            if _w is window:
                continue
            try:
                if hasattr(_w, "_force_close"):
                    _w._force_close = True
                if _w.isVisible():
                    _w.close()
                else:
                    # 隐藏但未销毁的窗口：先探测底层对象存活，再回收
                    alive = True
                    try:
                        _ = _w.winId()
                    except RuntimeError:
                        alive = False
                    if alive:
                        _w.deleteLater()
            except RuntimeError:
                pass
            except Exception as e:
                logger.warning(f"shutdown: close {_w.objectName() or type(_w).__name__} failed: {e}")

    def _do_quit():
        """退出应用：关闭所有窗口 → 停止 agent → quit。"""
        logger.info("shutting down...")
        if _hotkey_mgr:
            try:
                _hotkey_mgr.stop()
            except Exception as e:
                logger.warning(f"shutdown: hotkey stop failed: {e}")
        if _voice_session:
            try:
                _voice_session.deleteLater()
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

        _close_all_windows()

        try:
            agent.stop()
        except Exception as e:
            logger.warning(f"shutdown: agent stop failed: {e}")
        try:
            window.shutdown()
            window.close()
        except Exception as e:
            logger.warning(f"shutdown: window close failed: {e}")
        try:
            tray.hide()
        except Exception as e:
            logger.warning(f"shutdown: tray hide failed: {e}")

        app.quit()

    def _shutdown():
        """aboutToQuit 回调：轻量善后。"""
        logging.getLogger().removeHandler(_log_handler)
        # 正常退出：清除启动标记，避免下次启动误报异常退出
        get_guard().clear_marker()
        _guard.release()

    app.aboutToQuit.connect(_shutdown)

    # 将退出函数注入到需要的地方
    window._quit_fn = _do_quit
    tray._quit_fn = _do_quit

    # 初始化完成：更新启动标记，区分"启动中途崩溃"与"正常运行中崩溃"
    get_guard().mark_started()
    logger.info("Entering event loop")
    code = app.exec()
    # 仍有 QThread 运行时跳过解释器清理：~QThread 析构运行中的线程会触发 qFatal abort
    try:
        threads_running = agent.has_running_threads()
    except Exception:
        threads_running = False
    if threads_running:
        logger.warning("brain threads still running at exit, skipping interpreter teardown")
        logging.shutdown()
        os._exit(code)
    sys.exit(code)
