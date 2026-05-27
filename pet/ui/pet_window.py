from PySide6.QtWidgets import QLabel, QVBoxLayout
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QMouseEvent
from pet.ui.base_window import TransparentWindow
from pet.ui.pet_animations import PetAnimator
from pet.action import PetActions, ActionQueue
from config import config


class PetWindow(TransparentWindow):
    def __init__(self):
        super().__init__()
        self._setup_ui()
        self._grab_local: QPoint | None = None
        self._chat_bubble = None

    def set_chat_bubble(self, chat_bubble):
        """注入 ChatBubble 引用。"""
        self._chat_bubble = chat_bubble

    def enterEvent(self, event):
        """鼠标进入桌宠区域时显示聊天按钮。"""
        if self._chat_bubble and self._grab_local is None:
            self._chat_bubble.show_bubble()
        super().enterEvent(event)

    def leaveEvent(self, event):
        """鼠标离开桌宠区域时延迟隐藏。"""
        if self._chat_bubble:
            self._chat_bubble.schedule_hide()
        super().leaveEvent(event)

    def _setup_ui(self):
        self.setFixedSize(config.PET_WIDTH, config.PET_HEIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.pet_label = QLabel()
        self.pet_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.pet_label)

        self.pet_anim = PetAnimator(self.pet_label, parent=self)
        self.pet_actions = PetActions(self, self.pet_anim, parent=self)
        self.action_queue = ActionQueue(self.pet_actions, parent=self)

        self.pet_actions.gravity.falling_started.connect(self._on_falling_started)
        self.pet_actions.gravity.landed.connect(self._on_landed)

        # 初始位置：屏幕底部居中
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - config.PET_WIDTH) // 2
            y = geo.bottom() - config.PET_HEIGHT
            self.move(x, y)

        if not self.pet_anim.play("idle"):
            self._use_emoji_fallback()

    def _use_emoji_fallback(self):
        self.pet_label.setText("\U0001f436")
        font = self.pet_label.font()
        font.setPointSize(48)
        self.pet_label.setFont(font)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._grab_local = QPoint(62, 20)
            if self._chat_bubble:
                self._chat_bubble.hide_bubble()
            self.pet_actions.gravity.enable(False)
            self.action_queue.pause()
            self.action_queue.clear()
            self.pet_actions.caught()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._grab_local is not None:
            new_pos = event.globalPosition().toPoint() - self._grab_local
            self.move(new_pos)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._grab_local = None
        self.action_queue.resume()
        self.pet_actions.gravity.enable(True)

    def _on_falling_started(self):
        self.action_queue.pause()

    def _on_landed(self):
        self.action_queue.resume()

    # ── 队列控制接口 ──

    def queue_enqueue(self, method: str, *args, **kwargs):
        self.action_queue.enqueue(method, *args, **kwargs)

    def queue_enqueue_action(self, name: str, args: tuple, kwargs: dict):
        self.action_queue.enqueue(name, *args, **kwargs)

    def queue_start(self):
        self.action_queue.start()

    def queue_stop(self):
        self.action_queue.stop()

    def queue_clear(self):
        self.action_queue.clear()

    def shutdown(self):
        self.pet_anim.stop()
        self.action_queue.clear()
        self.pet_actions.gravity.enable(False)
