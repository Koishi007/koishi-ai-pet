"""桌宠帧动画模块 —— 纯帧序列播放。"""

import os
from PySide6.QtCore import Qt, QTimer, QObject, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel
from config import config

_SUPPORTED_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


class PetAnimator(QObject):
    """桌宠动画控制器"""

    animation_finished = Signal(str)  # 非循环播放完毕时发出

    def __init__(self, label: QLabel, pet_dir: str | None = None, parent=None):
        super().__init__(parent)
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

        self._cache: dict[str, list[QPixmap]] = {}

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
            interval = self._calc_interval(loop, fps)
            self._frame_timer.start(interval)

        return True

    def _calc_interval(self, loop: bool, fps: int | None) -> int:
        base_fps = fps or config.PET_FPS
        raw_interval = round(1000 / base_fps)
        if loop and len(self._frames) < base_fps:
            return max(1, round(1000 / len(self._frames)))
        return max(1, raw_interval)

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

    def _load_action(self, action: str) -> list[QPixmap]:
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
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            frames.append(pixmap)

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
