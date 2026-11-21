"""桌宠聊天交互组件"""

from pathlib import Path

from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLineEdit,
                               QLabel, QBoxLayout)
from pet.ui.ink_button import InkButton, paint_ink_shadow
from PySide6.QtCore import (Qt, QSize, QTimer, QPoint, QRect, QRectF, Signal,
                            QPropertyAnimation, QEasingCurve,
                            QParallelAnimationGroup, QEvent)
from PySide6.QtGui import QPainter
from pet import theme
from pet.ui.icons import tinted_icon
from pet.ui.qss import (BUBBLE_INPUT_BUSY_QSS, BUBBLE_INPUT_QSS,
                        bubble_button_qss)
from pet.ui.styles import BUBBLE_ROW_CHAT, bubble_column_y

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_ICON_PATH = str(BASE_DIR / "assets" / "icon" / "chat.png")
_COLLAPSE_ICON_PATH = str(BASE_DIR / "assets" / "icon" / "collapse.png")
_RECORDING_ICON_PATH = str(BASE_DIR / "assets" / "icon" / "audio_recording.png")
_SEND_ICON_PATH = str(BASE_DIR / "assets" / "icon" / "ink" / "arrow_right.svg")

_HINT_IDLE = "Enter 发送 · Esc 收起"
_HINT_BUSY = "正在思考，稍后再发"


class ChatBubble(QWidget):
    """聊天交互气泡 - 悬停桌宠时显示，点击展开输入框。"""

    chat_submitted = Signal(str)  # 用户提交消息时发出
    enter_intercept = Signal(bool)  # 请求开启/关闭全局回车拦截

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
        # 语音完成后自动收回（用户不提交时）
        self._voice_auto_collapse = QTimer(self)
        self._voice_auto_collapse.setSingleShot(True)
        self._voice_auto_collapse.timeout.connect(self._on_voice_auto_collapse)

        self._input.installEventFilter(self)

        self.hide()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(2)

        row = QWidget()
        self._layout = QHBoxLayout(row)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)

        # 聊天按钮（收起态显示）
        self._btn = InkButton(radius=theme.radius_for(32))
        self._btn.setFixedSize(32, 32)
        self._btn.setIcon(tinted_icon(_ICON_PATH, theme.INK, 22))
        self._btn.setIconSize(QSize(22, 22))
        self._btn.setStyleSheet(bubble_button_qss(theme.BUBBLE_CHAT))
        self._btn.clicked.connect(self._toggle_expand)
        self._layout.addWidget(self._btn)

        # 输入框（展开态显示）
        self._input = QLineEdit()
        self._input.setPlaceholderText("\u8bf4\u70b9\u4ec0\u4e48...")
        self._input.setMinimumWidth(0)
        self._input.setMaximumWidth(180)
        self._input.setMinimumHeight(28)
        self._input.setStyleSheet(BUBBLE_INPUT_QSS)
        self._input.returnPressed.connect(self.submit)
        self._input.textChanged.connect(self._on_input_changed)
        self._input.hide()
        self._layout.addWidget(self._input)

        # 发送按钮，展开态显示
        self._send = InkButton(radius=theme.radius_for(27))
        self._send.setFixedSize(27, 27)
        self._send.setIcon(tinted_icon(_SEND_ICON_PATH, theme.INK, 14))
        self._send.setIconSize(QSize(14, 14))
        self._send.setStyleSheet(
            bubble_button_qss(theme.BUBBLE_CHAT, radius=theme.radius_for(27)))
        self._send.clicked.connect(self.submit)
        self._send.hide()
        self._layout.addWidget(self._send)

        self._hint = QLabel(_HINT_IDLE)
        self._hint.setStyleSheet(
            f"font-size: 10.5px; color: {theme.INK_MUTED}; background: transparent;")
        self._hint.setIndent(36)
        self._hint.hide()
        outer.addWidget(row)
        outer.addWidget(self._hint)

        self.adjustSize()

    def paintEvent(self, event):
        if not self._input.isHidden():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            top_left = self._input.mapTo(self, QPoint(0, 0))
            paint_ink_shadow(
                painter, QRectF(QRect(top_left, self._input.size())),
                theme.RADIUS_LG)
            painter.end()
        super().paintEvent(event)

    def eventFilter(self, obj, event):
        if obj is self._input:
            if event.type() == QEvent.Type.FocusOut and self._expanded:
                # 失去焦点后 2s 自动缩回
                self._collapse_timer.start(2000)
            elif event.type() == QEvent.Type.FocusIn:
                self._collapse_timer.stop()
            elif (event.type() == QEvent.Type.KeyPress
                  and event.key() == Qt.Key.Key_Escape):
                # Esc 只收起，撤销语音期开的回车拦截
                self._voice_auto_collapse.stop()
                self.enter_intercept.emit(False)
                self._collapse()
                self.hide_bubble()
                return True
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
        if self._collapse_anim is not None:
            self._collapse_anim.stop()
        self._expanded = True
        self._input.setMinimumWidth(0)
        self._input.setMaximumWidth(180)
        self._input.show()
        self._btn.setIcon(tinted_icon(_COLLAPSE_ICON_PATH, theme.INK, 22))

        self._send.show()
        self._hint.show()

        self._expand_anim = QPropertyAnimation(self._input, b"minimumWidth")
        self._expand_anim.setDuration(200)
        self._expand_anim.setStartValue(0)
        self._expand_anim.setEndValue(180)
        self._expand_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        if self._busy:
            self._collapse_timer.start(5000)
        else:
            self._expand_anim.finished.connect(lambda: self._input.setFocus())
        self._expand_anim.valueChanged.connect(lambda _: self.update())
        self._expand_anim.start()

    def _collapse(self):
        if not self._expanded:
            return
        if self._expand_anim is not None:
            self._expand_anim.stop()
        self._expanded = False
        self._collapse_timer.stop()  # 手动收起时取消定时器
        self._btn.setIcon(tinted_icon(_ICON_PATH, theme.INK, 22))

        self._collapse_anim = QPropertyAnimation(self._input, b"minimumWidth")
        self._collapse_anim.setDuration(150)
        self._collapse_anim.setStartValue(self._input.width())
        self._collapse_anim.setEndValue(0)
        self._collapse_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._collapse_anim.finished.connect(self._on_collapse_done)
        self._collapse_anim.valueChanged.connect(lambda _: self.update())
        self._collapse_anim.start()

    def _on_collapse_done(self):
        # 收起只隐藏，不清空：未提交的输入要保留到下次展开
        self._input.hide()
        self._send.hide()
        self._hint.hide()
        self._input.setMinimumWidth(0)
        self._input.setMaximumWidth(180)

        self.layout().activate()
        self.adjustSize()

    def submit(self):
        """提交输入框内容，回车与语音热键共用此入口。"""
        # 取消语音自动收回定时器
        self._voice_auto_collapse.stop()
        self.enter_intercept.emit(False)
        text = self._input.text().strip()
        if not text:
            return
        if self._busy:
            # 忙碌时不提交，草稿留在输入框
            self._hint.setText(_HINT_BUSY)
            return
        self.chat_submitted.emit(text)
        self._input.clear()
        self._collapse()
        self.hide_bubble()

    def set_recording_icon(self, recording: bool):
        """切换按钮图标：录音中显示 audio_recording 图标。"""
        path = _RECORDING_ICON_PATH if recording else _ICON_PATH
        self._btn.setIcon(tinted_icon(path, theme.INK, 22))

    def show_voice_input(self):
        """自动展开输入框供语音输入。"""
        self.show_bubble()
        if not self._expanded:
            self._expand()
        self._input.setFocus()

    def set_voice_text(self, text: str):
        """实时更新识别文字到输入框。"""
        self._input.setText(text)
        if not self._expanded:
            self._expand()
        # 每次收到语音文字都重置自动收回定时器
        self._voice_auto_collapse.start(5000)

    def finalize_voice_text(self, text: str):
        """语音识别最终结果：填入文字并激活焦点供用户编辑。"""
        self._input.setText(text)
        if not self._expanded:
            self._expand()
        self.raise_()
        self.activateWindow()
        self._input.setFocus()
        # 开启全局回车拦截
        self.enter_intercept.emit(True)
        self._voice_auto_collapse.start(5000)

    def _on_voice_auto_collapse(self):
        """语音完成后超时未提交，自动收回气泡。"""
        self.enter_intercept.emit(False)
        if self._expanded:
            self._collapse()
            self.hide_bubble()

    def _on_input_changed(self):
        """用户手动编辑时重置语音自动收回定时器。"""
        if self._voice_auto_collapse.isActive():
            self._voice_auto_collapse.start(5000)


    def show_bubble(self):
        self.cancel_hide()
        if self._hide_anim and self._hide_anim.state() == QPropertyAnimation.State.Running:
            self._hide_anim.stop()
            self.setWindowOpacity(1.0)
            self._follow_timer.start(50)
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
            self._input.setStyleSheet(BUBBLE_INPUT_BUSY_QSS)
            self._input.setPlaceholderText("正在思考...")
        else:
            self._input.setEnabled(True)
            self._input.setStyleSheet(BUBBLE_INPUT_QSS)
            self._input.setPlaceholderText("说点什么...")
            self._hint.setText(_HINT_IDLE)

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


    def _follow_pet(self):
        if self._pet_window and self.isVisible():
            self._update_position()

    def _update_position(self):
        pet_geo = self._pet_window.geometry()
        # 取宠物所在屏
        screen = self._pet_window.screen() or self.screen()
        geo = screen.availableGeometry() if screen else None
        screen_right = geo.right() if geo else 9999

        bw = self.width()
        y = bubble_column_y(pet_geo.top(), BUBBLE_ROW_CHAT, self.height(), geo)

        if pet_geo.right() + bw + 10 > screen_right:
            x = pet_geo.left() - bw + 20  # 左侧
            self._layout.setDirection(QBoxLayout.Direction.RightToLeft)
            self._hint.setIndent(31)
        else:
            x = pet_geo.right() - 20      # 右侧
            self._layout.setDirection(QBoxLayout.Direction.LeftToRight)
            self._hint.setIndent(36)
        if geo:
            x = max(geo.left(), min(x, geo.right() - bw))
        self.move(x, y)


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
