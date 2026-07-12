"""桌宠音乐控制气泡 - 操控系统媒体播放"""

import random
from pathlib import Path

from PySide6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QBoxLayout
from PySide6.QtCore import Qt, QSize, QTimer, QPoint, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup
from PySide6.QtGui import QIcon

try:
    from pynput.keyboard import Key, Controller as KeyboardController
    _KEY_PREV = Key.media_previous
    _KEY_PLAY = Key.media_play_pause
    _KEY_NEXT = Key.media_next
    _HAS_PYNPUT = True
except ImportError:
    _KEY_PREV = _KEY_PLAY = _KEY_NEXT = None
    _HAS_PYNPUT = False

BASE_DIR = Path(__file__).resolve().parent.parent.parent

_PANEL_STYLE = (
    "QWidget#musicPanel {"
    "  background: rgba(255,255,255,220);"
    "  border-radius: 16px;"
    "}"
)
_ICON_BTN_STYLE = (
    "QPushButton {"
    "  background: transparent;"
    "  border: none;"
    "  border-radius: 4px;"
    "}"
    "QPushButton:hover {"
    "  background: rgba(0,0,0,30);"
    "}"
)

_PREV_RESPONSES = ["上一首会好听些吗…", "切过去了…", "上一首…", "之前的歌…"]
_PAUSE_RESPONSES = ["还想再听…", "不听了吗…", "暂停了…", "不听了…"]
_RESUME_RESPONSES = ["继续听吧…", "这个还不错…", "嗯…继续…", "听音乐…开心…"]
_NEXT_RESPONSES  = ["这首不好听嘛？", "这首不太喜欢呢…", "下一首会更好嘛？", "下一首…"]


class MusicBubble(QWidget):
    """音乐控制气泡 - 悬停桌宠时显示音乐按钮，展开后弹出三键播放器。"""

    def __init__(self, pet_window, parent=None):
        super().__init__(parent)
        self._pet_window = pet_window
        self._expanded = False
        self._is_paused = False  # 播放/暂停图标切换
        self._keyboard = KeyboardController() if _HAS_PYNPUT else None

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
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._try_hide)
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(self._on_auto_collapse)

        self.hide()

    def _setup_ui(self):
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(4)

        # 音乐按钮（收起态显示）
        self._btn = QPushButton()
        self._btn.setFixedSize(32, 32)
        self._btn.setIcon(QIcon(str(BASE_DIR / "assets" / "icon" / "music.png")))
        self._btn.setIconSize(QSize(28, 28))
        self._btn.setStyleSheet(
            "QPushButton {"
            "  background: rgba(255,255,255,220);"
            "  border: 1px solid #ccc;"
            "  border-radius: 16px;"
            "}"
            "QPushButton:hover {"
            "  background: rgba(220,230,255,240);"
            "  border-color: #aaa;"
            "}"
        )
        self._btn.clicked.connect(self._toggle_expand)
        self._layout.addWidget(self._btn)

        # 展开面板（圆角矩形容器）
        self._panel = QWidget()
        self._panel.setObjectName("musicPanel")
        self._panel.setStyleSheet(_PANEL_STYLE)
        panel_layout = QHBoxLayout(self._panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        self._btn_prev = QPushButton()
        self._btn_prev.setFixedSize(32, 32)
        self._btn_prev.setIcon(QIcon(str(BASE_DIR / "assets" / "icon" / "music_previous.png")))
        self._btn_prev.setIconSize(QSize(28, 28))
        self._btn_prev.setStyleSheet(_ICON_BTN_STYLE)
        self._btn_prev.clicked.connect(lambda: (self._send_media_key(_KEY_PREV), self._say(_PREV_RESPONSES)))
        panel_layout.addWidget(self._btn_prev)

        self._btn_play = QPushButton()
        self._btn_play.setFixedSize(32, 32)
        self._btn_play.setIcon(QIcon(str(BASE_DIR / "assets" / "icon" / "music_pause.png")))
        self._btn_play.setIconSize(QSize(28, 28))
        self._btn_play.setStyleSheet(_ICON_BTN_STYLE)
        self._btn_play.clicked.connect(self._on_play_clicked)
        panel_layout.addWidget(self._btn_play)

        self._btn_next = QPushButton()
        self._btn_next.setFixedSize(32, 32)
        self._btn_next.setIcon(QIcon(str(BASE_DIR / "assets" / "icon" / "music_next.png")))
        self._btn_next.setIconSize(QSize(28, 28))
        self._btn_next.setStyleSheet(_ICON_BTN_STYLE)
        self._btn_next.clicked.connect(lambda: (self._send_media_key(_KEY_NEXT), self._say(_NEXT_RESPONSES)))
        panel_layout.addWidget(self._btn_next)

        self._panel.hide()
        self._layout.addWidget(self._panel)
        self.adjustSize()

    def set_busy(self, busy: bool):
        self._busy = busy

    def _say(self, messages: list[str]):
        """在 speech bubble 中随机显示一句话（有等待输出时跳过）。"""
        speech_bubble = getattr(self._pet_window, "_speech_bubble", None)
        if speech_bubble is None:
            return
        if speech_bubble._is_active() or speech_bubble._speech_queue:
            return
        speech_bubble.show_text(random.choice(messages), duration=3000)

    def _send_media_key(self, key):
        """通过 pynput 发送系统媒体键。"""
        if self._keyboard is None:
            return
        try:
            self._keyboard.press(key)
            self._keyboard.release(key)
        except Exception:
            pass

    def _on_play_clicked(self):
        """发送播放/暂停键并切换图标。"""
        self._is_paused = not self._is_paused
        icon_name = "music_play.png" if self._is_paused else "music_pause.png"
        self._btn_play.setIcon(QIcon(str(BASE_DIR / "assets" / "icon" / icon_name)))
        self._send_media_key(_KEY_PLAY)
        self._say(_PAUSE_RESPONSES if self._is_paused else _RESUME_RESPONSES)

    def _on_auto_collapse(self):
        """展开后鼠标离开超时自动收起。"""
        self._collapse()
        self.hide_bubble()

    def _toggle_expand(self):
        if self._expanded:
            self._collapse()
        else:
            self._expand()

    def _expand(self):
        self._expanded = True
        self._panel.show()

        # 面板从 0 宽动画展开到自然宽度
        self._panel.setMinimumWidth(0)
        self._panel.setMaximumWidth(0)
        self.adjustSize()
        self._update_position()

        target_w = self._panel.minimumSizeHint().width()
        self._expand_anim = QPropertyAnimation(self._panel, b"maximumWidth")
        self._expand_anim.setDuration(200)
        self._expand_anim.setStartValue(0)
        self._expand_anim.setEndValue(target_w)
        self._expand_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._expand_anim.valueChanged.connect(lambda: self.adjustSize())
        self._expand_anim.start()

    def _collapse(self):
        if not self._expanded:
            return
        self._expanded = False
        if self._expand_anim and self._expand_anim.state() == QPropertyAnimation.State.Running:
            self._expand_anim.stop()
        self._panel.setMaximumWidth(16777215)  # QWIDGETSIZE_MAX
        self._panel.hide()
        self.adjustSize()
        self._update_position()

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
        screen = self.screen()
        if screen:
            screen_right = screen.availableGeometry().right()
        else:
            screen_right = 9999

        bw = self.width()
        y = pet_geo.top() + 85  # Chat(+5) → Feed(+45) → Music(+85)

        if pet_geo.right() + bw + 10 > screen_right:
            x = pet_geo.left() - bw + 20
            self._layout.setDirection(QBoxLayout.Direction.RightToLeft)
        else:
            x = pet_geo.right() - 20
            self._layout.setDirection(QBoxLayout.Direction.LeftToRight)
        self.move(x, y)

    def enterEvent(self, event):
        self.cancel_hide()
        self._collapse_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._expanded:
            self._collapse_timer.start(2000)
        else:
            self.schedule_hide()
        super().leaveEvent(event)
