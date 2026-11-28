"""全局热键管理器，使用 pynput 监听按键。"""

import logging
import time

try:
    from pynput import keyboard
except ImportError:
    keyboard = None  # 无显示服务器的环境（CI、无 X11 的 Linux）导入即失败

from PySide6.QtCore import QObject, Signal

from pet.config import config

logger = logging.getLogger(__name__)

TRIGGER_DOUBLE = "double"   # 配置取值 "single" 走默认分支，不需要同名常量

TAP_SECONDS = 0.35    # 短按上限：按得更久就不算「第一击」
ARM_SECONDS = 1.20    # 短按之后等待第二击按下的窗口


class HotkeyManager(QObject):
    """全局热键监听。两种模式都按住录音，语音输入完成后可拦截回车提交。"""

    voice_start = Signal()
    voice_stop = Signal()
    enter_pressed = Signal()  # 回车提交（语音输入激活时）
    error_occurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._listener: keyboard.Listener | None = None
        self._hotkey = config.VOICE_HOTKEY.lower()
        self._mode = config.VOICE_TRIGGER_MODE
        self._key_down = False       # 防止按键连发导致高频翻转
        self._recording = False      # 当前是否已发出 voice_start
        self._press_at = 0.0         # 本次按下的时刻
        self._armed_at = 0.0         # double 模式：第一击短按完成的时刻，0 表示未解除防备
        self._intercept_enter = False
        self._enter_handled = False

    def start(self):
        """启动全局键盘监听线程。"""
        if self._listener and self._listener.running:
            return
        if keyboard is None:
            msg = "pynput 不可用，无法监听全局热键"
            logger.error(f"[HotkeyManager] {msg}")
            self.error_occurred.emit(msg)
            return
        self._hotkey = config.VOICE_HOTKEY.lower()
        self._mode = config.VOICE_TRIGGER_MODE
        try:
            self._listener = keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release,
            )
            self._listener.daemon = True
            self._listener.start()
            logger.info(f"[HotkeyManager] listening for '{self._hotkey}' ({self._mode})")
        except Exception as e:
            logger.error(f"[HotkeyManager] failed to start listener: {e}")
            self.error_occurred.emit(str(e))

    def stop(self):
        """停止键盘监听。"""
        if self._listener and self._listener.running:
            self._listener.stop()
            self._listener = None
        self._key_down = False
        logger.info("[HotkeyManager] stopped")

    def reload(self):
        """重新读取热键与触发方式，用于设置页保存后即时生效。"""
        if self._recording:
            self._end()
        self._hotkey = config.VOICE_HOTKEY.lower()
        self._mode = config.VOICE_TRIGGER_MODE
        self._key_down = False
        self._press_at = 0.0
        self._armed_at = 0.0
        logger.info(f"[HotkeyManager] reloaded: '{self._hotkey}' ({self._mode})")

    def set_intercept_enter(self, enabled: bool):
        """开启/关闭回车拦截模式（语音输入完成后开启）。"""
        self._intercept_enter = enabled
        self._enter_handled = False
        logger.debug(f"[HotkeyManager] intercept_enter: {enabled}")

    def _on_press(self, key):
        """按键按下时处理。"""
        key_name = self._key_name(key)

        if self._intercept_enter and key_name == 'enter':
            if not self._enter_handled:
                self._enter_handled = True
                logger.info("[HotkeyManager] enter pressed → submit")
                self.enter_pressed.emit()
            return

        if self._key_down:
            return  # 防止 auto-repeat 连发
        if key_name != self._hotkey:
            return
        self._key_down = True
        self._press_at = time.monotonic()

        if self._mode == TRIGGER_DOUBLE and not self._armed():
            return  # 第一击只等松开时判定是否为短按
        self._armed_at = 0.0
        self._begin()

    def _on_release(self, key):
        """按键释放时处理。"""
        key_name = self._key_name(key)

        if key_name == 'enter':
            self._enter_handled = False
            return

        if key_name != self._hotkey:
            return
        if not self._key_down:
            return
        self._key_down = False

        was_recording = self._recording
        if was_recording:
            self._end()
        if self._mode != TRIGGER_DOUBLE or was_recording:
            return
        if time.monotonic() - self._press_at <= TAP_SECONDS:
            self._armed_at = time.monotonic()
            logger.info("[HotkeyManager] armed by short tap, waiting for second press")

    def _armed(self) -> bool:
        """double 模式：第一击的短按是否仍在有效窗口内。"""
        if not self._armed_at:
            return False
        return time.monotonic() - self._armed_at <= ARM_SECONDS

    def _begin(self):
        if self._recording:
            return
        self._recording = True
        logger.info(f"[HotkeyManager] press: {self._hotkey} → start recording")
        self.voice_start.emit()

    def _end(self):
        if not self._recording:
            return
        self._recording = False
        self._armed_at = 0.0
        logger.info(f"[HotkeyManager] release: {self._hotkey} → stop recording")
        self.voice_stop.emit()

    @staticmethod
    def _key_name(key) -> str:
        try:
            return key.char.lower() if hasattr(key, 'char') and key.char else key.name.lower()
        except Exception:
            return ""
