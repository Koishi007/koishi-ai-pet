"""重力系统 —— 模拟桌宠受重力下落，可站立在其他窗口上。"""

import logging

from PySide6.QtCore import QTimer, QObject, Signal, QPropertyAnimation, QPoint
from PySide6.QtWidgets import QWidget, QApplication

from pet.brain.window_detector import get_visible_windows, get_window_rect, is_window_occluded

logger = logging.getLogger(__name__)


class GravitySystem(QObject):
    """重力系统，定时检测桌宠是否悬空并模拟下落。支持甚出投掷物理。"""

    falling_started = Signal()  # 进入下落状态时发出
    landed = Signal()           # 落地时发出
    standing_lost = Signal(str) # 站立的窗口消失/被遮挡，附带窗口标题
    walk_finished = Signal()    # 重力行走完成时发出

    # 物理参数
    _GRAVITY_ACCEL  = 1.5   # 重力加速度（px/tick²）
    _FRICTION       = 0.99  # 水平摩擦系数（每 tick 乘以）
    _MAX_SPEED      = 25.0  # 最大速度（px/tick）
    _FALL_TERMINAL  = 8.0   # 常规下落终端速度（px/tick）
    _WALL_BOUNCE    = -0.4  # 碰屏幕左右边缘出射系数
    _IMPULSE_SCALE  = 0.05  # 鼠标速度（px/s）转 tick 速度比例（30ms tick）

    def __init__(self, window: QWidget, animator, win_anims: list, parent=None):
        super().__init__(parent)
        self._window = window
        self._anim = animator
        self._win_anims = win_anims  # PetActions 的动画列表，用于判断是否有动画在执行

        self._interval = 30
        self._enabled = True
        self._falling = False

        # 甚出物理状态
        self._vx: float = 0.0
        self._vy: float = 0.0
        self._in_flick: bool = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self._interval)

        self._scan_tick = 0
        self._cached_effective_bottom: int | None = None
        self._standing_hwnd: int = 0
        self._standing_title: str = ""
        self._standing_rect: tuple | None = None
        self._force_standing_check: bool = False
        self._ALIVE_CHECK_INTERVAL = 15
        self._suppress_idle: bool = False
        self._last_anim_played: str | None = None

        # 重力行走状态
        self._walking: bool = False
        self._walk_sign: int = 1       # 1=右, -1=左
        self._walk_speed: float = 3.0  # px/tick（约 100px/s @ 30ms tick）
        self._walk_target_x: int = 0   # 目标 x 坐标
        self._walk_start_x: int = 0    # 起始 x 坐标

    def apply_impulse(self, vx: float, vy: float):
        """注入初速度，开始甚出物理模式。

        Args:
            vx: 水平速度（px/s）
            vy: 垂直速度（px/s）
        """
        if not self._enabled:
            return

        # 换算为 px/tick
        self._vx = max(-self._MAX_SPEED, min(vx * self._IMPULSE_SCALE, self._MAX_SPEED))
        # 纯水平/向下甩出时给一个最小向上速度，确保离面
        scaled_vy = vy * self._IMPULSE_SCALE
        if scaled_vy >= 0:
            scaled_vy = min(scaled_vy, -2.0)
        self._vy = max(-self._MAX_SPEED, min(scaled_vy, self._MAX_SPEED))
        self._in_flick = True
        self._cached_effective_bottom = None
        if not self._falling:
            self._falling = True
            self.falling_started.emit()
            self._play_once("falling")
        logger.info(f"[Gravity] apply_impulse vx={self._vx:.1f} vy={self._vy:.1f} px/tick")

    @property
    def walking(self) -> bool:
        return self._walking

    def walk_start(self, direction: str, distance: int):
        """启动重力驱动的行走。由重力 tick 统一驱动位移和检测。

        Args:
            direction: "left" 或 "right"
            distance: 行走像素距离
        """
        if not self._enabled:
            return
        if direction not in ("left", "right"):
            raise ValueError(f"direction must be 'left' or 'right', got '{direction}'")

        self._walk_sign = 1 if direction == "right" else -1
        self._walk_start_x = self._window.x()
        self._walk_target_x = self._walk_start_x + self._walk_sign * distance
        self._walking = True
        self._suppress_idle = True
        logger.info(f"[Gravity] walk_start dir={direction} dist={distance} "
                     f"from={self._walk_start_x} to={self._walk_target_x}")

    def walk_stop(self):
        """停止行走。"""
        if not self._walking:
            return
        self._walking = False
        self._suppress_idle = False
        self._play_once("idle")
        logger.info(f"[Gravity] walk_stop at x={self._window.x()}")
        self.walk_finished.emit()

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
            self._falling = False
            self._in_flick = False
            self._vx = 0.0
            self._vy = 0.0
            self._standing_hwnd = 0
            self._standing_title = ""
            self._standing_rect = None
            self._walking = False
            self._last_anim_played = None
            if not self._timer.isActive():
                self._timer.start(self._interval)
        else:
            self._timer.stop()

    def _play_once(self, name: str):
        """只在动画变化时播一次，避免每 tick 重播同动画。"""
        if name != self._last_anim_played:
            self._last_anim_played = name
            self._anim.play(name)

    def _clamp_pos(self, pos):
        """将坐标限制在屏幕可用范围内。"""
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

        # 甩出的物理模式：自行驱动位移，不走常规重力逻辑
        if self._in_flick:
            self._tick_flick()
            return

        # 重力行走模式：每个 tick 驱动水平位移，同时保持垂直重力检测
        if self._walking:
            self._tick_walk()
            return

        self._scan_tick += 1
        old_y = self._window.y()
        self._vy = min(self._vy + self._GRAVITY_ACCEL, self._FALL_TERMINAL)
        new_y = old_y + self._vy

        try:
            screen = QApplication.primaryScreen()
            if screen is None:
                return

            w = self._window.width()
            h = self._window.height()
            screen_bottom = screen.availableGeometry().bottom() - h

            was_at_bottom = self._cached_effective_bottom is not None and old_y >= self._cached_effective_bottom
            if was_at_bottom:
                if self._standing_hwnd and (
                    self._scan_tick % self._ALIVE_CHECK_INTERVAL == 0
                    or self._force_standing_check
                ):
                    self._force_standing_check = False
                    rect = get_window_rect(self._standing_hwnd)
                    if rect is None:
                        logger.debug(f"[Gravity] standing window gone (hwnd={self._standing_hwnd})")
                        lost_title = self._standing_title
                        self._standing_hwnd = 0
                        self._standing_title = ""
                        self._cached_effective_bottom = None
                        self.standing_lost.emit(lost_title)
                    elif is_window_occluded(self._standing_hwnd, skip_hwnd=int(self._window.winId())):
                        logger.debug(f"[Gravity] standing window occluded (hwnd={self._standing_hwnd})")
                        lost_title = self._standing_title
                        self._standing_hwnd = 0
                        self._standing_title = ""
                        self._cached_effective_bottom = None
                        self.standing_lost.emit(lost_title)
                    else:
                        new_top = rect[1]
                        pet_x = self._window.x()
                        pet_w = self._window.width()
                        feet_l = pet_x + pet_w // 3
                        feet_r = pet_x + (2 * pet_w) // 3
                        if (feet_l >= rect[2] or feet_r <= rect[0]
                                or new_top != self._cached_effective_bottom + h):
                            logger.debug(f"[Gravity] standing window moved (hwnd={self._standing_hwnd})")
                            lost_title = self._standing_title
                            self._standing_hwnd = 0
                            self._standing_title = ""
                            self._cached_effective_bottom = None
                            self.standing_lost.emit(lost_title)
                else:
                    self._force_standing_check = False
                if self._cached_effective_bottom is not None:
                    effective_bottom = self._cached_effective_bottom
                    new_y = effective_bottom
                    self._vy = 0.0
                    self._window.move(self._window.x(), new_y)
                    return

            old_pet_bottom = old_y + h
            new_pet_bottom = new_y + h
            pet_x = self._window.x()
            pet_self = (pet_x, old_y, pet_x + w, old_y + h)
            found_hwnd = 0
            found_title = ""

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
                        found_title = win["title"][:30]
                        logger.debug(f"[Gravity] land on: \"{found_title}\" top={top}")
            self._cached_effective_bottom = effective_bottom
            if found_hwnd:
                self._standing_hwnd = found_hwnd
                self._standing_title = found_title
                self._standing_rect = get_window_rect(found_hwnd)
            elif effective_bottom == screen_bottom:
                self._standing_hwnd = 0
                self._standing_title = ""
                self._standing_rect = None
        except Exception:
            logger.exception("[Gravity] _tick scan failed")
            if self._cached_effective_bottom is None:
                s = QApplication.primaryScreen()
                fb = s.availableGeometry().bottom() - self._window.height() if s else new_y
                self._cached_effective_bottom = fb
                effective_bottom = fb
            else:
                effective_bottom = self._cached_effective_bottom

        at_bottom = new_y >= effective_bottom
        if at_bottom:
            new_y = effective_bottom
        clamped = self._clamp_pos(QPoint(self._window.x(), new_y))
        self._window.move(clamped.x(), clamped.y())

        if at_bottom and self._falling:
            self._falling = False
            self._vy = 0.0
            self._force_standing_check = True
            self._play_once("idle")
            self.landed.emit()
        elif at_bottom and not self._falling:
            self._vy = 0.0
            if not self._suppress_idle:
                self._play_once("idle")
        elif not at_bottom and not self._falling:
            self._falling = True
            logger.debug(f"[Gravity] falling started at y={old_y}, bottom={effective_bottom}")
            self.falling_started.emit()
            self._play_once("falling")

    def _tick_walk(self):
        """重力行走 tick：水平位移 + 垂直重力检测，丝滑无顿挫。"""
        cur_x = self._window.x()
        cur_y = self._window.y()
        w = self._window.width()
        h = self._window.height()

        # 水平步进
        step = self._walk_speed * self._walk_sign
        new_x = cur_x + step

        # 判断是否到达目标 / 屏幕边缘
        reached_target = (
            (self._walk_sign > 0 and new_x >= self._walk_target_x) or
            (self._walk_sign < 0 and new_x <= self._walk_target_x)
        )
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            hit_edge = (new_x <= geo.left()) or (new_x >= geo.right() - w)
        else:
            hit_edge = False

        if reached_target:
            new_x = self._walk_target_x
        if hit_edge and screen:
            new_x = max(geo.left(), min(new_x, geo.right() - w))

        # 垂直方向：正常重力检测
        self._vy = min(self._vy + self._GRAVITY_ACCEL, self._FALL_TERMINAL)
        new_y = cur_y + self._vy

        # 扫描落点
        try:
            screen_bottom = screen.availableGeometry().bottom() - h if screen else cur_y
            was_at_bottom = self._cached_effective_bottom is not None and cur_y >= self._cached_effective_bottom
            if was_at_bottom and self._standing_hwnd:
                rect = get_window_rect(self._standing_hwnd)
                if rect is None or is_window_occluded(self._standing_hwnd, skip_hwnd=int(self._window.winId())):
                    # 站立窗口消失 → 停止行走，交由常规重力处理
                    lost_title = self._standing_title
                    self._standing_hwnd = 0
                    self._standing_title = ""
                    self._standing_rect = None
                    self._cached_effective_bottom = None
                    self._walking = False
                    self._suppress_idle = False
                    self.standing_lost.emit(lost_title)
                    if not self._falling:
                        self._falling = True
                        self.falling_started.emit()
                        self._play_once("falling")
                    return
                else:
                    new_top = rect[1]
                    pet_w = self._window.width()
                    feet_l = int(new_x) + pet_w // 3
                    feet_r = int(new_x) + (2 * pet_w) // 3

                    off_edge = feet_l >= rect[2] or feet_r <= rect[0]
                    window_moved = new_top != self._cached_effective_bottom + h

                    if off_edge:
                        # 走出窗口范围 → 夹到边缘，再下落
                        if self._walk_sign > 0:
                            new_x = rect[2] - pet_w // 3
                        else:
                            new_x = rect[0] - (2 * pet_w) // 3
                        clamped = self._clamp_pos(QPoint(int(new_x), cur_y))
                        self._window.move(clamped.x(), clamped.y())
                        lost_title = self._standing_title
                        self._standing_hwnd = 0
                        self._standing_title = ""
                        self._standing_rect = None
                        self._cached_effective_bottom = None
                        self._walking = False
                        self._suppress_idle = False
                        self.standing_lost.emit(lost_title)
                        if not self._falling:
                            self._falling = True
                            self.falling_started.emit()
                            self._play_once("falling")
                        return

                    if window_moved:
                        # 窗口垂直移动 → 停止行走，下落
                        lost_title = self._standing_title
                        self._standing_hwnd = 0
                        self._standing_title = ""
                        self._standing_rect = None
                        self._cached_effective_bottom = None
                        self._walking = False
                        self._suppress_idle = False
                        self.standing_lost.emit(lost_title)
                        if not self._falling:
                            self._falling = True
                            self.falling_started.emit()
                            self._play_once("falling")
                        return

                    new_y = self._cached_effective_bottom
                    self._vy = 0.0
            else:
                # 不在已知落点上，做窗口扫描
                pet_hwnd = int(self._window.winId())
                pet_self = (int(new_x), cur_y, int(new_x) + w, cur_y + h)
                feet_l = int(new_x) + w // 3
                feet_r = int(new_x) + (2 * w) // 3
                effective_bottom = screen_bottom
                found_hwnd = 0
                found_title = ""
                for win in get_visible_windows():
                    left, top, right, bottom = win["rect"]
                    if (left == pet_self[0] and top == pet_self[1]
                            and right == pet_self[2] and bottom == pet_self[3]):
                        continue
                    if feet_l >= right or feet_r <= left:
                        continue
                    if cur_y + h <= top <= new_y + h:
                        landing = top - h
                        if landing < effective_bottom:
                            if is_window_occluded(win["hwnd"], skip_hwnd=pet_hwnd):
                                continue
                            effective_bottom = landing
                            found_hwnd = win["hwnd"]
                            found_title = win["title"][:30]
                self._cached_effective_bottom = effective_bottom
                if found_hwnd:
                    self._standing_hwnd = found_hwnd
                    self._standing_title = found_title
                    self._standing_rect = get_window_rect(found_hwnd)
                elif effective_bottom == screen_bottom:
                    self._standing_hwnd = 0
                    self._standing_title = ""
                    self._standing_rect = None

                at_bottom = new_y >= effective_bottom
                if at_bottom:
                    new_y = effective_bottom
                    self._vy = 0.0
        except Exception:
            logger.exception("[Gravity] _tick_walk scan failed")

        clamped = self._clamp_pos(QPoint(int(new_x), int(new_y)))
        self._window.move(clamped.x(), clamped.y())

        # 走到目标或边缘 → 停止行走
        if reached_target or hit_edge:
            self.walk_stop()

    def _tick_flick(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        w, h = self._window.width(), self._window.height()

        self._vy = min(self._vy + self._GRAVITY_ACCEL, self._MAX_SPEED)
        self._vx *= self._FRICTION

        step_x = max(-self._MAX_SPEED, min(self._vx, self._MAX_SPEED))
        step_y = max(-self._MAX_SPEED, min(self._vy, self._MAX_SPEED))
        new_x = self._window.x() + step_x
        new_y = self._window.y() + step_y

        left_lim = geo.left()
        right_lim = geo.right() - w
        top_lim = geo.top()
        if new_x <= left_lim:
            new_x = left_lim
            self._vx = abs(self._vx) * (-self._WALL_BOUNCE)
        elif new_x >= right_lim:
            new_x = right_lim
            self._vx = -abs(self._vx) * (-self._WALL_BOUNCE)

        if new_y < top_lim:
            new_y = top_lim
            self._vy = 0.0

        old_pet_bottom = self._window.y() + h
        new_pet_bottom = int(new_y) + h
        screen_bottom = geo.bottom() - h
        effective_bottom = screen_bottom
        found_hwnd = 0
        found_title = ""
        found_rect = None
        try:
            pet_hwnd = int(self._window.winId())
            pet_x_int = int(new_x)
            feet_l = pet_x_int + w // 3
            feet_r = pet_x_int + (2 * w) // 3
            for win in get_visible_windows():
                left, top, right, bottom = win["rect"]
                if feet_l >= right or feet_r <= left:
                    continue
                if old_pet_bottom <= top <= new_pet_bottom:
                    if not is_window_occluded(win["hwnd"], skip_hwnd=pet_hwnd):
                        landing = top - h
                        if landing < effective_bottom:
                            effective_bottom = landing
                            found_hwnd = win["hwnd"]
                            found_title = win["title"][:30]
                            found_rect = win["rect"]
        except Exception:
            pass

        at_bottom = new_y >= effective_bottom
        if at_bottom:
            new_y = effective_bottom
            self._in_flick = False
            self._vx = 0.0
            self._vy = 0.0
            self._cached_effective_bottom = effective_bottom
            self._standing_hwnd = found_hwnd
            self._standing_title = found_title
            self._standing_rect = found_rect
            self._falling = False
            self._force_standing_check = True
            self._play_once("idle")
            self.landed.emit()
            logger.info(f"[Gravity] flick landed at y={int(new_y)}, hwnd={found_hwnd}")

        self._window.move(int(new_x), int(new_y))
