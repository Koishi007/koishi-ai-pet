"""记忆管理窗口"""

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QGroupBox, QLabel, QLineEdit,
    QFormLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QCheckBox,
    QComboBox, QScrollArea, QTextEdit, QMessageBox,
    QSizePolicy, QSpacerItem,
)
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtGui import QFont, QIcon, QPainter, QPainterPath, QColor, QPen

from pet.ui.styles import (
    ICON_PATH, WINDOW_QSS, PANEL_QSS, BUTTON_QSS,
    BUTTON_PRIMARY_QSS, BUTTON_DANGER_QSS,
    TEXTEDIT_QSS, COMBOBOX_QSS, LIST_QSS,
    SCROLLBAR_QSS,
    _COLOR_BG, _COLOR_BORDER_DARK, _COLOR_TEXT_TITLE,
    _COLOR_ACCENT, _COLOR_DANGER,
    make_minimize_button, make_close_button, ensure_taskbar_icon,
)

logger = logging.getLogger(__name__)

_PAGE_SIZES = [20, 50, 100]
_DEFAULT_PAGE_SIZE = 50

# 级别颜色映射
_LEVEL_COLORS = {
    "L1": "#e81123",
    "L2": "#4a90d9",
    "L3": "#999999",
}


class MemoryWindow(QWidget):
    """记忆管理窗口 — 浏览、编辑、删除记忆。"""

    def __init__(self, agent, parent=None):
        super().__init__(parent)
        self._agent = agent
        self._memory_store = agent.memory_store if agent else None
        self._force_close = False

        # 分页状态
        self._page = 0
        self._page_size = _DEFAULT_PAGE_SIZE
        self._total = 0

        # 筛选状态
        self._filter_level = ""
        self._filter_importance = 0
        self._filter_search = ""

        # 当前选中记忆
        self._selected_ids: set[int] = set()

        self._setup_window()
        self._setup_ui()
        self.setStyleSheet(
            WINDOW_QSS + PANEL_QSS + BUTTON_QSS +
            BUTTON_PRIMARY_QSS + BUTTON_DANGER_QSS +
            TEXTEDIT_QSS + COMBOBOX_QSS +
            LIST_QSS + SCROLLBAR_QSS
        )

        self._refresh_data()

    def _setup_window(self):
        self.setWindowTitle("记忆管理")
        self.setObjectName("FlatWindow")
        self.setMinimumSize(800, 650)
        self.resize(800, 650)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        try:
            self.setWindowIcon(QIcon(ICON_PATH))
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        ensure_taskbar_icon(self)
        self._refresh_data()

    # 标题栏相关

    def closeEvent(self, event):
        if self._force_close:
            event.accept()
        else:
            self.hide()
            event.ignore()

    # UI 搭建

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 2, 6, 4)
        root.setSpacing(2)

        # 标题栏
        header = QWidget()
        header.setObjectName("LogHeader")
        header.setFixedHeight(38)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 6, 0)
        header_layout.setSpacing(6)

        try:
            icon_lbl = QLabel()
            icon_lbl.setPixmap(QIcon(ICON_PATH).pixmap(18, 18))
            header_layout.addWidget(icon_lbl)
        except Exception:
            pass

        title_lbl = QLabel("记忆管理")
        title_lbl.setStyleSheet(
            f"font-size:13px; color:{_COLOR_TEXT_TITLE}; font-weight:bold; background:transparent;"
        )
        header_layout.addWidget(title_lbl)
        header_layout.addStretch()
        header_layout.addWidget(make_minimize_button(self))
        header_layout.addWidget(make_close_button(self))

        header.mousePressEvent = self._header_press
        header.mouseMoveEvent = self._header_move
        self._drag_pos = None

        root.addWidget(header)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {_COLOR_BG}; border: none; }}"
            f"QWidget#ScrollInner {{ background: {_COLOR_BG}; }}"
        )

        scroll_inner = QWidget()
        scroll_inner.setObjectName("ScrollInner")
        scroll_layout = QVBoxLayout(scroll_inner)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(6)

        self._build_filter_bar(scroll_layout)
        self._build_table(scroll_layout)
        self._build_action_bar(scroll_layout)
        self._build_detail_panel(scroll_layout)
        scroll_layout.addStretch()

        scroll.setWidget(scroll_inner)
        root.addWidget(scroll, 1)

        # 分页条（固定在底部）
        self._build_pagination_bar(root)

    def _build_filter_bar(self, parent_layout):
        group = QGroupBox("筛选")
        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        row1 = QHBoxLayout()

        # 级别筛选按钮组
        self._label_level = QLabel("级别:")
        self._label_level.setStyleSheet("font-size: 12px; color: #333;")
        row1.addWidget(self._label_level)
        self._level_btns: dict[str, QPushButton] = {}
        for label in ("全部", "L1", "L2", "L3"):
            key = "" if label == "全部" else label
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedWidth(48)
            btn.setFont(QFont("", 12))
            btn.setChecked(key == self._filter_level)
            btn.clicked.connect(lambda checked, k=key: self._on_filter_level(k))
            self._level_btns[key] = btn
            row1.addWidget(btn)

        row1.addSpacing(12)

        # 重要性筛选
        self._label_imp = QLabel("重要性:")
        self._label_imp.setStyleSheet("font-size: 12px; color: #333;")
        row1.addWidget(self._label_imp)
        self._imp_btns: dict[int, QPushButton] = {}
        for val in [0, 1, 2, 3, 4, 5]:
            label = "全部" if val == 0 else str(val)
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedWidth(48)
            btn.setFont(QFont("", 12))
            btn.setChecked(val == self._filter_importance)
            btn.clicked.connect(lambda checked, v=val: self._on_filter_importance(v))
            self._imp_btns[val] = btn
            row1.addWidget(btn)

        row1.addStretch()
        layout.addLayout(row1)

        # 第二行：搜索 + 每页条数
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("搜索:"))
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("输入关键词搜索记忆内容…")
        self._search_input.setStyleSheet(
            "QLineEdit {"
            "  border: 1px solid #ddd; border-radius: 6px;"
            "  padding: 2px 8px; font-size: 12px;"
            "}"
        )
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(300)
        self._search_debounce.timeout.connect(self._on_search_changed)
        self._search_input.textChanged.connect(lambda: self._search_debounce.start())
        row2.addWidget(self._search_input)

        row2.addStretch()

        row2.addWidget(QLabel("每页:"))
        self._page_size_combo = QComboBox()
        self._page_size_combo.addItems([str(s) for s in _PAGE_SIZES])
        self._page_size_combo.setCurrentText(str(_DEFAULT_PAGE_SIZE))
        self._page_size_combo.currentTextChanged.connect(self._on_page_size_changed)
        row2.addWidget(self._page_size_combo)

        layout.addLayout(row2)
        parent_layout.addWidget(group)

    def _build_table(self, parent_layout):
        group = QGroupBox("记忆列表")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget()
        self._table.setMinimumHeight(200)
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels([
            "", "内容", "级别", "重要性", "创建时间", "访问次数"
        ])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 30)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(2, 50)
        self._table.setColumnWidth(3, 50)
        self._table.setColumnWidth(4, 100)
        self._table.setColumnWidth(5, 50)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(
            "QTableWidget {"
            "  alternate-background-color: #fafafa;"
            "  gridline-color: #eee;"
            "  selection-background-color: #d6eaf8;"
            "  selection-color: #333;"
            "}"
        )
        self._table.itemSelectionChanged.connect(self._on_table_selection_changed)

        layout.addWidget(self._table)
        parent_layout.addWidget(group)

    def _build_action_bar(self, parent_layout):
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)

        self._btn_delete = QPushButton("删除选中")
        self._btn_delete.setStyleSheet(BUTTON_DANGER_QSS)
        self._btn_delete.clicked.connect(self._on_delete)
        self._btn_delete.setEnabled(False)
        layout.addWidget(self._btn_delete)

        layout.addStretch()
        parent_layout.addWidget(wrapper)

    def _build_detail_panel(self, parent_layout):
        group = QGroupBox("详情")
        layout = QFormLayout(group)
        layout.setSpacing(6)

        self._detail_category = QLabel("—")
        layout.addRow("分类:", self._detail_category)

        self._detail_level = QComboBox()
        self._detail_level.addItems(["L1", "L2", "L3"])
        self._detail_level.currentTextChanged.connect(self._mark_dirty)
        self._detail_level.installEventFilter(self)
        layout.addRow("级别:", self._detail_level)

        self._detail_importance = QComboBox()
        self._detail_importance.addItems([str(i) for i in range(1, 6)])
        self._detail_importance.currentTextChanged.connect(self._mark_dirty)
        self._detail_importance.installEventFilter(self)
        layout.addRow("重要性:", self._detail_importance)

        self._detail_keywords = QLineEdit()
        self._detail_keywords.textChanged.connect(self._mark_dirty)
        layout.addRow("关键词:", self._detail_keywords)

        self._detail_content = QTextEdit()
        self._detail_content.setMaximumHeight(80)
        self._detail_content.textChanged.connect(self._mark_dirty)
        layout.addRow("全文:", self._detail_content)

        self._detail_created = QLabel("—")
        layout.addRow("创建时间:", self._detail_created)

        self._detail_accessed = QLabel("—")
        layout.addRow("最近访问:", self._detail_accessed)

        self._detail_access_count = QLabel("—")
        layout.addRow("访问次数:", self._detail_access_count)

        self._detail_effective = QLabel("—")
        layout.addRow("有效分:", self._detail_effective)

        # 保存按钮
        save_row = QHBoxLayout()
        save_row.addStretch()
        self._btn_save = QPushButton("保存")
        self._btn_save.setStyleSheet(BUTTON_PRIMARY_QSS)
        self._btn_save.clicked.connect(self._on_save)
        self._btn_save.setEnabled(False)
        save_row.addWidget(self._btn_save)
        layout.addRow(save_row)

        self._detail_panel = group
        self._detail_panel.setVisible(False)
        self._dirty = False
        parent_layout.addWidget(group)

    def _build_pagination_bar(self, parent_layout):
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(4)

        layout.addStretch()

        self._page_btns: list[QPushButton] = []

        self._btn_prev = QPushButton("<")
        self._btn_prev.setFixedWidth(32)
        self._btn_prev.clicked.connect(self._on_prev_page)
        layout.addWidget(self._btn_prev)

        for i in range(7):
            btn = QPushButton()
            btn.setFixedWidth(32)
            btn.setVisible(False)
            btn.clicked.connect(lambda checked, idx=i: self._on_goto_page(idx))
            self._page_btns.append(btn)
            layout.addWidget(btn)

        self._btn_next = QPushButton(">")
        self._btn_next.setFixedWidth(32)
        self._btn_next.clicked.connect(self._on_next_page)
        layout.addWidget(self._btn_next)

        layout.addStretch()
        parent_layout.addWidget(wrapper)

    # 数据加载

    def _refresh_data(self):
        if not self._memory_store:
            return
        self._rows, self._total = self._memory_store.list_memories(
            level=self._filter_level,
            importance=self._filter_importance,
            search=self._filter_search,
            page=self._page,
            page_size=self._page_size,
        )
        self._populate_table()

    def _populate_table(self):
        self._table.setRowCount(0)
        self._table.setRowCount(len(self._rows))
        for i, row in enumerate(self._rows):
            lookup = dict(row)

            # 复选框
            cb = QCheckBox()
            cb.stateChanged.connect(
                lambda state, mid=lookup["id"]: self._on_checkbox_toggled(mid, state)
            )
            container = QWidget()
            cb_layout = QHBoxLayout(container)
            cb_layout.addWidget(cb)
            cb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            self._table.setCellWidget(i, 0, container)

            # 内容
            content = lookup["content"] or ""
            display = content[:60] + ("…" if len(content) > 60 else "")
            self._table.setItem(i, 1, QTableWidgetItem(display))

            # 级别
            lvl_item = QTableWidgetItem(lookup.get("level", "L2"))
            lvl = lookup.get("level", "L2")
            lvl_item.setForeground(QColor(_LEVEL_COLORS.get(lvl, "#333")))
            self._table.setItem(i, 2, lvl_item)

            # 重要性
            self._table.setItem(i, 3, QTableWidgetItem(str(lookup.get("importance", 3))))

            # 创建时间
            created = lookup.get("created_at", "")
            if created:
                try:
                    dt = datetime.fromisoformat(created)
                    created = dt.strftime("%m-%d %H:%M")
                except Exception:
                    pass
            self._table.setItem(i, 4, QTableWidgetItem(created))

            # 访问次数
            self._table.setItem(i, 5, QTableWidgetItem(str(lookup.get("access_count", 0))))

        self._update_pagination_buttons()
        # 表格重建后清空勾选状态，防止旧 ID 残留导致误删
        self._selected_ids.clear()
        self._update_action_buttons()

    def _update_pagination_buttons(self):
        total_pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        self._btn_prev.setEnabled(self._page > 0)
        self._btn_next.setEnabled(self._page < total_pages - 1)

        for i, btn in enumerate(self._page_btns):
            start = max(0, self._page - 3)
            page_num = start + i
            if page_num < total_pages:
                btn.setText(str(page_num + 1))
                btn.setVisible(True)
                btn.setChecked(page_num == self._page)
                btn.setEnabled(page_num != self._page)
            else:
                btn.setVisible(False)

    # 筛选事件

    def _on_filter_level(self, key: str):
        self._filter_level = key
        self._page = 0
        for k, btn in self._level_btns.items():
            btn.setChecked(k == key)
        self._refresh_data()
        self._clear_detail()

    def _on_filter_importance(self, val: int):
        self._filter_importance = val
        self._page = 0
        for v, btn in self._imp_btns.items():
            btn.setChecked(v == val)
        self._refresh_data()
        self._clear_detail()

    def _on_search_changed(self):
        self._filter_search = self._search_input.text().strip()
        self._page = 0
        self._refresh_data()
        self._clear_detail()

    def _on_page_size_changed(self, text: str):
        self._page_size = int(text)
        self._page = 0
        self._refresh_data()
        self._clear_detail()

    # 分页事件

    def _on_prev_page(self):
        if self._page > 0:
            self._page -= 1
            self._refresh_data()
            self._clear_detail()

    def _on_next_page(self):
        total_pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        if self._page < total_pages - 1:
            self._page += 1
            self._refresh_data()
            self._clear_detail()

    def _on_goto_page(self, idx: int):
        start = max(0, self._page - 3)
        target = start + idx
        total_pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        if 0 <= target < total_pages:
            self._page = target
            self._refresh_data()
            self._clear_detail()

    # 表格选中

    def _on_checkbox_toggled(self, memory_id: int, state: int):
        if state == Qt.CheckState.Checked.value:
            self._selected_ids.add(memory_id)
        else:
            self._selected_ids.discard(memory_id)
        self._update_action_buttons()

    def _on_table_selection_changed(self):
        selected_rows = set()
        for item in self._table.selectedItems():
            selected_rows.add(item.row())

        if len(selected_rows) == 1:
            row = list(selected_rows)[0]
            self._fill_detail(row)
        else:
            self._clear_detail()

        self._update_action_buttons()

    def _update_action_buttons(self):
        active_ids = set()
        for i in range(self._table.rowCount()):
            widget = self._table.cellWidget(i, 0)
            if widget:
                cb = widget.findChild(QCheckBox)
                if cb and cb.isChecked():
                    if i < len(self._rows):
                        active_ids.add(dict(self._rows[i])["id"])
        self._selected_ids = active_ids
        self._btn_delete.setEnabled(len(self._selected_ids) > 0)

    def _clear_detail(self):
        self._detail_panel.setVisible(False)
        self._selected_ids.clear()
        self._update_action_buttons()
        self._dirty = False
        self._current_detail_id = None

    def _fill_detail(self, row: int):
        if row >= len(self._rows):
            return
        lookup = dict(self._rows[row])
        self._current_detail_id = lookup["id"]

        # 阻塞所有信号，避免填充时触发 _mark_dirty
        self._detail_level.blockSignals(True)
        self._detail_importance.blockSignals(True)
        self._detail_keywords.blockSignals(True)
        self._detail_content.blockSignals(True)

        self._detail_category.setText(lookup.get("category", ""))
        self._detail_level.setCurrentText(lookup.get("level", "L2"))
        self._detail_importance.setCurrentText(str(lookup.get("importance", 3)))
        self._detail_keywords.setText(lookup.get("keywords", ""))
        self._detail_content.setPlainText(lookup.get("content", ""))

        self._detail_level.blockSignals(False)
        self._detail_importance.blockSignals(False)
        self._detail_keywords.blockSignals(False)
        self._detail_content.blockSignals(False)

        self._dirty = False

        created = lookup.get("created_at", "—")
        if created and created != "—":
            try:
                dt = datetime.fromisoformat(created)
                created = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
        self._detail_created.setText(created)

        last_acc = lookup.get("last_accessed_at", "—")
        if last_acc and last_acc != "—":
            try:
                dt = datetime.fromisoformat(last_acc)
                last_acc = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
        self._detail_accessed.setText(last_acc)

        self._detail_access_count.setText(str(lookup.get("access_count", 0)))

        # 有效分值
        try:
            eff = self._memory_store.get_effective_importance(lookup)
            self._detail_effective.setText(f"{eff:.2f}")
        except Exception:
            self._detail_effective.setText("—")

        self._detail_panel.setVisible(True)
        self._btn_save.setEnabled(False)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            if obj is self._detail_level or obj is self._detail_importance:
                event.ignore()
                return True
        return super().eventFilter(obj, event)

    def _mark_dirty(self):
        if self._current_detail_id is None:
            return
        self._dirty = True
        self._btn_save.setEnabled(True)

    # 操作

    def _on_save(self):
        if not self._current_detail_id:
            return
        if not self._dirty:
            return

        memory_id = self._current_detail_id
        try:
            new_level = self._detail_level.currentText()
            new_importance = int(self._detail_importance.currentText())
            new_keywords = self._detail_keywords.text().strip()
            new_content = self._detail_content.toPlainText().strip()

            self._memory_store.update_memory(
                memory_id,
                level=new_level,
                importance=new_importance,
                keywords=new_keywords,
                content=new_content,
            )
            self._dirty = False
            self._btn_save.setEnabled(False)
            self._refresh_data()
            # 刷新后重新选中刚编辑的记忆行，保持详情面板可见
            self._reselect_memory(memory_id)
            logger.info(f"[MemoryWindow] 编辑记忆 #{memory_id}")
        except Exception as e:
            QMessageBox.warning(self, "编辑失败", str(e))

    def _reselect_memory(self, memory_id: int):
        """根据 memory_id 在表格中查找对应的行并填充详情。"""
        for i, row_data in enumerate(self._rows):
            if dict(row_data)["id"] == memory_id:
                self._table.selectRow(i)
                return
        # 编辑后的记忆不在当前页/筛选结果中，清空详情
        self._clear_detail()

    def _on_delete(self):
        if not self._selected_ids:
            return

        count = len(self._selected_ids)
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除选中的 {count} 条记忆吗？\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self._memory_store.delete_memories(list(self._selected_ids))
            logger.info(f"[MemoryWindow] 删除 {count} 条记忆")
            self._selected_ids.clear()
            self._clear_detail()
            self._refresh_data()
        except Exception as e:
            QMessageBox.warning(self, "删除失败", str(e))

    # 窗口拖拽

    def _header_press(self, event):
        self._drag_pos = event.globalPosition().toPoint()

    def _header_move(self, event):
        if self._drag_pos is not None:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()

    # 圆角背景

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)
        painter.fillPath(path, QColor(_COLOR_BG))
        painter.setPen(QPen(QColor(_COLOR_BORDER_DARK), 1))
        painter.drawPath(path)
        painter.end()
