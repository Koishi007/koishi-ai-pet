from PySide6.QtWidgets import QLabel, QVBoxLayout
from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent
from pet.ui.base_window import TransparentWindow
from pet.ui.pet_animations_player import PetAnimator
from config import config


class PetWindow(TransparentWindow):
    def __init__(self):
        super().__init__()
        self._setup_ui()
        self._old_pos = None

    def _setup_ui(self):
        self.setFixedSize(config.PET_WIDTH, config.PET_HEIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.pet_label = QLabel()
        self.pet_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.pet_label)

        # 初始化桌宠动画播放器
        self.pet_anim = PetAnimator(self, self.pet_label, parent=self)

        # 尝试播放 idle 动画，无素材时回退到 emoji
        if not self.pet_anim.play("idle"):
            self._use_emoji_fallback()

    def play_action(self, action: str, loop: bool = True) -> bool:
        """播放指定动作动画，无素材时回退到 emoji。"""
        if self.pet_anim.play(action, loop=loop):
            return True
        self._use_emoji_fallback()
        return False

    def _use_emoji_fallback(self):
        """无精灵素材时显示 emoji 占位。"""
        self.pet_label.setText("\U0001f436")
        font = self.pet_label.font()
        font.setPointSize(48)
        self.pet_label.setFont(font)

    # ── 拖拽移动 ──────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._old_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._old_pos is not None:
            delta = event.globalPosition().toPoint() - self._old_pos
            self.move(self.pos() + delta)
            self._old_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._old_pos = None
