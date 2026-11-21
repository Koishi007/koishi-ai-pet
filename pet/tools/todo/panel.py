"""Todo 管理面板"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QMessageBox, QMenu, QApplication,
    QDialog, QLineEdit, QFormLayout, QDialogButtonBox,
)
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from pet.tools.todo.core import TodoListTool
from pet.tools.todo.style import (BASE_QSS, BUTTON_QSS, CLOSE_QSS, LIST_QSS,
                                  MENU_QSS, PANEL_QSS)
from pet import theme

logger = logging.getLogger(__name__)

_W = 420
_H = 520

# 面板单例引用（防 GC 回收；已关闭的实例在下次 show_panel 时回收）
_current: "TodoPanel | None" = None


class TodoPanel(QWidget):
    """任务管理面板 — 无边框圆角窗口，标题栏可拖动。"""

    def __init__(self, core: TodoListTool):
        super().__init__()
        self._core = core
        self._drag_pos: QPoint | None = None

        self.setObjectName("todoPanel")
        self.setWindowTitle("待办事项")
        self.resize(_W, _H)
        self.setFixedSize(_W, _H)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._setup_ui()
        self.setStyleSheet(BASE_QSS + PANEL_QSS)
        self._refresh()

    @staticmethod
    def _status_icon(done: bool) -> QIcon:
        """圆形状态标记：未完成为空心圆，完成为黄绿底加对勾。"""
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(theme.INK), theme.BORDER))
        painter.setBrush(QColor(theme.ACCENT if done else theme.SURFACE))
        painter.drawEllipse(1, 1, 15, 15)
        if done:
            painter.setPen(QPen(QColor(theme.INK), 2))
            painter.drawPolyline([
                QPoint(5, 9), QPoint(8, 12), QPoint(13, 6),
            ])
        painter.end()
        return QIcon(pixmap)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bg = QWidget()
        bg.setObjectName("todoBg")
        bg.setStyleSheet(PANEL_QSS)
        layout = QVBoxLayout(bg)
        layout.setContentsMargins(14, 4, 14, 12)
        layout.setSpacing(8)
        root.addWidget(bg)

        title_bar = QWidget()
        title_bar.setFixedHeight(theme.TITLE_BAR_H)
        title_row = QHBoxLayout(title_bar)
        title_row.setContentsMargins(2, 0, 0, 0)

        title = QLabel("待办事项")
        title.setStyleSheet(
            f"font-size: 14px; font-weight: 800; color: {theme.INK};"
        )
        title_row.addWidget(title)
        title_row.addStretch()
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(34, 34)
        btn_close.setStyleSheet(CLOSE_QSS)
        btn_close.clicked.connect(self.close)
        title_row.addWidget(btn_close)

        layout.addWidget(title_bar)

        rule_row = QHBoxLayout()
        rule_row.setContentsMargins(12, 0, 12, 0)
        rule = QWidget()
        rule.setFixedHeight(theme.BORDER)
        rule.setStyleSheet(f"background:{theme.INK};")
        rule_row.addWidget(rule)
        layout.addLayout(rule_row)

        self._list = QListWidget()
        self._list.setStyleSheet(LIST_QSS)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_list_context_menu)
        self._list.setFrameShape(QListWidget.Shape.NoFrame)
        self._list.setAlternatingRowColors(True)
        self._list.setIconSize(QSize(18, 18))
        layout.addWidget(self._list, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(7)

        for text, handler, color, background, hover in [
            ("添加", self._on_add, theme.INK, theme.SURFACE, theme.SURFACE_WARM),
            ("完成", self._on_toggle, theme.INK, theme.ACCENT, theme.ACCENT_HOVER),
            ("删除", self._on_delete, theme.DANGER, theme.SURFACE, theme.PRIMARY_SOFT),
        ]:
            btn = QPushButton(text)
            btn.setStyleSheet(BUTTON_QSS + (
                f"QPushButton {{ color: {color}; background: {background}; }}"
                f"QPushButton:hover {{ background: {hover}; }}"
            ))
            btn.clicked.connect(handler)
            btn_row.addWidget(btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._stats = QLabel("")
        self._stats.setStyleSheet(
            f"font-size: 11px; color: {theme.INK_MUTED};")
        layout.addWidget(self._stats)


    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()


    def _refresh(self):
        items = self._core.list_all()
        self._list.clear()
        for t in items:
            done = t["status"] == "done"
            item = QListWidgetItem(self._status_icon(done), t["title"])
            item.setData(Qt.ItemDataRole.UserRole, t["id"])
            item.setData(Qt.ItemDataRole.ToolTipRole, t["title"])
            if done:
                font = item.font()
                font.setStrikeOut(True)
                item.setFont(font)
                item.setForeground(QColor(theme.INK_MUTED))
            self._list.addItem(item)
        done_count = sum(1 for t in items if t["status"] == "done")
        self._stats.setText(f"共 {len(items)} 条，{done_count} 已完成 · 右键可复制标题")

    def _current_id(self) -> int | None:
        item = self._list.currentItem()
        if item:
            return item.data(Qt.ItemDataRole.UserRole)
        return None

    def _on_add(self):
        """弹出标题对话框添加待办。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("添加待办")
        dlg.setStyleSheet(BASE_QSS)
        dlg.resize(320, 100)
        form = QFormLayout(dlg)
        form.setSpacing(10)

        edt_title = QLineEdit()
        edt_title.setPlaceholderText("输入任务标题")
        form.addRow("标题:", edt_title)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        form.addRow(btn_box)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        title = edt_title.text().strip()
        if not title:
            return

        self._core.add(title=title)
        self._refresh()

    def _on_toggle(self):
        tid = self._current_id()
        if tid is None:
            return
        self._core.toggle(tid)
        self._refresh()

    def _on_list_context_menu(self, pos):
        """右键菜单：复制选中项的纯文本标题。"""
        item = self._list.itemAt(pos)
        if item is None:
            return
        title = item.data(Qt.ItemDataRole.ToolTipRole)
        if not title:
            title = title.replace("[√]", "").replace("[ ]", "").replace("~~", "").strip()

        menu = QMenu(self)
        menu.setStyleSheet(MENU_QSS)
        act_copy = menu.addAction("复制")
        if menu.exec(self._list.mapToGlobal(pos)) == act_copy:
            QApplication.clipboard().setText(title)

    def _on_delete(self):
        tid = self._current_id()
        if tid is None:
            return
        reply = QMessageBox.question(
            self, "确认删除", "确定要删除这个任务吗？")
        if reply == QMessageBox.StandardButton.Yes:
            self._core.delete(tid)
            self._refresh()


def show_panel(core: TodoListTool) -> None:
    """显示管理面板（模块内单例）：已关闭的重建，存活的置顶。"""
    global _current
    if _current is not None:
        try:
            alive = _current.isVisible()
        except RuntimeError:
            alive = False
        if not alive:
            _current.deleteLater()
            _current = None
    if _current is None:
        _current = TodoPanel(core)
    _current.show()
    _current.raise_()
