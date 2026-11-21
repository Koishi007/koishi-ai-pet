"""像素画只读面板：跟随每次落笔刷新，展示画布内容与 revision。

面板不参与绘制：除关闭外不提供编辑操作，画面由工具侧的 payload 驱动。
"""

import logging

from PySide6.QtCore import (QObject, QPoint, QRectF, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from pet.tools.context import TOOL_CTX
from pet.tools.pixel_art import core as art_core
from pet.tools.pixel_art.canvas import PixelArtError
from pet.tools.pixel_art.style import (BASE_QSS, BUTTON_QSS, CHECKER_DARK,
                                       CHECKER_LIGHT, CLOSE_QSS, PANEL_QSS)
from pet import theme

logger = logging.getLogger(__name__)

_CANVAS_SIZE = 256
_CORNER_RADIUS = theme.RADIUS_SM


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
                                 QColor(CHECKER_LIGHT) if shade else QColor(CHECKER_DARK))
        if self._pixmap is not None:
            painter.drawPixmap(0, 0, self._pixmap)
        painter.setClipping(False)
        painter.setPen(QColor(theme.LINE))
        painter.drawRoundedRect(full.adjusted(0.5, 0.5, -0.5, -0.5),
                                _CORNER_RADIUS, _CORNER_RADIUS)


class PixelArtPanel(QWidget):
    """无边框置顶面板，跟随 payload 刷新。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._placed = False
        self._drag_pos: QPoint | None = None
        self._pending: dict | None = None
        self._user_closed = False
        self._canvas_id: str | None = None
        self._revision: int | None = None
        self._preset: str | None = None
        self._last_error_code: str | None = None
        self._status_base = ""
        self._status_budget = 0  # 状态行的常规文本宽度，提示文案按它省略

        self.setObjectName("pixelArtPanel")
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
        self.setStyleSheet(BASE_QSS + PANEL_QSS)
        self.hide()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bg = QWidget()
        bg.setObjectName("pixelArtBg")
        bg.setStyleSheet(PANEL_QSS)
        layout = QVBoxLayout(bg)
        layout.setContentsMargins(16, 8, 16, 12)
        layout.setSpacing(8)
        root.addWidget(bg)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel("像素画")
        title_font = self._title.font()
        title_font.setPixelSize(14)
        title_font.setBold(True)
        self._title.setFont(title_font)
        self._title.setStyleSheet(f"color: {theme.INK};")
        title_row.addWidget(self._title)
        title_row.addStretch()
        close = QPushButton("✕")
        close.setFixedSize(34, 34)
        close.setStyleSheet(CLOSE_QSS)
        close.clicked.connect(self._close_by_user)
        title_row.addWidget(close)
        layout.addLayout(title_row)

        self._canvas = CanvasView()
        layout.addWidget(self._canvas, alignment=Qt.AlignmentFlag.AlignHCenter)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        self._status = QLabel("等待画布")
        status_font = self._status.font()
        status_font.setPixelSize(12)
        self._status.setFont(status_font)
        self._status.setStyleSheet(f"color: {theme.INK_MUTED};")
        status_row.addWidget(self._status)
        status_row.addStretch()
        self._export_btn = QPushButton("导出 PNG")
        self._export_btn.setFixedHeight(26)
        self._export_btn.setStyleSheet(BUTTON_QSS)
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export_png)
        status_row.addWidget(self._export_btn)
        layout.addLayout(status_row)

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
        self._revision = payload.get("revision")
        self._preset = preset
        self._export_btn.setEnabled(bool(self._canvas_id))
        self._set_title(name)
        self._status_base = (
            f"{size}×{size} · {preset} · revision {revision} · 本次 {changed} 格")
        if not self._flash_timer.isActive():
            self._set_status(self._status_base)
        self._show_if_needed(payload)

    def _set_title(self, name: str | None) -> None:
        """标题带画布名；超宽时省略，避免长名字撑宽窗口。"""
        text = "像素画" if not name else f"像素画 · {name}"
        metrics = QFontMetrics(self._title.font())
        self._title.setText(metrics.elidedText(text, Qt.TextElideMode.ElideRight, _CANVAS_SIZE))

    def _render_empty(self) -> None:
        """空态：清空画面并提示先建画布，导出按钮禁用。"""
        self._canvas_id = None
        self._revision = None
        self._preset = None
        self._canvas.clear()
        self._export_btn.setEnabled(False)
        self._set_title(None)
        self._status_base = "还没有画布"
        if not self._flash_timer.isActive():
            self._set_status(self._status_base)

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
            self._last_error_code = e.code
            self._flash(f"导出失败：{e.message}")
            return
        except Exception as e:  # 异常只落状态行，不抛出事件循环
            self._last_error_code = type(e).__name__
            logger.warning("[pixel_art] panel export failed", exc_info=True)
            self._flash(f"导出失败：{e}")
            return
        self._last_error_code = None
        self._flash(f"已导出至 {result['data']['path']}")

    def _set_status(self, text: str) -> None:
        """常规状态：记下文本宽度，提示文案按它省略，不撑宽窗口。"""
        self._status.setText(text)
        self._status_budget = self._status.sizeHint().width()

    def _flash(self, text: str) -> None:
        """短暂提示：超宽部分中间省略，完整内容放 tooltip。"""
        budget = self._status_budget or self._status.sizeHint().width()
        metrics = QFontMetrics(self._status.font())
        self._status.setText(
            metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, budget))
        self._status.setToolTip(text)
        self._flash_timer.start()

    def _restore_status(self) -> None:
        self._status.setToolTip("")
        if self._status_base:
            self._set_status(self._status_base)

    def _close_by_user(self) -> None:
        """用户主动关面板：之后普通刷新不再自动弹出，直到 create / open。"""
        self._user_closed = True
        self.hide()

    def _place_near_pet(self) -> None:
        # 首次显示前布局未生效，先按 sizeHint 定尺寸再做边界判断
        self.adjustSize()
        pet = TOOL_CTX.pet_geometry()
        rect, avail = pet.get("rect") or {}, pet.get("available") or {}
        if pet.get("visible") and rect:
            right = rect["x"] + rect["width"] - 1  # QRect.right() 含端点
            x = right + 10
            y = rect["y"]
            if avail:
                if x + self.width() > avail["x"] + avail["width"] - 1:
                    x = max(avail["x"], rect["x"] - self.width() - 10)
                y = min(max(avail["y"], y),
                        avail["y"] + avail["height"] - 1 - self.height())
            self.move(x, y)
        else:
            screen = QApplication.primaryScreen()
            if screen is not None:
                avail = screen.availableGeometry()
                self.move(avail.center() - self.rect().center())
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


class _ViewBridge(QObject):
    """工具私有的跨线程桥：写入画面从工具线程排到主线程再交给面板。"""

    payload = Signal(object)

    def __init__(self):
        super().__init__()
        self.payload.connect(self._on_payload, Qt.ConnectionType.QueuedConnection)

    def _on_payload(self, payload: dict) -> None:
        _deliver(payload)


# 面板单例引用：模块级持有防 GC；关闭只隐藏，实例复用
_current: "PixelArtPanel | None" = None
_bridge: _ViewBridge | None = None


def _deliver(payload: dict) -> None:
    """主线程：面板按需创建，画面交给它渲染。"""
    global _current
    if _current is None:
        _current = PixelArtPanel()
    _current.render(payload)


def push_view(payload: dict) -> None:
    """任意线程调用；无 QApplication 的环境静默，纯逻辑测试可直接跑 handler。"""
    global _bridge
    app = QApplication.instance()
    if app is None:
        return
    if _bridge is None:
        _bridge = _ViewBridge()
        _bridge.moveToThread(app.thread())
    _bridge.payload.emit(payload)
