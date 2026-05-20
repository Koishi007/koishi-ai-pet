"""桌宠动画模块 —— 帧序列播放 + 窗口动画，统一由 PetAnimator 管理。"""

import os
from PySide6.QtCore import Qt, QPoint, QTimer, QPropertyAnimation, QEasingCurve, QObject, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QWidget
from config import config

# 支持的图片后缀
_SUPPORTED_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


class PetAnimator(QObject):
    """桌宠动画控制器"""

    # 非循环帧动画播放完毕时发出，参数为动作名
    animation_finished = Signal(str)

    def __init__(self, window: QWidget, label: QLabel, pet_dir: str | None = None, parent=None):
        super().__init__(parent)
        self._window = window
        self._label = label
        self._pet_dir = pet_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "assets", "actions",
        )

        self._frames: list[QPixmap] = []
        self._current_frame: int = 0
        self._current_action: str = ""
        self._loop: bool = True

        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._next_frame)

        # 帧缓存：{ action_name: [QPixmap, ...] }
        self._cache: dict[str, list[QPixmap]] = {}

        # 存储运行中的 QPropertyAnimation，防止被 GC
        self._win_anims: list[QPropertyAnimation] = []

    # ── 帧动画 ────────────────────────────────────────────

    def play(self, action: str, loop: bool = True, fps: int | None = None) -> bool:
        """播放指定动作的帧动画。"""
        frames = self._load_action(action)
        if not frames:
            return False

        self._frame_timer.stop()
        self._frames = frames
        self._current_action = action
        self._current_frame = 0
        self._loop = loop

        self._label.setPixmap(self._frames[0])

        if len(self._frames) > 1:
            interval = 1000 // (fps or config.PET_FPS)
            self._frame_timer.start(interval)

        return True

    def stop(self):
        """停止帧动画，画面保持在当前帧。"""
        self._frame_timer.stop()

    def has_frames(self, action: str) -> bool:
        """检查指定动作是否有可用帧。"""
        return len(self._load_action(action)) > 0

    def available_actions(self) -> list[str]:
        """返回 pet_dir 下所有有帧图片的动作名称。"""
        if not os.path.isdir(self._pet_dir):
            return []
        actions = []
        for name in sorted(os.listdir(self._pet_dir)):
            full = os.path.join(self._pet_dir, name)
            if os.path.isdir(full) and self.has_frames(name):
                actions.append(name)
        return actions

    @property
    def current_action(self) -> str:
        return self._current_action

    @property
    def is_playing(self) -> bool:
        return self._frame_timer.isActive()

    # ── 窗口动画 ──────────────────────────────────────────

    def move_to(self, start_pos, end_pos, duration=500, callback=None):
        """将窗口从 start_pos 移动到 end_pos。"""
        print("from", start_pos, " move to:", end_pos)
        anim = QPropertyAnimation(self._window, b"pos")
        anim.setDuration(duration)
        anim.setStartValue(start_pos)
        anim.setEndValue(end_pos)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        if callback:
            anim.finished.connect(callback)
        anim.start()
        self._win_anims.append(anim)
        return anim

    def fade_in(self, duration=300):
        """窗口淡入。"""
        anim = QPropertyAnimation(self._window, b"windowOpacity")
        anim.setDuration(duration)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.start()
        self._win_anims.append(anim)
        return anim

    def fade_out(self, duration=300, callback=None):
        """窗口淡出。"""
        anim = QPropertyAnimation(self._window, b"windowOpacity")
        anim.setDuration(duration)
        anim.setStartValue(self._window.windowOpacity())
        anim.setEndValue(0.0)
        if callback:
            anim.finished.connect(callback)
        anim.start()
        self._win_anims.append(anim)
        return anim

    def bounce(self, duration=600):
        """窗口弹跳。"""
        original_pos = self._window.pos()
        anim = QPropertyAnimation(self._window, b"pos")
        anim.setDuration(duration)
        anim.setKeyValueAt(0, original_pos)
        anim.setKeyValueAt(0.3, QPoint(original_pos.x(), original_pos.y() - 40))
        anim.setKeyValueAt(0.5, original_pos)
        anim.setKeyValueAt(0.7, QPoint(original_pos.x(), original_pos.y() - 20))
        anim.setKeyValueAt(1, original_pos)
        anim.setEasingCurve(QEasingCurve.Type.OutBounce)
        anim.start()
        self._win_anims.append(anim)
        return anim

    def idle_sway(self, amplitude=3):
        """窗口左右轻微摇摆（返回 QTimer，可手动停止）。"""
        timer = QTimer(self)
        original_x = self._window.x()
        direction = 1

        def sway():
            nonlocal direction
            new_x = original_x + amplitude * direction
            self._window.move(new_x, self._window.y())
            direction *= -1

        timer.timeout.connect(sway)
        timer.start(1000)
        return timer

    # ── 内部方法 ──────────────────────────────────────────

    def _load_action(self, action: str) -> list[QPixmap]:
        """加载并缓存指定动作的所有帧，按文件名排序。"""
        if action in self._cache:
            return self._cache[action]

        action_dir = os.path.join(self._pet_dir, action)
        frames: list[QPixmap] = []

        if not os.path.isdir(action_dir):
            return frames

        files = sorted(
            f for f in os.listdir(action_dir)
            if os.path.splitext(f)[1].lower() in _SUPPORTED_EXT
        )

        for f in files:
            pixmap = QPixmap(os.path.join(action_dir, f))
            if pixmap.isNull():
                continue
            pixmap = pixmap.scaled(
                config.PET_WIDTH,
                config.PET_HEIGHT,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            frames.append(pixmap)

        # 补帧：若帧数不足 PET_FPS，循环填充至 PET_FPS 帧
        # 保证一个播放周期 = 1 秒，避免帧数少时动画节奏过快
        min_frames = config.PET_FPS
        if 0 < len(frames) < min_frames:
            frames = (frames * (min_frames // len(frames) + 1))[:min_frames]

        if frames:
            self._cache[action] = frames
        return frames

    def _next_frame(self):
        """切换到下一帧。"""
        self._current_frame += 1
        if self._current_frame >= len(self._frames):
            if self._loop:
                self._current_frame = 0
            else:
                self._frame_timer.stop()
                self.animation_finished.emit(self._current_action)
                return
        self._label.setPixmap(self._frames[self._current_frame])
