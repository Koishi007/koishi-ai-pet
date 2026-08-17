"""觅食的食物悬浮窗 — 纯展示组件，生命周期由 FoodManager 管理。"""

import logging
import random

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QWidget

from pet.config import config

logger = logging.getLogger(__name__)

FOOD_EMOJIS = ["🍰", "🍙", "🍎", "🍜", "🍗", "🍩", "🍕", "🍓", "🥟", "🍣"]
FOOD_NAMES = {
    "🍰": "蛋糕", "🍙": "饭团", "🍎": "苹果", "🍜": "拉面", "🍗": "鸡腿",
    "🍩": "甜甜圈", "🍕": "披萨", "🍓": "草莓", "🥟": "饺子", "🍣": "寿司",
}

# 食物窗口尺寸（px）
FOOD_SIZE = 64


class FoodWindow(QWidget):
    """无边框置顶的 emoji 食物悬浮窗，鼠标穿透，带淡入与浮动动画。

    不包含任何行为逻辑：只负责展示，出现/消失由 FoodManager 驱动。
    """

    def __init__(self, emoji: str, x: int, y: int, parent=None):
        super().__init__(parent)
        self._emoji = emoji
        self.setFixedSize(FOOD_SIZE, FOOD_SIZE)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setFixedSize(FOOD_SIZE, FOOD_SIZE)
        self._label.setText(emoji)
        font = self._label.font()
        font.setPointSize(36)
        self._label.setFont(font)

        self.move(x, y)

        # 淡入
        self.setWindowOpacity(0.0)
        self.show()
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity")
        self._fade_anim.setDuration(300)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim.start()

        # 轻微上下浮动（呼吸感）
        self._float_timer = QTimer(self)
        self._float_timer.setInterval(1200)
        self._float_timer.timeout.connect(self._float_step)
        self._float_timer.start()
        self._float_up = False

        logger.info(f"[FoodWindow] spawned {emoji} at ({x}, {y})")

    def _float_step(self):
        """每 1.2s 在 ±6px 内上下浮动一次。"""
        dy = -6 if self._float_up else 6
        self._float_up = not self._float_up
        self.move(self.x(), self.y() + dy)

    def disappear(self, on_finished=None):
        """缩放 + 淡出动画，结束后销毁窗口（可回调）。"""
        self._float_timer.stop()
        self._fade_anim.stop()

        # 动画必须持有引用，否则局部变量被 GC 后淡出中断、窗口泄漏
        self._disappear_anim = QPropertyAnimation(self, b"windowOpacity")
        self._disappear_anim.setDuration(200)
        self._disappear_anim.setStartValue(self.windowOpacity())
        self._disappear_anim.setEndValue(0.0)
        self._disappear_anim.setEasingCurve(QEasingCurve.Type.InCubic)

        def _finish():
            self.deleteLater()
            if on_finished:
                on_finished()

        self._disappear_anim.finished.connect(_finish)
        self._disappear_anim.start()

    @staticmethod
    def pick_emoji(food_type: str | None = None) -> str:
        """按模型指定的食物类型选 emoji；未指定或未知则随机。"""
        if food_type:
            for emoji, name in FOOD_NAMES.items():
                if name == food_type:
                    return emoji
        return random.choice(list(FOOD_EMOJIS))

    @staticmethod
    def name_of(emoji: str) -> str:
        """emoji 对应的中文食物名，未知时返回「食物」。"""
        return FOOD_NAMES.get(emoji, "食物")
