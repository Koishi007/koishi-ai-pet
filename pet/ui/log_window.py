"""INFO 级日志查看器 —— 托盘右键打开，扁平化圆角风格。"""

import logging
from collections import deque

from PySide6.QtCore import Qt, Signal, QObject, QPoint
from PySide6.QtGui import QFont, QTextCursor, QIcon, QPainter, QPainterPath, QColor, QPen
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QLabel, QComboBox,
)
from pet.ui.styles import (
    ICON_PATH, TEXTEDIT_QSS, BUTTON_QSS, BUTTON_DANGER_QSS, COMBOBOX_QSS,
)


# ── 常量 ──

_RADIUS = 10  # 窗口圆角半径


# ── 跨线程日志桥接 ──

class _LogRelay(QObject):
    """跨线程日志桥接器 — 接收 LogWindowHandler 的 Signal 并交付给 LogWindow。"""

    log_received = Signal(str)

    _LEVELS = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }

    def __init__(self, buffer_size: int = 2000, parent=None):
        super().__init__(parent)
        self._widget: QWidget | None = None
        self._handler: LogWindowHandler | None = None
        self._buffer: deque[str] = deque(maxlen=buffer_size)
        self.log_received.connect(self._on_log_received)

    def set_widget(self, widget: QWidget):
        self._widget = widget
        while self._buffer:
            widget._append_log(self._buffer.popleft())

    def set_handler(self, handler: "LogWindowHandler"):
        self._handler = handler

    def set_level(self, level_name: str):
        """热切换日志级别 (DEBUG/INFO/WARNING/ERROR)。"""
        level = self._LEVELS.get(level_name)
        if level is not None and self._handler:
            self._handler.setLevel(level)

    def current_level_name(self) -> str:
        if self._handler:
            for name, val in self._LEVELS.items():
                if val == self._handler.level:
                    return name
        return "INFO"

    def _on_log_received(self, formatted: str):
        if self._widget:
            self._widget._append_log(formatted)
        else:
            self._buffer.append(formatted)


# ── 自定义 Handler ──

class LogWindowHandler(logging.Handler):
    """自定义 logging.Handler — 仅 INFO 及以上，格式化后经由 _LogRelay 进入 GUI。"""

    def __init__(self, relay: _LogRelay, level=logging.INFO):
        super().__init__(level=level)
        self._relay = relay
        self.setFormatter(logging.Formatter(
            "[%(asctime)s] [%(levelname)-5s] [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        ))

    def emit(self, record: logging.LogRecord):
        try:
            self._relay.log_received.emit(self.format(record))
        except Exception:
            self.handleError(record)


# ── QSS ──

_WINDOW_QSS = """
QWidget#LogWindowRoot {
    background: transparent;
}
"""

_HEADER_QSS = """
QWidget#LogHeader {
    background: transparent;
}
"""

_MAX_BLOCK_COUNT = 5000


# ── LogWindow ──

class LogWindow(QWidget):
    """INFO 日志查看窗口 — 无边框扁平化 + Win11 圆角。"""

    def __init__(self, relay: _LogRelay, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DeskPet 日志")
        self.setObjectName("LogWindowRoot")
        self.setMinimumSize(520, 350)
        self.resize(620, 440)

        # 无边框 + 透明背景（用于 paintEvent 画圆角）
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.setStyleSheet(_WINDOW_QSS)

        # 窗口图标 (与托盘一致)
        try:
            self.setWindowIcon(QIcon(ICON_PATH))
        except Exception:
            pass

        # ── 自定义标题栏 ──
        header = QWidget()
        header.setObjectName("LogHeader")
        header.setFixedHeight(34)
        header.setStyleSheet(_HEADER_QSS)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 6, 0)
        header_layout.setSpacing(6)

        # 图标
        icon_label = QLabel()
        try:
            icon_label.setPixmap(QIcon(ICON_PATH).pixmap(18, 18))
        except Exception:
            pass
        header_layout.addWidget(icon_label)

        # 标题
        title_label = QLabel("DeskPet 日志")
        title_label.setStyleSheet("font-size:13px; color:#444; font-weight:bold; background:transparent;")
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        # 关闭按钮
        close_btn = QPushButton("✕")
        close_btn.setObjectName("LogCloseBtn")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet(BUTTON_DANGER_QSS)
        close_btn.clicked.connect(self.hide)
        header_layout.addWidget(close_btn)

        # ── 工具栏 ──
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)

        # 日志级别切换
        self._level_combo = QComboBox()
        self._level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self._level_combo.setCurrentText(relay.current_level_name())
        self._level_combo.setStyleSheet(COMBOBOX_QSS)
        self._level_combo.currentTextChanged.connect(relay.set_level)
        toolbar.addWidget(self._level_combo)

        toolbar.addStretch()

        clear_btn = QPushButton("清空")
        clear_btn.setStyleSheet(BUTTON_QSS)
        clear_btn.clicked.connect(self._clear)
        toolbar.addWidget(clear_btn)

        # ── 日志正文 ──
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setUndoRedoEnabled(False)  # 防止 undo stack 随 trim 无限增长
        self._log_view.setFont(QFont("Consolas", 10))
        self._log_view.setStyleSheet(TEXTEDIT_QSS)

        # ── 组装 ──
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 8)
        root.setSpacing(6)
        root.addWidget(header)
        root.addLayout(toolbar)
        root.addWidget(self._log_view)

        # ── 拖拽支持 ──
        header.mousePressEvent = self._header_press
        header.mouseMoveEvent = self._header_move
        self._drag_pos: QPoint | None = None

        # 绑定 relay
        relay.set_widget(self)

    # ── 窗口圆角绘制 ──

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, _RADIUS, _RADIUS)
        # 填充背景
        painter.fillPath(path, QColor("#f0f0f0"))
        # 细描边
        painter.setPen(QPen(QColor("#cccccc"), 1))
        painter.drawPath(path)

    # ── 标题栏拖拽 ──

    def _header_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()

    def _header_move(self, event):
        if self._drag_pos is not None:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()

    # ── 公开 ──

    def _append_log(self, formatted: str):
        """由 _LogRelay 调用（主线程安全）。"""
        self._log_view.append(formatted)
        self._trim_if_needed()

    def _clear(self):
        self._log_view.clear()

    # ── 内部 ──

    def _trim_if_needed(self):
        doc = self._log_view.document()
        total = doc.blockCount()
        if total <= _MAX_BLOCK_COUNT:
            return
        excess = total - _MAX_BLOCK_COUNT
        # 一次性选中所有超标块并删除（比逐行删快 100 倍）
        cursor = QTextCursor(doc.firstBlock())
        cursor.movePosition(
            QTextCursor.MoveOperation.NextBlock,
            QTextCursor.MoveMode.KeepAnchor, excess - 1
        )
        cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
        cursor.removeSelectedText()
        cursor.deleteChar()  # 删除残留换行

    def closeEvent(self, event):
        """X 按钮 → 隐藏保留历史；程序退出 → 正常关闭。"""
        if event.spontaneous():
            self.hide()
            event.ignore()
        else:
            event.accept()
