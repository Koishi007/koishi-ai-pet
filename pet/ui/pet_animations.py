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
import math
import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QObject, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from pet.config import config

logger = logging.getLogger(__name__)

_SUPPORTED_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class PetAnimator(QObject):

    animation_finished = Signal(str)
    animation_interrupted = Signal(str)
    frame_changed = Signal(QPixmap)
    pose_changed = Signal(float, float, float)  # 呼吸姿态（纵向偏移px浮点, 横向缩放, 纵向缩放）

    def __init__(self, pet_dir: str | None = None, parent=None):
        super().__init__(parent)
        self._pet_dir = str(pet_dir) if pet_dir else str(BASE_DIR / "assets" / "actions")

        self._frames: list[QPixmap] = []
        self._tick_plan: list[int] = []       # 每帧停留 tick 数
        self._tick_in_frame: int = 0           # 当前帧内已过 tick
        self._current_frame: int = 0
        self._current_action: str = ""
        self._loop: bool = True

        # 呼吸姿态：按 tick 计算。位移只向上抬、缩放以脚底为锚点，避免沉出窗口
        self._tick_count: int = 0
        self._breath_amplitude: int = 0
        self._breath_scale_x: float = 1.0
        self._breath_scale_y: float = 1.0
        self._breath_period: int = 0
        self._pose_now: tuple[float, float, float] = (0, 1.0, 1.0)

        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._next_frame)

        self._duration_timer = QTimer(self)
        self._duration_timer.setSingleShot(True)
        self._duration_timer.timeout.connect(self._on_duration_end)

        self._cache: dict[str, dict] = {}      # action → {frames, tick_plan, loop, bob_*}
        self._cache_dpr: dict[str, float] = {}  # action → 生成帧时所用 DPR


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
            # 缺帧或配置损坏：停掉旧动画，让调用方（动作队列）能看出没播起来
            self._frame_timer.stop()
            self._duration_timer.stop()
            self._reset_breath()
            return False

        self._frame_timer.stop()
        self._duration_timer.stop()
        self._reset_breath()

        breath = data["breath"]
        self._frames = data["frames"]
        self._tick_plan = data["tick_plan"]
        self._loop = data["loop"]
        self._breath_amplitude = breath["amplitude"]
        self._breath_scale_x = breath["scale_x"]
        self._breath_scale_y = breath["scale_y"]
        self._breath_period = breath["period_ticks"]
        self._current_action = action
        self._current_frame = 0
        self._tick_in_frame = 0
        self._tick_count = 0

        self.frame_changed.emit(self._frames[0])

        interval = self._calc_tick_interval()
        self._frame_timer.start(interval)

        if self._loop and duration is not None and duration > 0:
            self._duration_timer.start(int(duration * 1000))

        return True

    def stop(self):
        self._frame_timer.stop()
        self._duration_timer.stop()
        self._reset_breath()

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


    def _calc_tick_interval(self) -> int:
        return max(1, round(1000 / config.PET_FPS))

    def _frame_dpr(self) -> float:
        """帧贴图按所属窗口所在屏幕的 DPR 生成；未挂到窗口时退回主屏。

        帧物理尺寸须等于窗口物理尺寸，否则跨屏后被重采样。
        """
        parent = self.parent()
        screen = parent.screen() if isinstance(parent, QWidget) else None
        if screen is None:
            screen = QApplication.primaryScreen()
        return screen.devicePixelRatio() if screen is not None else 1.0

    def rebuild_frames(self):
        """按当前屏幕 DPR 重建当前动作的帧，保留播放位置；窗口跨屏或系统缩放改变后调用。"""
        if not self._current_action:
            return
        self._cache.clear()
        self._cache_dpr.clear()
        data = self._load_action(self._current_action)
        if not data:
            return
        self._frames = data["frames"]
        self._tick_plan = data["tick_plan"]
        self._current_frame = min(self._current_frame, len(self._frames) - 1)
        self._tick_in_frame = 0
        self.frame_changed.emit(self._frames[self._current_frame])

    def _load_action(self, action: str) -> dict | None:
        dpr = self._frame_dpr()
        if action in self._cache and self._cache_dpr.get(action) == dpr:
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
            # 非整数 DPR（125%/150%）下 int 截断会与窗口物理尺寸差 1 像素，帧被重采样后边缘发虚
            pixmap = pixmap.scaled(
                round(config.PET_WIDTH * dpr),
                round(config.PET_HEIGHT * dpr),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            pixmap.setDevicePixelRatio(dpr)
            frames.append(pixmap)

        cfg = self._sanitize_config(cfg, len(frames), action)
        tick_plan = self._build_tick_plan(cfg, len(frames))
        data = {
            "frames": frames,
            "tick_plan": tick_plan,
            "loop": cfg.get("loop", True),
            "breath": self._parse_breath(cfg, action),
        }
        self._cache[action] = data
        self._cache_dpr[action] = dpr
        return data

    @staticmethod
    def _build_tick_plan(cfg: dict, frame_count: int) -> list[int]:
        """按累计比例分配 tick。

        逐帧独立取整在除不尽时会把差额全推给尾帧（如 8 帧 / 90 tick → 11×7 + 13），
        累计取整把误差摊到各帧上（→ 11/12 交替）。
        """
        tick_counts = cfg.get("tick_counts", 30)
        ratios = cfg.get("frame_ratios", [1.0 / frame_count] * frame_count)

        if len(ratios) != frame_count:
            ratios = [1.0 / frame_count] * frame_count

        tick_plan: list[int] = []
        allocated = 0
        cumulative = 0.0
        for i in range(frame_count - 1):
            cumulative += ratios[i]
            target = int(cumulative * tick_counts + 0.5)
            ceiling = tick_counts - allocated - (frame_count - i - 1)
            ticks = max(1, min(target - allocated, ceiling))
            tick_plan.append(ticks)
            allocated += ticks
        tick_plan.append(max(1, tick_counts - allocated))
        return tick_plan

    @staticmethod
    def _sanitize_config(cfg: dict, frame_count: int, action: str) -> dict:
        """把不合法的时间配置修正成可用值：比例不合法则等分，tick 不够则抬到帧数。

        返回新的 dict，不修改入参。
        """
        cfg = dict(cfg)

        ratios = cfg.get("frame_ratios")
        if ratios is not None:
            try:
                values = [float(r) for r in ratios]
            except (TypeError, ValueError):
                values = []
            if len(values) == frame_count and all(v > 0 for v in values):
                total = sum(values)
                if abs(total - 1.0) > 0.01:
                    logger.warning(f"'{action}': frame_ratios sum = {total:.3f}，已按比例归一化")
                    values = [v / total for v in values]
                cfg["frame_ratios"] = values
            else:
                logger.warning(f"'{action}': frame_ratios 与 {frame_count} 帧不匹配，改为等分")
                cfg.pop("frame_ratios", None)

        try:
            tick_counts = max(1, int(cfg.get("tick_counts", 30)))
        except (TypeError, ValueError):
            logger.warning(f"'{action}': tick_counts 非法，改用 30")
            tick_counts = 30
        if tick_counts < frame_count:
            logger.warning(f"'{action}': tick_counts ({tick_counts}) < 帧数 ({frame_count})，已抬到帧数")
            tick_counts = frame_count
        cfg["tick_counts"] = tick_counts

        return cfg

    @staticmethod
    def _parse_breath(cfg: dict, action: str) -> dict:
        """读取 breath 配置：位移 px、横纵缩放、周期 tick。

        位移上限 4px、缩放限制 0.5~2.0：贴图 1:1 撑满窗口，幅度更大就出界。
        """
        rest = {"amplitude": 0, "scale_x": 1.0, "scale_y": 1.0, "period_ticks": 0}
        breath = cfg.get("breath") or {}
        try:
            return {
                "amplitude": max(0, min(4, int(breath.get("amplitude", 0) or 0))),
                "scale_x": max(0.5, min(2.0, float(breath.get("scale_x", 1.0) or 1.0))),
                "scale_y": max(0.5, min(2.0, float(breath.get("scale_y", 1.0) or 1.0))),
                "period_ticks": max(0, min(900, int(breath.get("period_ticks", 0) or 0))),
            }
        except (TypeError, ValueError):
            logger.warning(f"'{action}': breath 配置无效，已忽略")
            return rest

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
        self._reset_breath()
        self.animation_finished.emit(self._current_action)

    def _next_frame(self):
        self._tick_count += 1
        self._apply_breath()
        self._tick_in_frame += 1
        if self._tick_in_frame >= self._tick_plan[self._current_frame]:
            self._tick_in_frame = 0
            self._current_frame += 1
            if self._current_frame >= len(self._frames):
                if self._loop:
                    self._current_frame = 0
                else:
                    self._frame_timer.stop()
                    self._reset_breath()
                    self.animation_finished.emit(self._current_action)
                    return
            self.frame_changed.emit(self._frames[self._current_frame])

    def _reset_breath(self):
        """停播或切动作时把呼吸姿态归位。"""
        self._breath_amplitude = 0
        self._breath_scale_x = 1.0
        self._breath_scale_y = 1.0
        self._breath_period = 0
        if self._pose_now != (0, 1.0, 1.0):
            self._pose_now = (0, 1.0, 1.0)
            self.pose_changed.emit(0, 1.0, 1.0)

    @staticmethod
    def _breath_pose(tick: int, amplitude: int, scale_x: float, scale_y: float,
                     period: int) -> tuple[float, float, float]:
        """呼吸姿态：sin² 曲线，位移只向上抬（负值）、缩放随吸气张开。

        位移输出浮点连续值，由绘制方按亚像素渲染，避免整数取整
        在低振幅下退化成 0/−1 方波（表现为站立时上下闪动）；
        缩放锚点在脚底（绘制方保证），Y < 1 只让头顶下降，不会沉出画面；
        scale_x * scale_y ≈ 1 时体积不变，看起来是呼吸而不是整体放大。
        """
        if period <= 0:
            return 0, 1.0, 1.0
        phase = math.sin(math.pi * (tick % period) / period) ** 2
        return (-amplitude * phase,
                1.0 + (scale_x - 1.0) * phase,
                1.0 + (scale_y - 1.0) * phase)

    def _apply_breath(self):
        if not self._breath_period:
            return
        pose = self._breath_pose(self._tick_count, self._breath_amplitude,
                                 self._breath_scale_x, self._breath_scale_y,
                                 self._breath_period)
        if pose != self._pose_now:
            self._pose_now = pose
            self.pose_changed.emit(*pose)
