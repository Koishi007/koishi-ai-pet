"""知识库管理面板 — 添加、导入文件、搜索、删除。"""

from __future__ import annotations

import logging
import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QMessageBox, QLineEdit, QTextEdit,
    QDialog, QFormLayout, QDialogButtonBox, QFileDialog,
)
from PySide6.QtCore import Qt

logger = logging.getLogger(__name__)

_W = 600
_H = 620


class KnowledgePanel(QWidget):
    """知识库管理面板 — 无边框圆角窗口，标题栏可拖动。"""

    def __init__(self, storage):
        super().__init__()
        self._storage = storage
        self._drag_pos = None
        self._page = 1

        self.setObjectName("knowledgePanel")
        self.setWindowTitle("知识库")
        self.resize(_W, _H)
        self.setFixedWidth(_W)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._setup_ui()
        self._refresh()

    # ── UI 构建 ──

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bg = QWidget()
        bg.setObjectName("kbBg")
        bg.setStyleSheet("""
            QWidget#kbBg {
                background: #f0f0f0;
                border-radius: 8px;
                font-size: 12px;
            }
        """)
        layout = QVBoxLayout(bg)
        layout.setContentsMargins(16, 0, 16, 16)
        layout.setSpacing(8)
        root.addWidget(bg)

        # ── 标题栏 ──
        title_bar = QWidget()
        title_bar.setFixedHeight(40)
        title_row = QHBoxLayout(title_bar)
        title_row.setContentsMargins(0, 0, 0, 0)

        title = QLabel("📚 知识库")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #333;")
        title_row.addWidget(title)
        title_row.addStretch()

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(28, 28)
        btn_close.setStyleSheet("""
            QPushButton {
                background: transparent; border: none;
                border-radius: 14px; font-size: 14px; color: #999;
            }
            QPushButton:hover { background: #e81123; color: #fff; }
        """)
        btn_close.clicked.connect(self.close)
        title_row.addWidget(btn_close)
        layout.addWidget(title_bar)

        # ── 搜索栏 ──
        search_row = QHBoxLayout()
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("输入关键词搜索知识库...")
        self._search_input.setStyleSheet("""
            QLineEdit {
                background: #fff; border: 1px solid #ddd;
                border-radius: 6px; padding: 6px 10px; font-size: 12px;
            }
            QLineEdit:focus { border-color: #4a90d9; }
        """)
        self._search_input.returnPressed.connect(self._on_search)
        search_row.addWidget(self._search_input)

        btn_search = QPushButton("🔍 搜索")
        btn_search.setStyleSheet("""
            QPushButton {
                background: #fff; border: 1px solid #ddd;
                border-radius: 6px; padding: 6px 12px;
                font-size: 12px; color: #333; min-width: 64px;
            }
            QPushButton:hover { background: #4a90d9; border-color: #4a90d9; color: #fff; }
        """)
        btn_search.clicked.connect(self._on_search)
        search_row.addWidget(btn_search)
        layout.addLayout(search_row)

        # ── 文档列表 ──
        self._list = QListWidget()
        self._list.setStyleSheet("""
            QListWidget {
                background: #fff; border: 1px solid #ddd;
                border-radius: 6px; font-size: 12px; color: #333; outline: none;
            }
            QListWidget::item { padding: 8px; border-bottom: 1px solid #eee; }
            QListWidget::item:selected { background: #e0e0e0; }
        """)
        layout.addWidget(self._list, stretch=1)

        # ── 搜索结果标签（默认隐藏）──
        self._result_label = QLabel("")
        self._result_label.setStyleSheet("font-size: 11px; color: #666;")
        self._result_label.setVisible(False)
        layout.addWidget(self._result_label)

        # ── 返回列表按钮（搜索结果模式下可见）──
        self._btn_back = QPushButton("📋 返回列表")
        self._btn_back.setStyleSheet("""
            QPushButton {
                background: #fff; border: 1px solid #ddd;
                border-radius: 6px; padding: 4px 12px;
                font-size: 12px; color: #333; min-width: 64px;
            }
            QPushButton:hover { background: #e8e8e8; }
            QPushButton:pressed { background: #d8d8d8; }
        """)
        self._btn_back.clicked.connect(self._on_back)
        self._btn_back.setVisible(False)
        layout.addWidget(self._btn_back)

        # ── 操作按钮 ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        for text, handler, hover_qss in [
            ("➕ 添加", self._on_add,
             "QPushButton:hover { background: #4a90d9; border-color: #4a90d9; color: #fff; }"),
            ("📁 导入文件", self._on_import,
             "QPushButton:hover { background: #27ae60; border-color: #27ae60; color: #fff; }"),
            ("🗑️ 删除", self._on_delete,
             "QPushButton:hover { background: #e74c3c; border-color: #e74c3c; color: #fff; }"),
        ]:
            btn = QPushButton(text)
            btn.setStyleSheet("""
                QPushButton {
                    background: #fff; border: 1px solid #ddd;
                    border-radius: 6px; padding: 4px 12px;
                    font-size: 12px; color: #333; min-width: 64px;
                }
                QPushButton:hover { background: #e8e8e8; }
                QPushButton:pressed { background: #d8d8d8; }
            """ + hover_qss)
            btn.clicked.connect(handler)
            btn_row.addWidget(btn)

        btn_row.addStretch()

        # ── 分页 ──
        self._btn_prev = QPushButton("◀ 上一页")
        self._btn_prev.setStyleSheet("""
            QPushButton { background: #fff; border: 1px solid #ddd;
                border-radius: 6px; padding: 4px 12px; font-size: 12px; color: #666; }
            QPushButton:hover { background: #e8e8e8; }
            QPushButton:disabled { color: #ccc; }
        """)
        self._btn_prev.clicked.connect(self._prev_page)
        btn_row.addWidget(self._btn_prev)

        self._btn_next = QPushButton("下一页 ▶")
        self._btn_next.setStyleSheet(self._btn_prev.styleSheet())
        self._btn_next.clicked.connect(self._next_page)
        btn_row.addWidget(self._btn_next)

        layout.addLayout(btn_row)

        # ── 底部统计 ──
        self._stats = QLabel("")
        self._stats.setStyleSheet("font-size: 11px; color: #666;")
        layout.addWidget(self._stats)

    # ── 窗口拖动 ──

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

    # ── 数据刷新 ──

    def _refresh(self):
        data = self._storage.list_documents(page=self._page, page_size=20)
        self._list.clear()
        for doc in data["documents"]:
            tags_str = f" [{doc['tags']}]" if doc.get("tags") else ""
            chunk_str = f" ({doc.get('chunk_count', 0)}块)"
            preview = doc["content"][:80].replace("\n", " ")
            text = f"  #{doc['id']}  {doc['title']}{tags_str}{chunk_str}\n      {preview}..."
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, doc["id"])
            self._list.addItem(item)

        self._stats.setText(
            f"第 {data['page']}/{data['total_pages']} 页"
        )
        self._btn_prev.setEnabled(data["has_prev"])
        self._btn_next.setEnabled(data["has_next"])
        self._result_label.setVisible(False)
        self._btn_back.setVisible(False)

    # ── 操作 ──

    def _on_add(self):
        """弹出添加对话框 — 标题 + 正文 + 标签。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("添加知识")
        dlg.resize(500, 400)
        form = QFormLayout(dlg)
        form.setSpacing(10)

        edt_title = QLineEdit()
        edt_title.setPlaceholderText("知识标题/摘要")
        form.addRow("标题:", edt_title)

        edt_tags = QLineEdit()
        edt_tags.setPlaceholderText("逗号分隔，如: 宠物,安全,饮食")
        form.addRow("标签:", edt_tags)

        edt_content = QTextEdit()
        edt_content.setPlaceholderText("输入知识全文内容...\n支持多行文本。")
        form.addRow("内容:", edt_content)

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
        content = edt_content.toPlainText().strip()
        tags = edt_tags.text().strip()
        if not title or not content:
            QMessageBox.warning(self, "提示", "标题和内容不能为空")
            return

        try:
            result = self._storage.add_document(title=title, content=content, tags=tags)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"添加失败: {e}")
            return
        QMessageBox.information(self, "成功", f"已添加，共 {result['chunks']} 个分块")
        self._refresh()

    def _on_import(self):
        """从文件导入知识。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择文件",
            "", "文本文件 (*.txt *.md);;所有文件 (*.*)"
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            QMessageBox.warning(self, "错误", f"读取文件失败: {e}")
            return

        if not content.strip():
            QMessageBox.warning(self, "提示", "文件内容为空")
            return

        title = os.path.splitext(os.path.basename(path))[0]
        try:
            result = self._storage.add_document(
                title=title, content=content, source="file"
            )
        except Exception as e:
            QMessageBox.warning(self, "错误", f"导入失败: {e}")
            return
        QMessageBox.information(
            self, "导入成功",
            f"已导入「{title}」，共 {result['chunks']} 个分块\n来源: {path}"
        )
        self._refresh()

    def _on_search(self):
        """本地搜索测试 — 验证检索效果。"""
        query = self._search_input.text().strip()
        if not query:
            return

        try:
            results = self._storage.search(query, limit=5)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"搜索失败: {e}")
            return
        self._list.clear()

        if not results:
            self._result_label.setText("未找到匹配结果")
            self._result_label.setVisible(True)
            self._stats.setText("")
            self._show_back_btn()
            return

        for r in results:
            score = r.get("score", 0)
            score_str = f" (相似度: {score:.2f})" if score else ""
            title = r.get("title", "")
            preview = r["content"][:100].replace("\n", " ")
            text = f"  📄 {title}{score_str}\n      {preview}..."
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, r.get("doc_id"))
            self._list.addItem(item)

        self._result_label.setText(f"找到 {len(results)} 条匹配结果")
        self._result_label.setVisible(True)
        self._stats.setText("")
        self._show_back_btn()

    def _on_delete(self):
        doc_id = self._current_id()
        if doc_id is None:
            return
        reply = QMessageBox.question(
            self, "确认删除", "确定要删除这条知识吗？")
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self._storage.delete_document(doc_id)
            except Exception as e:
                QMessageBox.warning(self, "错误", f"删除失败: {e}")
                return
            self._refresh()

    def _prev_page(self):
        if self._page > 1:
            self._page -= 1
            self._refresh()

    def _next_page(self):
        self._page += 1
        self._refresh()

    def _on_back(self):
        """从搜索结果返回列表视图。"""
        self._search_input.clear()
        self._refresh()

    def _show_back_btn(self):
        """搜索结果模式下显示返回列表按钮。"""
        self._btn_back.setVisible(True)

    def _current_id(self) -> int | None:
        item = self._list.currentItem()
        if item:
            return item.data(Qt.ItemDataRole.UserRole)
        return None
