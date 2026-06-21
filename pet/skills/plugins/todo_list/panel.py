"""Todo 管理面板 — 右键菜单「查看待办」弹出的独立窗口。"""

import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLineEdit, QComboBox, QLabel, QInputDialog, QMessageBox,
)
from PySide6.QtCore import Qt

from pet.skills.plugins.todo_list.storage import TodoStorage

logger = logging.getLogger(__name__)


class TodoPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._storage = TodoStorage()
        self.setWindowTitle("待办事项")
        self.resize(420, 520)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self._setup_ui()
        self._refresh()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── 筛选栏 ──
        filter_row = QHBoxLayout()
        self._status_filter = QComboBox()
        self._status_filter.addItems(["pending", "done"])
        self._status_filter.currentTextChanged.connect(self._refresh)
        filter_row.addWidget(QLabel("状态:"))
        filter_row.addWidget(self._status_filter)

        self._priority_filter = QComboBox()
        self._priority_filter.addItems(["全部", "high", "medium", "low"])
        self._priority_filter.currentTextChanged.connect(self._refresh)
        filter_row.addWidget(QLabel("优先级:"))
        filter_row.addWidget(self._priority_filter)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        # ── 任务列表 ──
        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        layout.addWidget(self._list)

        # ── 操作按钮 ──
        btn_row = QHBoxLayout()
        btn_add = QPushButton("➕ 添加")
        btn_add.clicked.connect(self._on_add)
        btn_row.addWidget(btn_add)

        btn_done = QPushButton("✓ 完成")
        btn_done.clicked.connect(self._on_complete)
        btn_row.addWidget(btn_done)

        btn_delete = QPushButton("✗ 删除")
        btn_delete.clicked.connect(self._on_delete)
        btn_row.addWidget(btn_delete)

        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self._refresh)
        btn_row.addWidget(btn_refresh)
        layout.addLayout(btn_row)

    def _refresh(self):
        status = self._status_filter.currentText()
        priority = self._priority_filter.currentText()
        items = self._storage.list(
            status=status,
            priority=None if priority == "全部" else priority,
        )
        self._list.clear()
        for t in items:
            text = f"[{t['priority']}] {t['title']}"
            if t["due_date"]:
                text += f"  ⏰ {t['due_date']}"
            if t["category"]:
                text += f"  📁 {t['category']}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, t["id"])
            if t["status"] == "done":
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(item)

    def _current_id(self) -> int | None:
        item = self._list.currentItem()
        if item:
            return item.data(Qt.ItemDataRole.UserRole)
        return None

    def _on_add(self):
        title, ok = QInputDialog.getText(self, "添加待办", "任务标题:")
        if not ok or not title.strip():
            return
        self._storage.add(title=title.strip())
        self._refresh()

    def _on_complete(self):
        tid = self._current_id()
        if tid is None:
            return
        self._storage.complete(tid)
        self._refresh()

    def _on_delete(self):
        tid = self._current_id()
        if tid is None:
            return
        reply = QMessageBox.question(
            self, "确认删除", f"确定要删除任务 #{tid} 吗？")
        if reply == QMessageBox.StandardButton.Yes:
            self._storage.delete(tid)
            self._refresh()

    def closeEvent(self, event):
        self._storage.close()
        super().closeEvent(event)
