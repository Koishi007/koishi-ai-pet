"""Todo 管理面板"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QMessageBox,
    QDialog, QLineEdit, QFormLayout, QDialogButtonBox,
)
from PySide6.QtCore import Qt

from pet.tools.todo import _instance as _todo_instance
from pet.tools.todo.style import LIST_QSS, BUTTON_QSS

logger = logging.getLogger(__name__)

_W = 420
_H = 520


class TodoPanel(QWidget):
    """任务管理面板 — 无边框圆角窗口，标题栏可拖动。"""

    def __init__(self):
        super().__init__()
        self._core = _todo_instance
        self._storage = self._core._storage
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
        self._refresh()


    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 背景容器
        bg = QWidget()
        bg.setObjectName("todoBg")
        bg.setStyleSheet("""
            QWidget#todoBg {
                background: #f0f0f0;
                border-radius: 8px;
                font-size: 12px;
            }
        """)
        layout = QVBoxLayout(bg)
        layout.setContentsMargins(16, 0, 16, 16)
        layout.setSpacing(8)
        root.addWidget(bg)

        title_bar = QWidget()
        title_bar.setFixedHeight(40)
        title_row = QHBoxLayout(title_bar)
        title_row.setContentsMargins(0, 0, 0, 0)

        title = QLabel("📋 待办事项")
        title.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: #333;"
        )
        title_row.addWidget(title)
        title_row.addStretch()

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(28, 28)
        btn_close.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 14px;
                font-size: 14px;
                color: #999;
            }
            QPushButton:hover {
                background: #e81123;
                color: #fff;
            }
        """)
        btn_close.clicked.connect(self.close)
        title_row.addWidget(btn_close)

        layout.addWidget(title_bar)

        self._list = QListWidget()
        self._list.setStyleSheet(LIST_QSS)
        self._list.setFrameShape(QListWidget.Shape.NoFrame)
        self._list.setAlternatingRowColors(True)
        layout.addWidget(self._list, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        for text, handler, extra_qss in [
            ("➕ 添加", self._on_add, """
                QPushButton:hover {
                    background: #4a90d9;
                    border-color: #4a90d9;
                    color: #fff;
                }
            """),
            ("✓ 完成", self._on_toggle, """
                QPushButton:hover {
                    background: #27ae60;
                    border-color: #27ae60;
                    color: #fff;
                }
            """),
            ("✗ 删除", self._on_delete, """
                QPushButton:hover {
                    background: #e74c3c;
                    border-color: #e74c3c;
                    color: #fff;
                }
            """),
        ]:
            btn = QPushButton(text)
            btn.setStyleSheet(BUTTON_QSS + extra_qss)
            btn.clicked.connect(handler)
            btn_row.addWidget(btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._stats = QLabel("")
        self._stats.setStyleSheet("font-size: 11px; color: #666;")
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
        items = self._storage.list()
        self._list.clear()
        for t in items:
            if t["status"] == "done":
                text = f"  ✅  ~~{t['title']}~~"
            else:
                text = f"  ○  {t['title']}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, t["id"])
            self._list.addItem(item)
        done_count = sum(1 for t in items if t["status"] == "done")
        self._stats.setText(f"共 {len(items)} 条，{done_count} 已完成")

    def _current_id(self) -> int | None:
        item = self._list.currentItem()
        if item:
            return item.data(Qt.ItemDataRole.UserRole)
        return None

    def _on_add(self):
        """弹出标题对话框添加待办。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("添加待办")
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

        self._storage.add(title=title)
        self._refresh()

    def _on_toggle(self):
        tid = self._current_id()
        if tid is None:
            return
        self._storage.toggle(tid)
        self._refresh()

    def _on_delete(self):
        tid = self._current_id()
        if tid is None:
            return
        reply = QMessageBox.question(
            self, "确认删除", "确定要删除这个任务吗？")
        if reply == QMessageBox.StandardButton.Yes:
            self._storage.delete(tid)
            self._refresh()
