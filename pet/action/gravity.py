"""重力系统 —— 模拟桌宠受重力下落，可站立在其他窗口上。"""

import logging

from PySide6.QtCore import QTimer, QObject, Signal, QPropertyAnimation, QPoint
from PySide6.QtWidgets import QWidget

from pet.brain.window_detector import get_visible_windows, get_window_rect, is_window_occluded

logger = logging.getLogger(__name__)


class GravitySystem(QObject):
    """重力系统，定时检测桌宠是否悬空并模拟下落。"""

    falling_started = Signal()  # 进入下落状态时发出
    landed = Signal()           # 落地时发出

    def __init__(self, window: QWidget, animator, win_anims: list, parent=None):
        super().__init__(parent)
        self._window = window
        self._anim = animator
        self._win_anims = win_anims  # PetActions 的动画列表，用于判断是否有动画在执行

        self._step = 5
        self._interval = 30
        self._enabled = True
        self._falling = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self._interval)

        self._scan_tick = 0
        self._cached_effective_bottom: int | None = None
        self._standing_hwnd: int = 0
        self._force_standing_check: bool = False
        self._ALIVE_CHECK_INTERVAL = 15
        self._suppress_idle: bool = False

    @property
    def falling(self) -> bool:
        return self._falling

    @property
    def suppress_idle(self) -> bool:
        return self._suppress_idle

    @suppress_idle.setter
    def suppress_idle(self, value: bool):
        self._suppress_idle = value

    def enable(self, enabled: bool = True):
        self._enabled = enabled
        if enabled:
            self._cached_effective_bottom = None
            self._falling = False  # 重置下落状态，下一 tick 重新检测
            if not self._timer.isActive():
                self._timer.start(self._interval)
        else:
            self._timer.stop()

    def _clamp_pos(self, pos):
        """将坐标限制在屏幕可用范围内。"""
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QPoint
        screen = QApplication.primaryScreen()
        if not screen:
            return pos
        geo = screen.availableGeometry()
        x = max(geo.left(), min(pos.x(), geo.right() - self._window.width()))
        y = max(geo.top(), min(pos.y(), geo.bottom() - self._window.height()))
        return QPoint(x, y)

    def _tick(self):
        if not self._enabled:
            return
        if any(a.state() == QPropertyAnimation.State.Running for a in self._win_anims):
            return

        self._scan_tick += 1
        old_y = self._window.y()
        new_y = old_y + self._step

        try:
            from PySide6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
            if screen is None:
                return

            w = self._window.width()
            h = self._window.height()
            screen_bottom = screen.availableGeometry().bottom() - h

            # 静止时：定时检查站立窗口是否存活或移动
            was_at_bottom = self._cached_effective_bottom is not None and old_y >= self._cached_effective_bottom
            if was_at_bottom and self._cached_effective_bottom is not None:
                if self._standing_hwnd and (
                    self._scan_tick % self._ALIVE_CHECK_INTERVAL == 0
                    or self._force_standing_check
                ):
                    self._force_standing_check = False
                    rect = get_window_rect(self._standing_hwnd)
                    if rect is None:
                        logger.info(f"[Gravity] standing window gone (hwnd={self._standing_hwnd})")
                        self._standing_hwnd = 0
                        self._cached_effective_bottom = None
                    elif is_window_occluded(self._standing_hwnd, skip_hwnd=int(self._window.winId())):
                        logger.info(f"[Gravity] standing window occluded (hwnd={self._standing_hwnd})")
                        self._standing_hwnd = 0
                        self._cached_effective_bottom = None
                    else:
                        new_top = rect[1]
                        pet_x = self._window.x()
                        pet_w = self._window.width()
                        feet_l = pet_x + pet_w // 3
                        feet_r = pet_x + (2 * pet_w) // 3
                        if (feet_l >= rect[2] or feet_r <= rect[0]
                                or new_top != self._cached_effective_bottom + h):
                            logger.info(f"[Gravity] standing window moved (hwnd={self._standing_hwnd})")
                            self._standing_hwnd = 0
                            self._cached_effective_bottom = None
                if self._cached_effective_bottom is not None:
                    effective_bottom = self._cached_effective_bottom
                    new_y = effective_bottom
                    self._window.move(self._window.x(), new_y)
                    return

            # 下落中：每 tick 全量扫描
            old_pet_bottom = old_y + h
            new_pet_bottom = new_y + h
            pet_x = self._window.x()
            pet_self = (pet_x, old_y, pet_x + w, old_y + h)
            found_hwnd = 0

            effective_bottom = screen_bottom
            pet_hwnd = int(self._window.winId())
            feet_l = pet_x + w // 3
            feet_r = pet_x + (2 * w) // 3
            for win in get_visible_windows():
                left, top, right, bottom = win["rect"]
                if (left == pet_self[0] and top == pet_self[1]
                        and right == pet_self[2] and bottom == pet_self[3]):
                    continue
                if feet_l >= right or feet_r <= left:
                    continue
                if old_pet_bottom <= top <= new_pet_bottom:
                    landing = top - h
                    if landing < effective_bottom:
                        if is_window_occluded(win["hwnd"], skip_hwnd=pet_hwnd):
                            continue
                        effective_bottom = landing
                        found_hwnd = win["hwnd"]
                        logger.info(f"[Gravity] land on: \"{win['title'][:30]}\" top={top}")
            self._cached_effective_bottom = effective_bottom
            if found_hwnd:
                self._standing_hwnd = found_hwnd
            elif effective_bottom == screen_bottom:
                self._standing_hwnd = 0
        except Exception:
            if self._cached_effective_bottom is None:
                from PySide6.QtWidgets import QApplication as _QA
                s = _QA.primaryScreen()
                fb = s.availableGeometry().bottom() - self._window.height() if s else new_y
                self._cached_effective_bottom = fb
                effective_bottom = fb
            else:
                effective_bottom = self._cached_effective_bottom

        at_bottom = new_y >= effective_bottom
        if at_bottom:
            new_y = effective_bottom
        # 限制在屏幕范围内
        clamped = self._clamp_pos(QPoint(self._window.x(), new_y))
        self._window.move(clamped.x(), clamped.y())

        if at_bottom and self._falling:
            # 正在下落，落到了地面
            self._falling = False
            self._force_standing_check = True
            self._anim.play("idle")
            self.landed.emit()
        elif at_bottom and not self._falling:
            # 释放拖拽时已经在地上，直接 idle（walk 期间抑制）
            if not self._suppress_idle:
                self._anim.play("idle")
        elif not at_bottom and not self._falling:
            # 悬空，开始下落
            self._falling = True
            self._anim.play("falling")
            logger.info(f"[Gravity] falling started at y={old_y}, bottom={effective_bottom}")
            self.falling_started.emit()
