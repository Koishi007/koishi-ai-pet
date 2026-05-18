from PySide6.QtWidgets import QLabel, QWidget
from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QPainter, QColor, QPen, QPolygon
from config import config

TAIL_HEIGHT = 10


class SpeechBubble(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(
            "padding: 10px;"
            f"font-size: {config.BUBBLE_FONT_SIZE}px;"
            "color: #333;"
        )
        self.setWordWrap(True)
        self.setMaximumWidth(config.BUBBLE_MAX_WIDTH)
        self.setMinimumWidth(80)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self._follow_timer = QTimer(self)
        self._follow_timer.timeout.connect(self._follow_pet)
        self.hide()

    # ── 绘制 ──
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        body_h = h - TAIL_HEIGHT

        # 气泡主体：圆角矩形
        body_rect = self.rect()
        body_rect.setHeight(body_h)
        painter.setBrush(QColor(255, 255, 255, 220))
        painter.setPen(QPen(QColor(200, 200, 200), 1))
        painter.drawRoundedRect(body_rect.adjusted(0, 0, -1, -1), 12, 12)

        # 尾巴：指向下方的三角形
        cx = w // 2
        tail_top = body_h - 1  # 略微重叠避免缝隙
        tail = QPolygon([
            QPoint(cx - 6, tail_top),
            QPoint(cx + 6, tail_top),
            QPoint(cx,     h - 1),
        ])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 220))
        painter.drawPolygon(tail)

        painter.end()
        super().paintEvent(event)

    # ── 显示 ──
    def show_text(self, text, duration=5000, parent_pos=None):
        self.setText(text)
        self.adjustSize()
        # 预留尾巴高度
        self.resize(self.width(), self.height() + TAIL_HEIGHT)

        if parent_pos is not None:
            self._position_above(parent_pos)
        self.show()
        self._follow_timer.start(50)
        self._hide_timer.start(duration)

    def hide(self):
        self._follow_timer.stop()
        super().hide()

    # ── 定位 ──
    def _position_above(self, target_pos):
        bubble_x = target_pos.x() - self.width() // 2
        bubble_y = target_pos.y() - self.height() - 15
        self.move(max(0, bubble_x), max(0, bubble_y))

    def _follow_pet(self):
        pet = self.parent()
        if isinstance(pet, QWidget) and self.isVisible():
            target = pet.geometry().center()
            self._position_above(target)
