"""全局热键管理器，使用 pynput 监听按键。

当前仅支持单键热键（如 F8），按下切换录音开/关。
"""

import logging

from pynput import keyboard
from PySide6.QtCore import QObject, Signal

from config import config

logger = logging.getLogger(__name__)


class HotkeyManager(QObject):
    """全局热键监听。

    每次按键 → 切换录音状态，根据当前状态发出 voice_start 或 voice_stop。
    """

    voice_start = Signal()
    voice_stop = Signal()
    error_occurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._listener: keyboard.Listener | None = None
        self._hotkey = config.VOICE_HOTKEY.lower()
        self._active = False  # True = 正在录音中

    def reset(self):
        """重置切换状态（当录音非正常结束时调用）。"""
        self._active = False

    def start(self):
        """启动全局键盘监听线程。"""
        if self._listener and self._listener.running:
            return
        self._listener = keyboard.Listener(on_press=self._on_press)
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

        self._active = not self._active
        if self._active:
            logger.info(f"[HotkeyManager] toggle ON: {self._hotkey}")
            self.voice_start.emit()
        else:
            logger.info(f"[HotkeyManager] toggle OFF: {self._hotkey}")
            self.voice_stop.emit()
