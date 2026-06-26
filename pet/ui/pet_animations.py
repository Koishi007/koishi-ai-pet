"""桌宠帧动画模块 —— 基于 JSON 配置的帧序列播放。

每个动作目录下需包含 `<action>.json` 配置文件：

    {
      "desc": "待机动画，轻轻呼吸",
      "tick_counts": 120,
      "frame_ratios": [0.95, 0.05],
      "loop": true,
      "note": ""
    }

- tick_counts: 一个循环的总 tick 数，配合 PET_FPS 控制周期时长
- frame_ratios: 每张素材占比（和 = 1.0），按文件名字母序对应
- loop: 是否循环播放（默认 true）
- 每一tick时长 = 1000/PET_FPS（ms）
"""

import json
import logging
import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QObject, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from pet.config import config

logger = logging.getLogger(__name__)

_SUPPORTED_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class PetAnimator(QObject):

    animation_finished = Signal(str)
    animation_interrupted = Signal(str)
    frame_changed = Signal(QPixmap)

    def __init__(self, pet_dir: str | None = None, parent=None):
        super().__init__(parent)
        self._pet_dir = str(pet_dir) if pet_dir else str(BASE_DIR / "assets" / "actions")

        self._frames: list[QPixmap] = []
        self._tick_plan: list[int] = []       # 每帧停留 tick 数
        self._tick_in_frame: int = 0           # 当前帧内已过 tick
        self._current_frame: int = 0
        self._current_action: str = ""
        self._loop: bool = True

        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._next_frame)

        self._duration_timer = QTimer(self)
        self._duration_timer.setSingleShot(True)
        self._duration_timer.timeout.connect(self._on_duration_end)

        self._cache: dict[str, dict] = {}      # action → {frames, tick_plan, loop, desc, note}

    # ── public API ──

    def play(self, action: str, duration: float | None = None) -> bool:
        """播放动作。

        loop 动作: duration 控制总时长（秒），None 则无限循环。
        one-shot: duration 忽略，时长由 tick_counts + PET_FPS 决定。
        """
        if self._current_action and not self._loop and self.is_playing:
            self._frame_timer.stop()
            self._duration_timer.stop()
            self.animation_interrupted.emit(self._current_action)
            self.animation_finished.emit(self._current_action)

        data = self._load_action(action)
        if not data:
            return False

        self._frame_timer.stop()
        self._duration_timer.stop()

        self._frames = data["frames"]
        self._tick_plan = data["tick_plan"]
        self._loop = data["loop"]
        self._current_action = action
        self._current_frame = 0
        self._tick_in_frame = 0

        self.frame_changed.emit(self._frames[0])

        interval = self._calc_tick_interval()
        self._frame_timer.start(interval)

        if self._loop and duration is not None and duration > 0:
            self._duration_timer.start(int(duration * 1000))

        return True

    def stop(self):
        self._frame_timer.stop()
        self._duration_timer.stop()

    def has_frames(self, action: str) -> bool:
        return self._load_action(action) is not None

    def available_actions(self) -> list[str]:
        if not os.path.isdir(self._pet_dir):
            return []
        actions = []
        for name in sorted(os.listdir(self._pet_dir)):
            full = os.path.join(self._pet_dir, name)
            if os.path.isdir(full) and self._config_exists(name):
                actions.append(name)
        return actions

    @property
    def current_action(self) -> str:
        return self._current_action

    @property
    def is_playing(self) -> bool:
        return self._frame_timer.isActive()

    # ── internal ──

    def _calc_tick_interval(self) -> int:
        return max(1, round(1000 / config.PET_FPS))

    def _load_action(self, action: str) -> dict | None:
        if action in self._cache:
            return self._cache[action]

        cfg = self._load_action_config(action)
        if cfg is None:
            return None

        action_dir = os.path.join(self._pet_dir, action)
        image_files = sorted(
            f for f in os.listdir(action_dir)
            if os.path.splitext(f)[1].lower() in _SUPPORTED_EXT
        )
        if not image_files:
            return None

        frames: list[QPixmap] = []
        for f in image_files:
            pixmap = QPixmap(os.path.join(action_dir, f))
            if pixmap.isNull():
                logger.warning(f"Failed to load image: {action}/{f}")
                return None
            dpr = QApplication.primaryScreen().devicePixelRatio() if QApplication.primaryScreen() else 1.0
            pixmap = pixmap.scaled(
                int(config.PET_WIDTH * dpr),
                int(config.PET_HEIGHT * dpr),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            pixmap.setDevicePixelRatio(dpr)
            frames.append(pixmap)

        if not self._validate_config(cfg, len(frames), action):
            return None

        tick_plan = self._build_tick_plan(cfg, len(frames))
        data = {
            "frames": frames,
            "tick_plan": tick_plan,
            "loop": cfg.get("loop", True),
        }
        self._cache[action] = data
        return data

    def _build_tick_plan(self, cfg: dict, frame_count: int) -> list[int]:
        tick_counts = cfg.get("tick_counts", 30)
        ratios = cfg.get("frame_ratios", [1.0 / frame_count] * frame_count)

        if len(ratios) != frame_count:
            ratios = [1.0 / frame_count] * frame_count

        tick_plan: list[int] = []
        allocated = 0
        for i in range(frame_count - 1):
            ticks = max(1, round(ratios[i] * tick_counts))
            ceiling = tick_counts - allocated - (frame_count - i - 1)
            ticks = max(1, min(ticks, ceiling))
            tick_plan.append(ticks)
            allocated += ticks
        tick_plan.append(max(1, tick_counts - allocated))
        return tick_plan

    def _validate_config(self, cfg: dict, frame_count: int, action: str) -> bool:
        ratios = cfg.get("frame_ratios")
        if ratios is not None:
            if len(ratios) != frame_count:
                logger.warning(
                    f"Config mismatch for '{action}': "
                    f"frame_ratios has {len(ratios)} entries but {frame_count} image files"
                )
                return False
            total = sum(ratios)
            if abs(total - 1.0) > 0.01:
                logger.warning(
                    f"Config mismatch for '{action}': "
                    f"frame_ratios sum = {total:.3f}, expected 1.0"
                )
                return False

        tick_counts = cfg.get("tick_counts")
        effective_len = len(ratios) if ratios else frame_count
        if tick_counts is not None and tick_counts < effective_len * 3:
            logger.info(
                f"'{action}': tick_counts ({tick_counts}) is low relative to "
                f"frame count ({effective_len}), frame_ratios may be flattened"
            )
        if tick_counts is not None and tick_counts < effective_len:
            logger.warning(
                f"Config mismatch for '{action}': "
                f"tick_counts ({cfg['tick_counts']}) < frame count ({effective_len})"
            )
            return False

        return True

    def _config_exists(self, action: str) -> bool:
        return os.path.isfile(os.path.join(self._pet_dir, action, f"{action}.json"))

    def _load_action_config(self, action: str) -> dict | None:
        config_path = os.path.join(self._pet_dir, action, f"{action}.json")
        if not os.path.isfile(config_path):
            return None
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to parse config: {config_path}: {e}")
            return None

    def _on_duration_end(self):
        self._frame_timer.stop()
        self.animation_finished.emit(self._current_action)

    def _next_frame(self):
        self._tick_in_frame += 1
        if self._tick_in_frame >= self._tick_plan[self._current_frame]:
            self._tick_in_frame = 0
            self._current_frame += 1
            if self._current_frame >= len(self._frames):
                if self._loop:
                    self._current_frame = 0
                else:
                    self._frame_timer.stop()
                    self.animation_finished.emit(self._current_action)
                    return
            self.frame_changed.emit(self._frames[self._current_frame])
