"""文件气泡 - 拖入文件后显示摘要与动作按钮，单例复用。"""

from __future__ import annotations

import logging
import os
import threading

from PySide6.QtCore import (QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation,
                            Qt, QTimer, Signal, Slot)
from PySide6.QtWidgets import (QBoxLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                               QWidget)

from pet.config import config
from pet.file_intake import KIND_LABELS, FileRef, dir_summary
from pet.ui.debounce import Debounce
from pet.tools.registry import TOOL_REGISTRY
from pet.ui.styles import BUBBLE_ROW_CHAT, bubble_column_y

logger = logging.getLogger(__name__)

# 核心动作固定显示，工具动作由注册表声明
CORE_ACTIONS = (("taste", "尝一口"), ("read", "看一看"))


_PANEL_QSS = (
    "QWidget#filePanel {"
    "  background: rgba(255,255,255,225);"
    "  border: 1px solid #dcdcdc;"
    "  border-radius: 12px;"
    "}"
)
_ACTION_QSS = (
    "QPushButton {"
    "  background: rgba(255,255,255,240);"
    "  border: 1px solid #ccc;"
    "  border-radius: 10px;"
    "  padding: 4px 10px;"
    "  color: #333;"
    "}"
    "QPushButton:hover { background: rgba(240,240,250,255); }"
    "QPushButton:disabled {"
    "  color: #b4b4b4;"
    "  background: rgba(248,248,248,200);"
    "  border-color: #e4e4e4;"
    "}"
)
_CANCEL_QSS = (
    "QPushButton {"
    "  background: transparent;"
    "  border: 1px solid #d8d8d8;"
    "  border-radius: 9px;"
    "  padding: 2px 8px;"
    "  color: #888;"
    "  font-size: 12px;"
    "}"
    "QPushButton:hover { color: #444; border-color: #bbb; }"
)
_TITLE_QSS = "color:#666; font-size:12px;"
_ROW_QSS = "color:#333; font-size:12px;"


def _format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def describe_ref(ref: FileRef, dir_note: str = "") -> str:
    """一行摘要：名称 · 类型 · 大小；目录的条目摘要由后台扫描后补上。"""
    parts = [ref.name, KIND_LABELS.get(ref.kind, ref.kind)]
    if ref.kind == "dir":
        parts.append(dir_note or "扫描中…")
    else:
        parts.append(_format_size(ref.size))
    return " · ".join(parts)


def _dir_note(count: int, names: list[str]) -> str:
    """目录摘要：条目数与前若干名字。"""
    if not count:
        return "空文件夹"
    note = f"{count} 项"
    if names:
        note = f"{note}：{'、'.join(names)}"
    return note


def _button_label(text: str, reason: str) -> str:
    return f"{text}（{reason}）" if reason else text


_BUSY_NAME_MAX = 3


def _busy_note(paths: list[str]) -> str:
    """忙态提示的文件清单：最多列 3 个名字，超出补总数。"""
    names = [os.path.basename(path) for path in paths[:_BUSY_NAME_MAX]]
    listed = "、".join(names)
    if len(paths) > _BUSY_NAME_MAX:
        listed = f"{listed} 等 {len(paths)} 个"
    return f"「{listed}」等你忙完再拖一次"


class FileBubble(QWidget):
    """文件气泡：显示拖入项与动作按钮，超时或收起时通知装配层。"""

    action_chosen = Signal(str, object)
    idle_timeout = Signal()
    dir_scanned = Signal(int, str)

    def __init__(self, pet_window, parent=None):
        super().__init__(parent)
        self._pet_window = pet_window
        self._refs: tuple[FileRef, ...] = ()
        self._busy_hint = False
        self._rows: list[QLabel] = []
        self._action_ids: tuple[tuple[str, str, bool], ...] = ()
        self._dir_token = 0
        self._action_guard = Debounce()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._setup_ui()
        self._show_anim: QParallelAnimationGroup | None = None
        self._hide_anim: QPropertyAnimation | None = None
        self._follow_timer = QTimer(self)
        self._follow_timer.timeout.connect(self._follow_pet)
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._on_idle)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_actions)
        self.dir_scanned.connect(self._on_dir_scanned)

        self.hide()

    def _setup_ui(self):
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(6, 6, 6, 6)

        self._panel = QWidget()
        self._panel.setObjectName("filePanel")
        self._panel.setStyleSheet(_PANEL_QSS)
        self._panel_layout = QVBoxLayout(self._panel)
        self._panel_layout.setContentsMargins(10, 8, 10, 8)
        self._panel_layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(8)
        self._title = QLabel("让恋恋")
        self._title.setStyleSheet(_TITLE_QSS)
        header.addWidget(self._title)
        header.addStretch(1)

        self._cancel_button = QPushButton("取消")
        self._cancel_button.setStyleSheet(_CANCEL_QSS)
        self._cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_button.clicked.connect(self._on_cancel)
        header.addWidget(self._cancel_button)
        self._panel_layout.addLayout(header)

        self._body = QVBoxLayout()
        self._body.setSpacing(2)
        self._panel_layout.addLayout(self._body)

        self._buttons = QHBoxLayout()
        self._buttons.setSpacing(6)
        self._panel_layout.addLayout(self._buttons)

        self._layout.addWidget(self._panel)

    # 公开接口

    def show_files(self, refs: tuple[FileRef, ...], timeout_s: int | None = None):
        """显示文件摘要与动作按钮；重复调用复用同一实例，内容整体替换。"""
        self._show(refs, busy=False, timeout_s=timeout_s)

    def show_busy(self, paths: list[str]):
        """忙态放下：复用同一窗口提示收不下，不提供动作。"""
        self._show((), busy=True, paths=paths)

    def _show(self, refs: tuple[FileRef, ...], busy: bool, timeout_s: int | None = None,
              paths: list[str] | None = None):
        self._busy_hint = busy
        self._refs = tuple(refs)
        self._title.setText("现在忙，先收不下啦" if busy else "这些给你")
        self._render_body(paths=paths)
        self._render_buttons()
        self._show_bubble()
        seconds = config.FILE_DROP_BUBBLE_TIMEOUT_S if timeout_s is None else timeout_s
        if seconds > 0:
            self._idle_timer.start(int(seconds * 1000))
        self._refresh_timer.start(1000)

    def hide_bubble(self):
        self._idle_timer.stop()
        self._refresh_timer.stop()
        if not self.isVisible():
            return
        if self._show_anim and self._show_anim.state() == QParallelAnimationGroup.State.Running:
            self._show_anim.stop()
        self._follow_timer.stop()
        self._hide_anim = QPropertyAnimation(self, b"windowOpacity")
        self._hide_anim.setDuration(150)
        self._hide_anim.setStartValue(self.windowOpacity())
        self._hide_anim.setEndValue(0.0)
        self._hide_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._hide_anim.finished.connect(self.hide)
        self._hide_anim.start()

    # 渲染

    def _render_body(self, paths: list[str] | None = None):
        while self._body.count():
            item = self._body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = []
        if paths is not None:
            row = QLabel(_busy_note(paths))
            row.setStyleSheet(_ROW_QSS)
            row.setWordWrap(True)
            self._body.addWidget(row)
            self._rows.append(row)
            return
        for ref in self._refs:
            row = QLabel(describe_ref(ref))
            row.setStyleSheet(_ROW_QSS)
            row.setWordWrap(True)
            self._body.addWidget(row)
            self._rows.append(row)
        self._scan_dirs()

    def _scan_dirs(self):
        """目录条目摘要交给后台线程读，避免网络路径或无响应盘符卡住界面。"""
        self._dir_token += 1
        token = self._dir_token
        targets = [(index, ref.path) for index, ref in enumerate(self._refs)
                   if ref.kind == "dir"]
        if not targets:
            return

        def worker() -> None:
            for index, path in targets:
                if token != self._dir_token:
                    return
                count, names = dir_summary(path)
                self.dir_scanned.emit(index, _dir_note(count, names))

        threading.Thread(target=worker, daemon=True, name="file-dir-scan").start()

    @Slot(int, str)
    def _on_dir_scanned(self, index: int, note: str):
        if index < len(self._rows):
            self._rows[index].setText(describe_ref(self._refs[index], note))

    def _available_actions(self) -> list[tuple[str, str, bool]]:
        """返回（动作 id, 按钮文案, 是否可用）：核心两条在前，工具动作在后。

        依赖图片的核心动作在视觉关闭时置灰并标注原因（设计文档 §4.4）；
        `accepts` 与 kind 不匹配，或内容读取关闭且动作需要内容时，工具动作不出现（设计文档 §5.1）。
        """
        kinds = {ref.kind for ref in self._refs}
        has_text = "text" in kinds
        has_image = "image" in kinds
        read_content = bool(config.FILE_DROP_READ_CONTENT)
        vision = bool(config.VISION_ENABLED)

        taste_usable = read_content and (vision or kinds != {"image"})
        read_usable = read_content and (has_text or (has_image and vision))
        if not read_content:
            taste_reason = read_reason = "内容读取已关闭"
        else:
            taste_reason = "" if taste_usable else "视觉通道已关闭"
            read_reason = "" if read_usable else (
                "视觉通道已关闭" if has_image and not vision else "没有可读文本")

        actions: list[tuple[str, str, bool]] = [
            ("taste", _button_label(CORE_ACTIONS[0][1], taste_reason), taste_usable),
            ("read", _button_label(CORE_ACTIONS[1][1], read_reason), read_usable),
        ]
        for action in TOOL_REGISTRY.file_actions():
            accepts = action.get("accepts", "any")
            if not read_content and action.get("needs_content", True):
                usable = False
            elif accepts == "text":
                usable = has_text
            elif accepts == "image":
                usable = has_image
            else:
                usable = True
            if usable:
                actions.append((f"tool:{action['tool']}:{action['id']}", action["label"], True))
        return actions

    def _render_buttons(self):
        actions = () if self._busy_hint else tuple(self._available_actions())
        signature = tuple(actions)
        if signature == self._action_ids:
            return
        self._action_ids = signature

        while self._buttons.count():
            item = self._buttons.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for action_id, label, usable in actions:
            button = QPushButton(label)
            button.setStyleSheet(_ACTION_QSS)
            button.setEnabled(usable)
            button.clicked.connect(lambda _=False, aid=action_id: self._on_action(aid))
            self._buttons.addWidget(button)

        self._panel.adjustSize()
        self.adjustSize()
        self._update_position()

    def _refresh_actions(self):
        """气泡可见期间重读注册表，工具加载完成后按钮自行出现。"""
        if self.isVisible():
            self._render_buttons()

    # 交互

    def _on_cancel(self):
        """取消：收起气泡，不读内容、不发请求。"""
        logger.info("[FileBubble] cancelled")
        self.hide_bubble()

    def _on_action(self, action_id: str):
        if not self._action_guard.ready():
            return
        self._idle_timer.stop()
        refs = self._refs
        self.hide_bubble()
        logger.info(f"[FileBubble] action chosen: {action_id}")
        self.action_chosen.emit(action_id, refs)

    def _on_idle(self):
        self.hide_bubble()
        logger.info("[FileBubble] idle timeout")
        self.idle_timeout.emit()

    # 跟随与显示

    def _follow_pet(self):
        if self._pet_window and self.isVisible():
            self._update_position()

    def _update_position(self):
        pet_geo = self._pet_window.geometry()
        screen = self._pet_window.screen() or self.screen()
        geo = screen.availableGeometry() if screen else None
        screen_right = geo.right() if geo else 9999

        width = self.width()
        y = bubble_column_y(pet_geo.top(), BUBBLE_ROW_CHAT, self.height(), geo)
        if pet_geo.right() + width + 10 > screen_right:
            x = pet_geo.left() - width + 20
            self._layout.setDirection(QBoxLayout.Direction.RightToLeft)
        else:
            x = pet_geo.right() - 20
            self._layout.setDirection(QBoxLayout.Direction.LeftToRight)
        if geo:
            x = max(geo.left(), min(x, geo.right() - width))
            y = max(geo.top(), min(y, geo.bottom() - self.height()))
        self.move(x, y)

    def _show_bubble(self):
        if self._hide_anim and self._hide_anim.state() == QPropertyAnimation.State.Running:
            self._hide_anim.stop()
            # 淡出被打断：复原透明度与跟随，下面的提前返回不再重建这两项
            self.setWindowOpacity(1.0)
            self._follow_timer.start(50)
        if self.isVisible():
            return
        self._update_position()
        offset = -15 if self.pos().x() < self._pet_window.geometry().center().x() else 15
        start_pos = self.pos() + QPoint(offset, 0)
        final_pos = self.pos()
        self.move(start_pos)
        self.setWindowOpacity(0.0)
        self.show()
        self._follow_timer.start(50)

        self._show_anim = QParallelAnimationGroup(self)
        pos_anim = QPropertyAnimation(self, b"pos")
        pos_anim.setDuration(250)
        pos_anim.setStartValue(start_pos)
        pos_anim.setEndValue(final_pos)
        pos_anim.setEasingCurve(QEasingCurve.Type.OutBack)
        opacity_anim = QPropertyAnimation(self, b"windowOpacity")
        opacity_anim.setDuration(200)
        opacity_anim.setStartValue(0.0)
        opacity_anim.setEndValue(1.0)
        opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._show_anim.addAnimation(pos_anim)
        self._show_anim.addAnimation(opacity_anim)
        self._show_anim.start()

    def enterEvent(self, event):
        self._idle_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.isVisible() and config.FILE_DROP_BUBBLE_TIMEOUT_S > 0:
            self._idle_timer.start(int(config.FILE_DROP_BUBBLE_TIMEOUT_S * 1000))
        super().leaveEvent(event)
