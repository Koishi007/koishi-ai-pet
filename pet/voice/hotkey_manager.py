"""全局热键管理器，使用 pynput 监听按键按下/释放。

当前仅支持单键热键（如 F8、Scroll_Lock 等），
组合键（Ctrl+Shift+V 等）需要额外处理 key up 。
"""

import logging

from pynput import keyboard
from PySide6.QtCore import QObject, Signal

from config import config

logger = logging.getLogger(__name__)


class HotkeyManager(QObject):
    """全局热键监听。

    按下热键 → voice_start 信号
    松开热键 → voice_stop 信号
    """

    voice_start = Signal()
    voice_stop = Signal()
    error_occurred = Signal(str)

    _MODIFIERS = {"ctrl_l", "ctrl_r", "alt_l", "alt_r", "shift_l", "shift_r", "cmd_l", "cmd_r"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._listener: keyboard.Listener | None = None
        self._hotkey = config.VOICE_HOTKEY.lower()
        self._pressed = False
        self._is_modifier = self._hotkey in self._MODIFIERS

    def start(self):
        """启动全局键盘监听线程。"""
        if self._listener and self._listener.running:
            return
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()
        logger.info(f"[HotkeyManager] listening for '{self._hotkey}'")

    def stop(self):
        """停止键盘监听。"""
        if self._listener and self._listener.running:
            self._listener.stop()
            self._listener = None
        logger.info("[HotkeyManager] stopped")

    def _on_press(self, key):
        try:
            key_name = key.char.lower() if hasattr(key, 'char') and key.char else key.name.lower()
        except Exception:
            return

        if key_name != self._hotkey:
            return

        if not self._pressed:
            self._pressed = True
            logger.info(f"[HotkeyManager] hotkey pressed: {self._hotkey}")
            self.voice_start.emit()

    def _on_release(self, key):
        try:
            key_name = key.char.lower() if hasattr(key, 'char') and key.char else key.name.lower()
        except Exception:
            return

        if key_name != self._hotkey:
            return

        if self._pressed:
            self._pressed = False
            logger.info(f"[HotkeyManager] hotkey released: {self._hotkey}")
            self.voice_stop.emit()
