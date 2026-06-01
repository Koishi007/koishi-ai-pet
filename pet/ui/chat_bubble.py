"""桌宠聊天交互组件 - 悬停显示，点击展开输入框。"""

from pathlib import Path

from PySide6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLineEdit, QBoxLayout
from PySide6.QtCore import Qt, QTimer, QPoint, Signal, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QEvent
from PySide6.QtGui import QFont, QIcon
from config import config

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class ChatBubble(QWidget):
    """聊天交互气泡 - 悬停桌宠时显示，点击展开输入框。"""

    chat_submitted = Signal(str)  # 用户提交消息时发出

    _INPUT_STYLE = (
        "QLineEdit {"
        "  background: rgba(255,255,255,230);"
        "  border: 1px solid #ccc;"
        "  border-radius: 14px;"
        "  padding: 2px 10px;"
        "  font-size: 13px;"
        "}"
    )
    _INPUT_STYLE_BUSY = (
        "QLineEdit {"
        "  background: rgba(230,230,230,200);"
        "  border: 1px solid #ddd;"
        "  border-radius: 14px;"
        "  padding: 2px 10px;"
        "  font-size: 13px;"
        "  color: #999;"
        "}"
    )

    def __init__(self, pet_window, parent=None):
        super().__init__(parent)
        self._pet_window = pet_window
        self._expanded = False
        self._busy = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._setup_ui()
        self._show_anim: QParallelAnimationGroup | None = None
        self._hide_anim: QPropertyAnimation | None = None
        self._expand_anim: QPropertyAnimation | None = None
        self._collapse_anim: QPropertyAnimation | None = None
        self._follow_timer = QTimer(self)
        self._follow_timer.timeout.connect(self._follow_pet)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._try_hide)
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(self._on_auto_collapse)

        self._input.installEventFilter(self)

        self.hide()

    def _setup_ui(self):
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(4)

        # 聊天按钮（收起态显示）
        self._btn = QPushButton()
        self._btn.setIcon(QIcon(str(BASE_DIR / "assets" / "icon" / "chat.png")))
        self._btn.setIconSize(self._btn.size())
        self._btn.setFixedSize(32, 32)
        self._btn.setStyleSheet(
            "QPushButton {"
            "  background: rgba(255,255,255,220);"
            "  border: 1px solid #ccc;"
            "  border-radius: 16px;"
            "}"
            "QPushButton:hover {"
            "  background: rgba(240,240,255,240);"
            "  border-color: #aaa;"
            "}"
        )
        self._btn.clicked.connect(self._toggle_expand)
        self._layout.addWidget(self._btn)

        # 输入框（展开态显示）
        self._input = QLineEdit()
        self._input.setPlaceholderText("\u8bf4\u70b9\u4ec0\u4e48...")
        self._input.setMinimumWidth(0)
        self._input.setMaximumWidth(180)
        self._input.setMinimumHeight(28)
        self._input.setStyleSheet(self._INPUT_STYLE)
        self._input.returnPressed.connect(self._on_submit)
        self._input.hide()
        self._layout.addWidget(self._input)

        self.adjustSize()

    def eventFilter(self, obj, event):
        if obj is self._input:
            if event.type() == QEvent.Type.FocusOut and self._expanded:
                # 失去焦点后 2s 自动缩回
                self._collapse_timer.start(2000)
            elif event.type() == QEvent.Type.FocusIn:
                self._collapse_timer.stop()
        return super().eventFilter(obj, event)

    def _on_auto_collapse(self):
        """失焦定时器触发：收起输入框 + 隐藏整个气泡。"""
        self._collapse()
        self.hide_bubble()

    def _toggle_expand(self):
        if self._expanded:
            self._collapse()
        else:
            self._expand()

    def _expand(self):
        self._expanded = True
        self._input.setMinimumWidth(0)
        self._input.setMaximumWidth(180)
        self._input.show()
        self._btn.setText("\u2715")

        self._expand_anim = QPropertyAnimation(self._input, b"minimumWidth")
        self._expand_anim.setDuration(200)
        self._expand_anim.setStartValue(0)
        self._expand_anim.setEndValue(180)
        self._expand_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        if self._busy:
            self._collapse_timer.start(5000)
        else:
            self._expand_anim.finished.connect(lambda: self._input.setFocus())
        self._expand_anim.start()

    def _collapse(self):
        if not self._expanded:
            return
        self._expanded = False
        self._collapse_timer.stop()  # 手动收起时取消定时器
        self._btn.setText("\U0001f4ac")

        self._collapse_anim = QPropertyAnimation(self._input, b"minimumWidth")
        self._collapse_anim.setDuration(150)
        self._collapse_anim.setStartValue(self._input.width())
        self._collapse_anim.setEndValue(0)
        self._collapse_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._collapse_anim.finished.connect(self._on_collapse_done)
        self._collapse_anim.start()

    def _on_collapse_done(self):
        self._input.hide()
        self._input.clear()
        self._input.setMinimumWidth(0)
        self._input.setMaximumWidth(180)
        self.adjustSize()

    def _on_submit(self):
        text = self._input.text().strip()
        if text:
            self.chat_submitted.emit(text)
            self._collapse()
            self.hide_bubble()

    # ── 显示/隐藏 ──

    def show_bubble(self):
        self.cancel_hide()
        if self._hide_anim and self._hide_anim.state() == QPropertyAnimation.State.Running:
            self._hide_anim.stop()
        if self.isVisible():
            return
        self._update_position()
        on_left = self.pos().x() < self._pet_window.geometry().center().x()
        offset = -15 if on_left else 15
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

    def set_busy(self, busy: bool):
        self._busy = busy
        if busy:
            self._input.setEnabled(False)
            self._input.setStyleSheet(self._INPUT_STYLE_BUSY)
            self._input.setPlaceholderText("正在思考...")
        else:
            self._input.setEnabled(True)
            self._input.setStyleSheet(self._INPUT_STYLE)
            self._input.setPlaceholderText("说点什么...")

    def hide_bubble(self):
        if not self.isVisible():
            return
        if self._show_anim and self._show_anim.state() == QParallelAnimationGroup.State.Running:
            self._show_anim.stop()
        self._follow_timer.stop()
        self._collapse()
        self._hide_anim = QPropertyAnimation(self, b"windowOpacity")
        self._hide_anim.setDuration(150)
        self._hide_anim.setStartValue(self.windowOpacity())
        self._hide_anim.setEndValue(0.0)
        self._hide_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._hide_anim.finished.connect(self._on_hide_done)
        self._hide_anim.start()

    def _on_hide_done(self):
        self.hide()
        self.setWindowOpacity(1.0)

    def schedule_hide(self):
        self._hide_timer.start(500)

    def cancel_hide(self):
        self._hide_timer.stop()

    def _try_hide(self):
        if not self.underMouse() and not self._expanded:
            self.hide_bubble()

    # ── 跟随桌宠 ──

    def _follow_pet(self):
        if self._pet_window and self.isVisible():
            self._update_position()

    def _update_position(self):
        pet_geo = self._pet_window.geometry()
        screen = self.screen()
        if screen:
            screen_right = screen.availableGeometry().right()
        else:
            screen_right = 9999

        bw = self.width()
        y = pet_geo.top() + 10

        if pet_geo.right() + bw + 10 > screen_right:
            x = pet_geo.left() - bw + 20  # 左侧
            self._layout.setDirection(QBoxLayout.Direction.RightToLeft)
        else:
            x = pet_geo.right() - 20      # 右侧
            self._layout.setDirection(QBoxLayout.Direction.LeftToRight)
        self.move(x, y)

    # ── 鼠标事件 ──

    def enterEvent(self, event):
        self.cancel_hide()
        if self._busy and self._expanded:
            self._collapse_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self._expanded:
            self.schedule_hide()
        if self._busy and self._expanded:
            self._collapse_timer.start(2000)
        super().leaveEvent(event)
