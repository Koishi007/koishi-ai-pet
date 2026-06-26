"""全局热键管理器，使用 pynput 监听按键。"""

import logging

from pynput import keyboard
from PySide6.QtCore import QObject, Signal

from pet.config import config

logger = logging.getLogger(__name__)


class HotkeyManager(QObject):
    """全局热键监听。长按触发录音，松开停止。语音输入完成后可拦截回车提交。"""

    voice_start = Signal()
    voice_stop = Signal()
    enter_pressed = Signal()  # 回车提交（语音输入激活时）
    error_occurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._listener: keyboard.Listener | None = None
        self._hotkey = config.VOICE_HOTKEY.lower()
        self._key_down = False  # 防止按键连发导致高频翻转
        self._intercept_enter = False  # 是否拦截回车
        self._enter_handled = False  # 防止回车连发

    def start(self):
        """启动全局键盘监听线程。"""
        if self._listener and self._listener.running:
            return
        try:
            self._listener = keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release,
            )
            self._listener.daemon = True
            self._listener.start()
            logger.info(f"[HotkeyManager] listening for '{self._hotkey}'")
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

    def set_intercept_enter(self, enabled: bool):
        """开启/关闭回车拦截模式（语音输入完成后开启）。"""
        self._intercept_enter = enabled
        self._enter_handled = False
        logger.debug(f"[HotkeyManager] intercept_enter: {enabled}")

    def _on_press(self, key):
        """按键按下时处理。"""
        key_name = self._key_name(key)

        # 回车拦截（语音输入完成后的提交）
        if self._intercept_enter and key_name == 'enter':
            if not self._enter_handled:
                self._enter_handled = True
                logger.info("[HotkeyManager] enter pressed → submit")
                self.enter_pressed.emit()
            return

        # 录音热键
        if self._key_down:
            return  # 防止 auto-repeat 连发
        if key_name != self._hotkey:
            return
        self._key_down = True
        logger.info(f"[HotkeyManager] press: {self._hotkey} → start recording")
        self.voice_start.emit()

    def _on_release(self, key):
        """按键释放时处理。"""
        key_name = self._key_name(key)

        # 回车释放时重置防连发标记
        if key_name == 'enter':
            self._enter_handled = False
            return

        # 录音热键释放
        if key_name != self._hotkey:
            return
        if not self._key_down:
            return
        self._key_down = False
        logger.info(f"[HotkeyManager] release: {self._hotkey} → stop recording")
        self.voice_stop.emit()

    @staticmethod
    def _key_name(key) -> str:
        try:
            return key.char.lower() if hasattr(key, 'char') and key.char else key.name.lower()
        except Exception:
            return ""
