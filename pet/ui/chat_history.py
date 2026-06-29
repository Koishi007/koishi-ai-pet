"""对话历史窗口 — 以对话气泡形式展示用户与桌宠的对话记录。"""

import html
import logging

from PySide6.QtCore import Qt, QPoint, QTimer, QSize, QRect, QModelIndex
from PySide6.QtGui import (
    QFont, QTextCursor, QIcon, QPainter, QPainterPath, QColor, QPen,
    QTextDocument, QAbstractTextDocumentLayout, QPalette
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QListWidget, QListWidgetItem,
    QStyledItemDelegate, QStyleOptionViewItem
)

from pet.brain.conversation_store import ConversationStore
from pet.ui.styles import (
    ICON_PATH, BUTTON_PRIMARY_QSS, TEXTEDIT_QSS, SCROLLBAR_QSS, LIST_QSS,
    _COLOR_BG, _COLOR_TEXT_TITLE,
    make_minimize_button, make_close_button, ensure_taskbar_icon,
)

logger = logging.getLogger(__name__)

_RADIUS = 10  # 窗口圆角半径

_WINDOW_QSS = """
QWidget#ChatHistoryRoot {
    background: transparent;
}
"""

_HEADER_QSS = """
QWidget#ChatHistoryHeader {
    background: transparent;
}
"""


class ChatBubbleDelegate(QStyledItemDelegate):
    """自定义委托，用于绘制的聊天气泡。"""

    def __init__(self, list_widget, parent=None):
        super().__init__(parent)
        self.list_widget = list_widget
        self._h_padding = 14
        self._v_padding = 10
        self._spacing = 6
        self._bubble_radius = 8

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        role = index.data(Qt.ItemDataRole.UserRole)
        time_str = index.data(Qt.ItemDataRole.UserRole + 1)
        content = index.data(Qt.ItemDataRole.UserRole + 2)

        rect = option.rect

        # 特殊提示信息处理
        if role == "info":
            painter.setPen(QColor("#999999"))
            font = QFont("Microsoft YaHei", 9)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, content)
            painter.restore()
            return

        y = rect.y() + self._v_padding

        # 绘制时间和角色名
        is_user = (role == "user")
        role_name = "用户" if is_user else "恋恋"
        time_text = f"{role_name}  {time_str}"
        
        font_time = QFont("Microsoft YaHei", 8)
        painter.setFont(font_time)
        painter.setPen(QColor("#999999"))
        time_rect = QRect(rect.left() + self._h_padding, y, rect.width() - 2 * self._h_padding, 16)
        
        if is_user:
            painter.drawText(time_rect, Qt.AlignmentFlag.AlignRight, time_text)
        else:
            painter.drawText(time_rect, Qt.AlignmentFlag.AlignLeft, time_text)
        
        y += 16 + self._spacing

        # 准备文本测量
        doc = QTextDocument()
        doc.setDefaultFont(QFont("Microsoft YaHei", 10))
        doc.setPlainText(content)

        view_width = self.list_widget.viewport().width() - 2 * self._h_padding
        max_bubble_w = max(view_width * 0.65, 100)
        
        text_width = min(doc.idealWidth(), max_bubble_w)
        doc.setTextWidth(text_width)
        text_height = doc.size().height()

        bubble_w = int(text_width) + 2 * self._h_padding
        bubble_h = int(text_height) + 2 * self._v_padding

        # 计算气泡位置
        if is_user:
            bubble_x = rect.right() - self._h_padding - bubble_w
        else:
            bubble_x = rect.left() + self._h_padding

        bubble_rect = QRect(bubble_x, y, bubble_w, bubble_h)

        # 绘制气泡背景
        bubble_color = QColor("#d4edda") if is_user else QColor("#d6eaf8")  # 淡绿色 / 淡蓝色
        border_color = QColor("#b8d4be") if is_user else QColor("#b8cfe0")

        path = QPainterPath()
        path.addRoundedRect(bubble_rect, self._bubble_radius, self._bubble_radius)

        painter.fillPath(path, bubble_color)
        painter.setPen(QPen(border_color, 1))
        painter.drawPath(path)

        # 绘制文本
        painter.setPen(QColor("#333333"))
        text_rect = QRect(bubble_x + self._h_padding, y + self._v_padding, int(text_width), int(text_height))

        ctx = QAbstractTextDocumentLayout.PaintContext()
        ctx.palette.setColor(QPalette.ColorRole.Text, QColor("#333333"))

        painter.translate(text_rect.topLeft())
        doc.documentLayout().draw(painter, ctx)
        painter.translate(-text_rect.topLeft())

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        content = index.data(Qt.ItemDataRole.UserRole + 2)
        if not content:
            return QSize(option.rect.width(), 40)

        if index.data(Qt.ItemDataRole.UserRole) == "info":
            return QSize(option.rect.width(), 40)

        doc = QTextDocument()
        doc.setDefaultFont(QFont("Microsoft YaHei", 10))
        doc.setPlainText(content)

        view_width = self.list_widget.viewport().width() - 2 * self._h_padding
        max_bubble_w = max(view_width * 0.65, 100)

        text_width = min(doc.idealWidth(), max_bubble_w)
        doc.setTextWidth(text_width)
        text_height = doc.size().height()

        bubble_h = int(text_height) + 2 * self._v_padding
        total_h = self._v_padding + 16 + self._spacing + bubble_h + self._v_padding

        return QSize(option.rect.width() if option.rect.width() > 0 else view_width, total_h)


class ChatHistoryWindow(QWidget):
    """对话历史查看窗口。"""

    def __init__(self, store: ConversationStore, parent=None):
        super().__init__(parent)
        self._store = store
        self.setWindowTitle("对话历史")
        self.setObjectName("ChatHistoryRoot")
        self.setMinimumSize(500, 350)
        self.resize(700, 500)

        # 无边框 + 透明背景（用于 paintEvent 画圆角）
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.setStyleSheet(_WINDOW_QSS)

        # 窗口图标
        try:
            self.setWindowIcon(QIcon(ICON_PATH))
        except Exception:
            pass

        # ── 自定义标题栏 ──
        header = QWidget()
        header.setObjectName("ChatHistoryHeader")
        header.setFixedHeight(38)
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
        title_label = QLabel("对话历史")
        title_label.setStyleSheet(f"font-size:13px; color:{_COLOR_TEXT_TITLE}; font-weight:bold; background:transparent;")
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        # 最小化按钮
        header_layout.addWidget(make_minimize_button(self))

        # 关闭按钮
        header_layout.addWidget(make_close_button(self, on_close=self.hide))

        # ── 主体：左侧日期列表 + 右侧对话内容 ──
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)

        # 左侧日期列表
        self._date_list = QListWidget()
        self._date_list.setFixedWidth(120)
        self._date_list.setStyleSheet(LIST_QSS + SCROLLBAR_QSS + f"""
            QListWidget {{
                font-size: 12px;
                background: {_COLOR_BG};
            }}
        """)
        self._date_list.currentItemChanged.connect(self._on_date_changed)
        body.addWidget(self._date_list)

        # 右侧对话内容 (改用 QListWidget 支持自定义绘制)
        self._content_view = QListWidget()
        self._content_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content_view.setSpacing(4)
        self._content_view.setStyleSheet(TEXTEDIT_QSS + SCROLLBAR_QSS + f"""
            QListWidget {{
                background: {_COLOR_BG};
                border: 1px solid #ddd;
                border-radius: 8px;
                outline: none;
            }}
        """)
        self._bubble_delegate = ChatBubbleDelegate(self._content_view)
        self._content_view.setItemDelegate(self._bubble_delegate)
        body.addWidget(self._content_view)

        # ── 底部工具栏 ──
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)

        toolbar.addStretch()
        refresh_btn = QPushButton("刷新")
        refresh_btn.setStyleSheet(BUTTON_PRIMARY_QSS)
        refresh_btn.clicked.connect(self._refresh)
        toolbar.addWidget(refresh_btn)

        # ── 组装 ──
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 8)
        root.setSpacing(6)
        root.addWidget(header)
        root.addLayout(body)
        root.addLayout(toolbar)

        # ── 拖拽支持 ──
        header.mousePressEvent = self._header_press
        header.mouseMoveEvent = self._header_move
        self._drag_pos: QPoint | None = None

        # ── 自动刷新定时器（3s） ──
        self._auto_refresh = QTimer(self)
        self._auto_refresh.setInterval(3000)
        self._auto_refresh.timeout.connect(self._auto_refresh_tick)

    def showEvent(self, event):
        super().showEvent(event)
        ensure_taskbar_icon(self)
        self._refresh_dates()
        self._auto_refresh.start()

    def hideEvent(self, event):
        self._auto_refresh.stop()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 窗口大小改变时，重新计算气泡尺寸和位置
        self._content_view.doItemsLayout()
        self._content_view.viewport().update()

    # ── 窗口圆角绘制 ──

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, _RADIUS, _RADIUS)
        painter.fillPath(path, QColor(_COLOR_BG))
        painter.setPen(QPen(QColor("#000000"), 1))
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

    # ── 日期列表逻辑 ──

    def _refresh_dates(self):
        """刷新左侧日期列表。"""
        self._date_list.blockSignals(True)
        self._date_list.clear()
        dates = self._store.get_available_dates()
        for d in dates:
            # 显示格式: MM-DD
            parts = d.split("-")
            label = f"{parts[1]}-{parts[2]}" if len(parts) >= 3 else d
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, d)
            self._date_list.addItem(item)

        if self._date_list.count() > 0:
            self._date_list.setCurrentRow(0)
        self._date_list.blockSignals(False)

        # 触发展示第一条，保持原阅读位置
        if self._date_list.currentItem():
            self._refresh_content(self._date_list.currentItem().data(Qt.ItemDataRole.UserRole), preserve_scroll=True)

    def _on_date_changed(self, current, previous):
        if current:
            date_str = current.data(Qt.ItemDataRole.UserRole)
            # 切换日期时，直接滚到底部
            self._refresh_content(date_str, preserve_scroll=False)

    # ── 对话内容渲染 ──

    def _refresh_content(self, date_str: str, preserve_scroll: bool = False):
        """构建气泡项并添加到 QListWidget。"""
        scrollbar = self._content_view.verticalScrollBar()
        was_at_bottom = scrollbar.value() == scrollbar.maximum()
        scroll_pos = scrollbar.value()

        self._content_view.clear()
        records = self._store.query_by_date(date_str)
        
        if not records:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, "info")
            item.setData(Qt.ItemDataRole.UserRole + 2, "暂无对话记录")
            self._content_view.addItem(item)
            return

        for r in records:
            role = r.get("role", "")
            content = r.get("content", "")
            created_at = r.get("created_at", "")
            time_str = ""
            try:
                if "T" in created_at:
                    time_str = created_at.split("T")[1][:5]  # HH:MM
            except Exception:
                pass

            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, role)
            item.setData(Qt.ItemDataRole.UserRole + 1, time_str)
            item.setData(Qt.ItemDataRole.UserRole + 2, content)
            self._content_view.addItem(item)

        # 决定滚动条位置：如果设置了保持滚动位置，且用户之前不在底部，则恢复用户阅读位置
        if preserve_scroll and not was_at_bottom:
            scrollbar.setValue(scroll_pos)
        else:
            # 否则（在底部、新切换日期或初次打开），滚动到底部
            self._content_view.scrollToBottom()

    # ── 刷新 ──

    def _refresh(self):
        """刷新按钮：重新加载日期列表和当前日期内容（保持滚动位置）。"""
        self._refresh_dates()

    def _auto_refresh_tick(self):
        """定时器回调：仅刷新当前选中日期的对话内容（不重建日期列表，不重置滚动条）。"""
        item = self._date_list.currentItem()
        if item:
            self._refresh_content(item.data(Qt.ItemDataRole.UserRole), preserve_scroll=True)

    # ── 关闭即隐藏 ──

    def closeEvent(self, event):
        if event.spontaneous():
            self.hide()
            event.ignore()
        else:
            event.accept()
