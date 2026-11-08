"""命令确认卡片：队列展示、倒计时、允许与拒绝。"""

import logging

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import (QColor, QCursor, QGuiApplication, QIcon, QPainter,
                           QPainterPath, QPen)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from pet.terminal import core
from pet.ui.styles import (ICON_PATH, TERMINAL_ACCENT, TERMINAL_ACCENT_TEXT,
                           TERMINAL_BORDER, TERMINAL_CARD_RADIUS, TERMINAL_CODE_BG,
                           TERMINAL_CONTROL_RADIUS, TERMINAL_DANGER, TERMINAL_HOVER,
                           TERMINAL_MUTED, TERMINAL_PANEL, TERMINAL_TEXT, TERMINAL_USER,
                           terminal_mono_font, terminal_ui_font)

logger = logging.getLogger(__name__)

_GAP = 8
_MARGIN = 24
_AMBER_SECONDS = 10
# 卡片宽度上限，避免超长命令把窗口撑出屏幕
CARD_MAX_WIDTH = 560

ALLOW_TEXT = "允许"
ALLOW_DANGER_TEXT = "仍要执行"
DENY_TEXT = "拒绝"
DENY_ALL_TEXT = "全部拒绝"


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def card_position(pet_rect: QRect, screen_rect: QRect, card_size: QSize) -> QPoint:
    """卡片位置：优先恋恋左右侧面，其次下方，再次上方，恋恋不可见时放置在屏幕右下角。"""
    width = max(0, card_size.width())
    height = max(0, card_size.height())
    right = screen_rect.right() - width
    bottom = screen_rect.bottom() - height
    if not pet_rect.intersects(screen_rect):
        return QPoint(_clamp(screen_rect.right() - _MARGIN - width, screen_rect.left(), right),
                      _clamp(screen_rect.bottom() - _MARGIN - height, screen_rect.top(), bottom))

    left_room = pet_rect.left() - screen_rect.left() - _GAP
    right_room = screen_rect.right() - pet_rect.right() - _GAP
    if max(left_room, right_room) >= width:
        x = (pet_rect.left() - _GAP - width if left_room >= right_room
             else pet_rect.right() + 1 + _GAP)
        y = _clamp(pet_rect.center().y() - height // 2, screen_rect.top(), bottom)
        return QPoint(_clamp(x, screen_rect.left(), right), y)

    x = _clamp(pet_rect.center().x() - width // 2, screen_rect.left(), right)
    below = pet_rect.bottom() + 1 + _GAP
    if below <= bottom:
        return QPoint(x, below)
    above = pet_rect.top() - _GAP - height
    if above >= screen_rect.top():
        return QPoint(x, above)
    return QPoint(x, bottom)


class _CountdownBar(QWidget):
    """按钮行上方的 1px 倒计时线。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(2)
        self._progress = 1.0
        self._urgent = False

    def set_progress(self, progress: float, urgent: bool = False) -> None:
        self._progress = max(0.0, min(1.0, float(progress)))
        self._urgent = urgent
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(TERMINAL_BORDER))
        width = int(self.width() * self._progress)
        if width > 0:
            color = TERMINAL_USER if self._urgent else TERMINAL_ACCENT
            painter.fillRect(0, 0, width, self.height(), QColor(color))


class ConfirmCard(QWidget):
    """一次只显示一条确认请求的独立小窗。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._request = None
        self._next_fn = None
        self._reject_all_fn = None
        self._pet_rect_fn = None
        self._danger = False
        self._remaining = 0
        self._drag_offset: QPoint | None = None

        self.setWindowTitle("命令确认")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumWidth(420)
        self.setMaximumWidth(CARD_MAX_WIDTH)
        try:
            self.setWindowIcon(QIcon(ICON_PATH))
        except Exception:
            pass

        self._strip = QWidget()
        self._strip.setFixedHeight(2)

        self._title = QLabel("恋恋想执行一条命令")
        self._title.setFont(terminal_ui_font(12, bold=True))
        self._title.setStyleSheet(f"color:{TERMINAL_TEXT};")
        self._counter = QLabel("1/1")
        self._counter.setFont(terminal_mono_font(10))
        self._counter.setStyleSheet(f"color:{TERMINAL_MUTED};")
        self._seconds = QLabel("")
        self._seconds.setFont(terminal_mono_font(10))
        self._seconds.setStyleSheet(f"color:{TERMINAL_MUTED};")

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title_row.addWidget(self._title)
        title_row.addStretch()
        title_row.addWidget(self._counter)
        title_row.addWidget(self._seconds)

        self._command = QLabel()
        self._command.setWordWrap(True)
        self._command.setMaximumHeight(84)
        self._command.setFont(terminal_mono_font(12))
        self._command.setStyleSheet(
            f"background:{TERMINAL_CODE_BG}; color:{TERMINAL_TEXT};"
            f" border-radius:{TERMINAL_CONTROL_RADIUS}px; padding:12px;"
        )

        self._meta = QLabel()
        self._meta.setWordWrap(True)
        self._meta.setFont(terminal_ui_font(11))
        self._meta.setStyleSheet(f"color:{TERMINAL_MUTED};")

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setFont(terminal_ui_font(12))
        self._hint.setVisible(False)

        self._countdown = _CountdownBar()

        self._deny_all_btn = QPushButton(DENY_ALL_TEXT)
        self._deny_all_btn.setFlat(True)
        self._deny_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._deny_all_btn.setFont(terminal_ui_font(11))
        self._deny_all_btn.setStyleSheet(
            f"QPushButton {{ color:{TERMINAL_MUTED}; background:transparent; border:none; }}"
            f"QPushButton:hover {{ color:{TERMINAL_DANGER}; }}"
        )
        self._deny_all_btn.clicked.connect(self._deny_all)

        self._deny_btn = QPushButton(DENY_TEXT)
        self._deny_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._deny_btn.setFont(terminal_ui_font(12))
        self._deny_btn.setStyleSheet(
            f"QPushButton {{ color:{TERMINAL_TEXT}; background:transparent;"
            f" border:1px solid {TERMINAL_BORDER}; border-radius:{TERMINAL_CONTROL_RADIUS}px;"
            f" padding:6px 18px; }}"
            f"QPushButton:hover {{ background:{TERMINAL_HOVER}; }}"
        )
        self._deny_btn.clicked.connect(self._deny)

        self._allow_btn = QPushButton(ALLOW_TEXT)
        self._allow_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._allow_btn.setFont(terminal_ui_font(12, bold=True))
        self._allow_btn.clicked.connect(self._allow)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        button_row.addWidget(self._deny_all_btn)
        button_row.addStretch()
        button_row.addWidget(self._deny_btn)
        button_row.addWidget(self._allow_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 12, 18, 16)
        root.setSpacing(10)
        root.addWidget(self._strip)
        root.addLayout(title_row)
        root.addWidget(self._command)
        root.addWidget(self._meta)
        root.addWidget(self._hint)
        root.addWidget(self._countdown)
        root.addLayout(button_row)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

    def set_pet_rect_provider(self, provider) -> None:
        """注入恋恋窗口矩形提供者，用于定位。"""
        self._pet_rect_fn = provider

    def set_advance(self, next_fn) -> None:
        """注入「展示下一条」的回调。"""
        self._next_fn = next_fn

    def set_reject_all(self, reject_fn) -> None:
        """注入「全部拒绝」的回调。"""
        self._reject_all_fn = reject_fn

    def show_request(self, request) -> None:
        """展示一条请求；同一实例更新内容，不反复建窗。"""
        self._request = request
        self._remaining = max(1, int(request.timeout_s))
        self._danger = bool(request.matched_patterns)
        self._title.setText("恋恋想执行一条命令")
        self._counter.setText(f"{request.sequence}/{request.total}")
        self._command.setText(core.display_argv(request.routed or request.argv))
        self._meta.setText(self._meta_text(request))
        self._apply_hint(request)
        self._strip.setStyleSheet(
            f"background:{TERMINAL_DANGER if self._danger else TERMINAL_USER if request.untrusted else TERMINAL_ACCENT};")
        self._allow_btn.setText(ALLOW_DANGER_TEXT if self._danger else ALLOW_TEXT)
        self._allow_btn.setStyleSheet(self._allow_style(self._danger))
        self._seconds.setText(f"{self._remaining}s")
        self._countdown.set_progress(1.0, False)
        self._timer.start()
        self.show()
        self.raise_()
        self.activateWindow()
        self._reposition()

    def _meta_text(self, request) -> str:
        parts = ["恋恋" if request.source == "model" else "用户"]
        if request.cwd:
            parts.append(request.cwd)
        parts.append(f"超时 {int(request.timeout_s)}s")
        if request.builtin:
            parts.append(f"内建 {request.builtin}")
        if request.shell_mode:
            parts.append(f"整条交给 shell（{request.shell_mode}）")
        return " · ".join(parts)

    def _apply_hint(self, request) -> None:
        if request.matched_patterns:
            text, color = "命中危险规则：" + "、".join(request.matched_patterns), TERMINAL_DANGER
        elif request.untrusted:
            text, color = "程序来自可写目录", TERMINAL_USER
        elif getattr(request, "shell_mode", ""):
            text, color = "整条命令交给 shell，不做程序解析", TERMINAL_USER
        else:
            text, color = "", TERMINAL_MUTED
        self._hint.setText(text)
        self._hint.setStyleSheet(
            f"color:{TERMINAL_TEXT}; background:{TERMINAL_CODE_BG}; padding:8px;"
            f" border-left:3px solid {color}; border-radius:{TERMINAL_CONTROL_RADIUS}px;"
        )
        self._hint.setVisible(bool(text))

    @staticmethod
    def _allow_style(danger: bool) -> str:
        color = TERMINAL_DANGER if danger else TERMINAL_ACCENT
        return (
            f"QPushButton {{ color:{TERMINAL_ACCENT_TEXT}; background:{color}; border:none;"
            f" border-radius:{TERMINAL_CONTROL_RADIUS}px; padding:6px 18px; }}"
            f"QPushButton:hover {{ background:{TERMINAL_DANGER if not danger else '#e06a66'}; }}"
        )

    def _tick(self) -> None:
        if self._request is None:
            self._timer.stop()
            return
        self._remaining -= 1
        self._seconds.setText(f"{max(0, self._remaining)}s")
        total = max(1, int(self._request.timeout_s))
        urgent = self._remaining <= _AMBER_SECONDS
        self._countdown.set_progress(self._remaining / total, urgent)
        if self._remaining <= 0:
            self.finish(False)

    def _allow(self) -> None:
        if self._request is not None and self._danger:
            logger.info("[ConfirmCard] 危险命令被显式允许")
        self.finish(True)

    def _deny(self) -> None:
        self.finish(False)

    def _deny_all(self) -> None:
        self._timer.stop()
        request, self._request = self._request, None
        if request is not None:
            request.result = False
            request.event.set()
        self.hide()
        if self._reject_all_fn is not None:
            self._reject_all_fn()

    def finish(self, allowed: bool, advance: bool = True) -> None:
        """写回结果并关闭；advance 为真时推进队列。"""
        self._timer.stop()
        request, self._request = self._request, None
        if request is not None:
            request.result = bool(allowed)
            request.event.set()
        self.hide()
        if advance and self._next_fn is not None:
            self._next_fn()

    def mousePressEvent(self, event):
        """卡片任意非按钮区域都可拖动。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (event.globalPosition().toPoint()
                                 - self.frameGeometry().topLeft())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._deny()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not self._danger:
                self._allow()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self.finish(False)
        event.accept()

    def _reposition(self) -> None:
        """每条请求都按恋恋当前位置重算"""
        provider = self._pet_rect_fn
        try:
            pet_rect = provider() if provider is not None else QRect()
            screen = QGuiApplication.screenAt(pet_rect.center()) if pet_rect.isValid() else None
            if screen is None:
                screen = QGuiApplication.screenAt(QCursor.pos())
            if screen is None:
                screen = QGuiApplication.primaryScreen()
            if screen is None:
                return
            self.adjustSize()
            self.move(card_position(pet_rect, screen.availableGeometry(), self.size()))
        except Exception:
            logger.debug("[ConfirmCard] 定位失败，沿用系统位置", exc_info=True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, TERMINAL_CARD_RADIUS, TERMINAL_CARD_RADIUS)
        painter.fillPath(path, QColor(TERMINAL_PANEL))
        painter.setPen(QPen(QColor(TERMINAL_BORDER), 1))
        painter.drawPath(path)
