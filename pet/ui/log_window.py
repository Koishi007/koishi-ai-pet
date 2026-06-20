"""INFO 级日志查看器 —— 托盘右键打开，扁平化圆角风格。"""

import ctypes
import logging
import sys
from collections import deque

from PySide6.QtCore import Qt, Signal, QObject, QPoint
from PySide6.QtGui import QFont, QTextCursor, QIcon
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QLabel,
)


# ── Win11 圆角窗口 ──

def _apply_win11_rounded(hwnd: int):
    """启用 Windows 11 原生圆角 (DWMWA_WINDOW_CORNER_PREFERENCE = 33)。"""
    if sys.platform != "win32":
        return
    try:
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_ROUND = 2
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(ctypes.c_int(DWMWCP_ROUND)),
            ctypes.sizeof(ctypes.c_int),
        )
    except Exception:
        pass


# ── 跨线程日志桥接 ──

class _LogRelay(QObject):
    """跨线程日志桥接器 — 接收 LogWindowHandler 的 Signal 并交付给 LogWindow。"""

    log_received = Signal(str)

    def __init__(self, buffer_size: int = 2000, parent=None):
        super().__init__(parent)
        self._widget: QWidget | None = None
        self._buffer: deque[str] = deque(maxlen=buffer_size)
        self.log_received.connect(self._on_log_received)

    def set_widget(self, widget: QWidget):
        self._widget = widget
        while self._buffer:
            widget._append_log(self._buffer.popleft())

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
    background: #f0f0f0;
    border-radius: 10px;
}
"""

_HEADER_QSS = """
QWidget#LogHeader {
    background: #f0f0f0;
}
"""

_TEXTEDIT_QSS = """
QTextEdit {
    background: #ffffff;
    border: 1px solid #ddd;
    border-radius: 8px;
    padding: 6px 8px;
    font-family: "Consolas", "Microsoft YaHei", monospace;
    font-size: 12px;
    color: #333;
    selection-background-color: #b3d9ff;
}
QTextEdit:focus {
    border-color: #aaa;
}
"""

_CLOSE_BTN_QSS = """
QPushButton#LogCloseBtn {
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 14px;
    color: #888;
}
QPushButton#LogCloseBtn:hover {
    background: #e81123;
    color: #fff;
}
"""

_CLEAR_BTN_QSS = """
QPushButton#LogClearBtn {
    background: transparent;
    border: 1px solid #ccc;
    border-radius: 10px;
    padding: 2px 12px;
    font-size: 12px;
    color: #555;
}
QPushButton#LogClearBtn:hover {
    background: #e0e0e0;
    border-color: #aaa;
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

        # 无边框
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        self.setStyleSheet(_WINDOW_QSS)

        # 窗口图标 (与托盘一致)
        try:
            self.setWindowIcon(QIcon("assets/icon/sys_tray.png"))
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
            icon_label.setPixmap(QIcon("assets/icon/sys_tray.png").pixmap(18, 18))
        except Exception:
            pass
        header_layout.addWidget(icon_label)

        # 标题
        title_label = QLabel("DeskPet 日志")
        title_label.setStyleSheet("font-size:13px; color:#444; font-weight:bold; background:transparent;")
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        # 日志级别标签
        level_badge = QLabel("INFO+")
        level_badge.setStyleSheet(
            "font-size:11px; color:#888; background:transparent;"
            "border:1px solid #ccc; border-radius:8px; padding:1px 8px;"
        )
        header_layout.addWidget(level_badge)

        header_layout.addSpacing(8)

        # 关闭按钮
        close_btn = QPushButton("✕")
        close_btn.setObjectName("LogCloseBtn")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet(_CLOSE_BTN_QSS)
        close_btn.clicked.connect(self.hide)
        header_layout.addWidget(close_btn)

        # ── 工具栏 ──
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)

        clear_btn = QPushButton("清空")
        clear_btn.setObjectName("LogClearBtn")
        clear_btn.setStyleSheet(_CLEAR_BTN_QSS)
        clear_btn.clicked.connect(self._clear)
        toolbar.addStretch()
        toolbar.addWidget(clear_btn)

        # ── 日志正文 ──
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setFont(QFont("Consolas", 10))
        self._log_view.setStyleSheet(_TEXTEDIT_QSS)

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

    # ── 窗口圆角 ──

    def showEvent(self, event):
        super().showEvent(event)
        _apply_win11_rounded(int(self.winId()))

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
        if doc.blockCount() <= _MAX_BLOCK_COUNT:
            return
        excess = doc.blockCount() - _MAX_BLOCK_COUNT
        cursor = QTextCursor(doc.firstBlock())
        for _ in range(excess):
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    def closeEvent(self, event):
        """关闭即隐藏，保留日志历史。"""
        self.hide()
        event.ignore()
