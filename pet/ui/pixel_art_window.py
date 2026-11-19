"""像素画只读窗口 — 跟随每次落笔刷新，展示画布内容与 revision。

窗口不参与绘制：除关闭外不提供编辑操作，画面由工具侧的 payload 驱动。
"""

import logging

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from pet.tools.pixel_art import core as art_core
from pet.tools.pixel_art.canvas import PixelArtError

logger = logging.getLogger(__name__)

_CANVAS_SIZE = 256
_CORNER_RADIUS = 8
_BG_QSS = (
    "QWidget#pixelArtBg {"
    "  background: #f5f6fa;"
    "  border-radius: 12px;"
    "  font-size: 13px;"
    "}"
)
_CLOSE_QSS = (
    "QPushButton {"
    "  background: transparent; border: none; border-radius: 13px;"
    "  font-size: 15px; color: #999;"
    "}"
    "QPushButton:hover { background: #e81123; color: #fff; }"
)


class CanvasView(QWidget):
    """棋盘底上绘制当前画面，透明像素露出底纹。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self.setFixedSize(_CANVAS_SIZE, _CANVAS_SIZE)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.update()

    def clear(self) -> None:
        self._pixmap = None
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        full = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(full, _CORNER_RADIUS, _CORNER_RADIUS)
        painter.setClipPath(clip)
        cell = 8
        for y in range(0, _CANVAS_SIZE, cell):
            for x in range(0, _CANVAS_SIZE, cell):
                shade = (x // cell + y // cell) % 2
                painter.fillRect(x, y, cell, cell,
                                 QColor(0xE6, 0xE6, 0xE6) if shade else QColor(0xC9, 0xC9, 0xC9))
        if self._pixmap is not None:
            painter.drawPixmap(0, 0, self._pixmap)
        painter.setClipping(False)
        painter.setPen(QColor(0xD5, 0xD9, 0xE2))
        painter.drawRoundedRect(full.adjusted(0.5, 0.5, -0.5, -0.5),
                                _CORNER_RADIUS, _CORNER_RADIUS)


class PixelArtWindow(QWidget):
    """无边框置顶窗口，跟随 payload 刷新。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pet_window = None
        self._placed = False
        self._drag_pos: QPoint | None = None
        self._pending: dict | None = None
        self._user_closed = False
        self._canvas_id: str | None = None
        self._status_base = ""

        self.setObjectName("pixelArtWindow")
        self.setWindowTitle("像素画")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._merge_timer = QTimer(self)
        self._merge_timer.setSingleShot(True)
        self._merge_timer.setInterval(60)
        self._merge_timer.timeout.connect(self._apply)

        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.setInterval(3000)
        self._flash_timer.timeout.connect(self._restore_status)

        self._setup_ui()
        self.hide()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        bg = QWidget()
        bg.setObjectName("pixelArtBg")
        bg.setStyleSheet(_BG_QSS)
        layout = QVBoxLayout(bg)
        layout.setContentsMargins(16, 10, 16, 12)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel("像素画")
        title_font = self._title.font()
        title_font.setPixelSize(14)
        title_font.setBold(True)
        self._title.setFont(title_font)
        self._title.setStyleSheet("color: #333;")
        title_row.addWidget(self._title)
        title_row.addStretch()
        close = QPushButton("×")
        close.setFixedSize(26, 26)
        close.setStyleSheet(_CLOSE_QSS)
        close.clicked.connect(self._close_by_user)
        title_row.addWidget(close)
        layout.addLayout(title_row)

        self._canvas = CanvasView()
        layout.addWidget(self._canvas, alignment=Qt.AlignmentFlag.AlignHCenter)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        self._status = QLabel("等待画布")
        self._status.setStyleSheet("color: #666; font-size: 12px;")
        status_row.addWidget(self._status)
        status_row.addStretch()
        self._export_btn = QPushButton("导出 PNG")
        self._export_btn.setFixedHeight(24)
        self._export_btn.setStyleSheet(
            "QPushButton { background: #e8eaf0; border: none; border-radius: 6px;"
            "  padding: 0 10px; font-size: 12px; color: #444; }"
            "QPushButton:hover { background: #dfe3ec; }"
            "QPushButton:disabled { color: #aaa; }")
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export_png)
        status_row.addWidget(self._export_btn)
        layout.addLayout(status_row)

        root.addWidget(bg)

    def set_pet_window(self, window) -> None:
        self._pet_window = window

    def render(self, payload: dict) -> None:
        """接收工具线程推来的画面；60ms 内合并，避免连续算子反复重绘。"""
        if not isinstance(payload, dict):
            return
        self._pending = payload
        if not self._merge_timer.isActive():
            self._merge_timer.start()

    def _apply(self) -> None:
        payload = self._pending
        self._pending = None
        if not payload:
            return
        if payload.get("empty"):
            self._render_empty()
            self._show_if_needed(payload)
            return
        png = payload.get("png")
        if isinstance(png, (bytes, bytearray)):
            pixmap = QPixmap()
            if pixmap.loadFromData(bytes(png), "PNG"):
                self._canvas.set_pixmap(pixmap)
        name = payload.get("name") or "未命名"
        size = payload.get("size") or "?"
        revision = payload.get("revision", 0)
        palette_size = payload.get("palette_size", 0)
        preset = payload.get("preset") or f"{palette_size} 色"
        changed = payload.get("changed_cells", 0)
        self._canvas_id = payload.get("canvas_id")
        self._export_btn.setEnabled(bool(self._canvas_id))
        self._set_title(name)
        self._status_base = (
            f"{size}×{size} · {preset} · revision {revision} · 本次 {changed} 格")
        if not self._flash_timer.isActive():
            self._status.setText(self._status_base)
        self._show_if_needed(payload)

    def _set_title(self, name: str | None) -> None:
        """标题带画布名；超宽时省略，避免长名字撑宽窗口。"""
        text = "像素画" if not name else f"像素画 · {name}"
        metrics = QFontMetrics(self._title.font())
        self._title.setText(metrics.elidedText(text, Qt.TextElideMode.ElideRight, _CANVAS_SIZE))

    def _render_empty(self) -> None:
        """空态：清空画面并提示先建画布，导出按钮禁用。"""
        self._canvas_id = None
        self._canvas.clear()
        self._export_btn.setEnabled(False)
        self._set_title(None)
        self._status_base = "还没有画布"
        if not self._flash_timer.isActive():
            self._status.setText(self._status_base)

    def _show_if_needed(self, payload: dict) -> None:
        if payload.get("force_show"):
            self._user_closed = False
        if not self.isVisible() and not self._user_closed:
            self._place_near_pet()
            self.show()
            self.raise_()

    def _export_png(self) -> None:
        """按当前画布内容导出 PNG，参数固定，不经模型与执行器。"""
        if not self._canvas_id:
            return
        try:
            result = art_core.export(canvas_id=self._canvas_id, format="png")
        except PixelArtError as e:
            self._flash(f"导出失败：{e.message}")
            return
        except Exception as e:  # 无 UI 环境或磁盘异常都不该打断事件循环
            logger.warning("[pixel_art] window export failed", exc_info=True)
            self._flash(f"导出失败：{e}")
            return
        self._flash(f"已导出至 {result['data']['path']}")

    def _flash(self, text: str) -> None:
        self._status.setText(text)
        self._flash_timer.start()

    def _restore_status(self) -> None:
        if self._status_base:
            self._status.setText(self._status_base)

    def _close_by_user(self) -> None:
        """用户主动关窗：之后普通刷新不再自动弹出，直到 create / open。"""
        self._user_closed = True
        self.hide()

    def _place_near_pet(self) -> None:
        # 首次显示前布局还没生效，先用 sizeHint 定尺寸，否则按 640×480 判断会误判放不下
        self.adjustSize()
        pet = self._pet_window
        if pet is not None and pet.isVisible():
            geo = pet.geometry()
            x = geo.right() + 10
            y = geo.top()
            screen = pet.screen()
            if screen:
                avail = screen.availableGeometry()
                if x + self.width() > avail.right():
                    x = max(avail.left(), geo.left() - self.width() - 10)
                y = min(max(avail.top(), y), avail.bottom() - self.height())
            self.move(x, y)
        else:
            from PySide6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                self.move(geo.center() - self.rect().center())
        self._placed = True

    def mousePressEvent(self, event):  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()
            event.accept()

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt 命名
        if self._drag_pos is not None:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()
            event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt 命名
        self._drag_pos = None
        event.accept()
