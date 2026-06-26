"""桌宠情绪气泡显示 emoji 表情"""

from PySide6.QtWidgets import QLabel, QWidget
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from pet.config import config

# 情绪名 → emoji 映射
EMOTION_MAP = {
    "happy":      "\U0001f60a",   # 😊
    "excited":    "\U0001f929",   # 🤩
    "sad":        "\U0001f622",   # 😢
    "angry":      "\U0001f620",   # 😠
    "surprised":  "\U0001f632",   # 😲
    "thinking":   "\U0001f914",   # 🤔
    "sleepy":     "\U0001f62a",   # 😪
    "love":       "\u2764\ufe0f", # ❤️
    "cool":       "\U0001f60e",   # 😎
    "shy":        "\U0001f633",   # 😳
    "scared":     "\U0001f631",   # 😱
    "hungry":     "\U0001f35c",   # 🍜
    "curious":    "\U0001f9d0",   # 🧐
    "proud":      "\U0001f607",   # 😇
    "bored":      "\U0001f634",   # 😴
    "crazy":      "\U0001f92a",   # 🤪
}

VALID_EMOTIONS = set(EMOTION_MAP.keys())


def emotion_to_emoji(name: str) -> str:
    """将情绪名映射为 emoji；未知名直接返回原文本。"""
    return EMOTION_MAP.get(name.lower().strip(), name)


class EmotionBubble(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(
            "background: transparent;"
            "font-size: 32px;"
        )
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(48, 48)
        self.setContentsMargins(2, 2, 2, 4)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide_bubble)

        self._follow_timer = QTimer(self)
        self._follow_timer.timeout.connect(self._follow_pet)

        self._fade_anim: QPropertyAnimation | None = None
        self.hide()

    def show_emotion(self, emotion: str, duration: int = 3000, parent_pos=None):
        """显示情绪 emoji。emotion 可为情绪名或 emoji 文本。"""
        emoji = emotion_to_emoji(emotion)
        self.setText(emoji)

        if parent_pos is None and isinstance(self.parent(), QWidget):
            parent_pos = self.parent().geometry().center()

        if parent_pos is not None:
            x, y = self._final_position(parent_pos)
            self.move(x, y)
            # 淡入
            self.setWindowOpacity(0.0)
            self.show()
            self._fade_anim = QPropertyAnimation(self, b"windowOpacity")
            self._fade_anim.setDuration(200)
            self._fade_anim.setStartValue(0.0)
            self._fade_anim.setEndValue(0.95)
            self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._fade_anim.start()
        else:
            self.show()

        self._follow_timer.start(50)
        self._hide_timer.start(duration)

    def _hide_bubble(self):
        # 淡出
        self._follow_timer.stop()
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity")
        self._fade_anim.setDuration(300)
        self._fade_anim.setStartValue(self.windowOpacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade_anim.finished.connect(self._do_hide)
        self._fade_anim.start()

    def _do_hide(self):
        self._fade_anim = None
        super().hide()

    def hide(self):
        self._follow_timer.stop()
        if self._fade_anim:
            self._fade_anim.stop()
            self._fade_anim = None
        super().hide()

    def _final_position(self, target_pos) -> tuple:
        """定位在宠物右上角，避免与 speech 气泡（上方居中）重叠。"""
        pet = self.parent()
        if isinstance(pet, QWidget):
            # 右上角：pet 右边缘靠近，pet 上边缘对齐
            x = pet.x() + config.PET_WIDTH - 16
            y = pet.y() - 8
        else:
            x = target_pos.x() + config.PET_WIDTH // 2 - 16
            y = target_pos.y() - config.PET_HEIGHT // 2 - 8
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = max(geo.left(), min(x, geo.right() - self.width()))
            y = max(geo.top(), min(y, geo.bottom() - self.height()))
        return (x, y)

    def _follow_pet(self):
        pet = self.parent()
        if isinstance(pet, QWidget) and self.isVisible():
            target = pet.geometry().center()
            x, y = self._final_position(target)
            self.move(x, y)
