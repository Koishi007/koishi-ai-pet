"""桌宠行为动作模块 —— 复合行为（移动、弹跳、重力等），通过 PetAnimator 播放帧动画。"""

from PySide6.QtCore import QPoint, QTimer, QPropertyAnimation, QEasingCurve, QObject, Signal
from PySide6.QtWidgets import QWidget
from config import config
from pet.agent.window_detector import get_visible_windows, get_window_rect, is_window_occluded


class PetActions(QObject):
    """桌宠行为控制器 —— 管理复合动作和重力系统。"""

    falling_started = Signal()  # 进入下落状态时发出

    def __init__(self, window: QWidget, animator, parent=None):
        super().__init__(parent or window)
        self._window = window
        self._anim = animator  # PetAnimator instance for frame playback

        self._win_anims: list[QPropertyAnimation] = []

        self._gravity_timer = QTimer(self)
        self._gravity_timer.timeout.connect(self._gravity_tick)
        self._gravity_step = 5
        self._gravity_interval = 30
        self._gravity_enabled = True
        self._gravity_falling = False
        self._gravity_timer.start(self._gravity_interval)

        self._scan_tick = 0
        self._cached_effective_bottom: int | None = None
        self._standing_hwnd: int = 0
        self._force_standing_check: bool = False
        self._ALIVE_CHECK_INTERVAL = 15

    def _cleanup_stopped_anims(self):
        self._win_anims[:] = [
            a for a in self._win_anims
            if a.state() == QPropertyAnimation.State.Running
        ]

    def enable_gravity(self, enabled: bool = True):
        self._gravity_enabled = enabled
        if enabled:
            self._cached_effective_bottom = None
            if not self._gravity_timer.isActive():
                self._gravity_timer.start(self._gravity_interval)

    def _gravity_tick(self):
        if not self._gravity_enabled:
            return
        if any(a.state() == QPropertyAnimation.State.Running for a in self._win_anims):
            return
        self._scan_tick += 1
        old_y = self._window.y()
        new_y = old_y + self._gravity_step

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
                        print(f"[Gravity] standing window gone (hwnd={self._standing_hwnd})")
                        self._standing_hwnd = 0
                        self._cached_effective_bottom = None
                    elif is_window_occluded(self._standing_hwnd, skip_hwnd=int(self._window.winId())):
                        print(f"[Gravity] standing window occluded (hwnd={self._standing_hwnd})")
                        self._standing_hwnd = 0
                        self._cached_effective_bottom = None
                    else:
                        new_top = rect[1]
                        pet_x = self._window.x()
                        pet_w = self._window.width()
                        # 脚部区域：宽度中间 1/3
                        feet_l = pet_x + pet_w // 3
                        feet_r = pet_x + (2 * pet_w) // 3
                        if (feet_l >= rect[2] or feet_r <= rect[0]
                                or new_top != self._cached_effective_bottom + h):
                            print(f"[Gravity] standing window moved (hwnd={self._standing_hwnd})")
                            self._standing_hwnd = 0
                            self._cached_effective_bottom = None
                if self._cached_effective_bottom is not None:
                    effective_bottom = self._cached_effective_bottom
                    at_bottom = True
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
                        print(f"[Gravity] land on: \"{win['title'][:30]}\" top={top}")
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
        self._window.move(self._window.x(), new_y)

        if at_bottom and self._gravity_falling:
            self._gravity_falling = False
            self._force_standing_check = True
            self._anim.play("idle")
        elif not at_bottom and not self._gravity_falling:
            self._gravity_falling = True
            self._anim.play("falling")
            print(f"[Gravity] falling started at y={old_y}, bottom={effective_bottom}")
            self.falling_started.emit()

    def move_to(self, start_pos, end_pos, duration=500, callback=None):
        """将窗口从 start_pos 移动到 end_pos。"""
        self._cleanup_stopped_anims()
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

    def walk(self, direction: str, distance: int, duration=2000, bounce=25):
        """弹跳行走：每 50px 一跳，跳间留 300ms 供重力检测，悬空则自动取消。"""
        if direction not in ("left", "right"):
            raise ValueError(f"direction must be 'left' or 'right', got '{direction}'")

        walk_action = f"walk_{direction}"
        self._anim.play(walk_action, loop=True)
        self._cleanup_stopped_anims()

        sign = 1 if direction == "right" else -1
        step_px = 50 * sign
        total_steps = max(1, distance // 50)
        hop_ms = max(30, duration // total_steps)
        gap_ms = 300  

        sentinel = QPropertyAnimation(self._window, b"objectName")
        sentinel.setStartValue(self._window.objectName())
        sentinel.setEndValue(self._window.objectName())
        sentinel.setDuration(100)
        sentinel.setLoopCount(-1)
        sentinel.start()

        def _hop(step: int):
            if step >= total_steps:
                self._anim.play("idle")
                sentinel.stop()
                return
            if self._gravity_falling:
                # 已被重力接管，不覆盖动画
                sentinel.stop()
                return

            base_y = self._window.y()
            from_x = self._window.x()
            to_x = from_x + step_px
            mid_x = from_x + step_px // 2

            anim = QPropertyAnimation(self._window, b"pos")
            anim.setDuration(hop_ms)
            anim.setKeyValueAt(0.0, QPoint(from_x, base_y))
            anim.setKeyValueAt(0.5, QPoint(mid_x, base_y - bounce))
            anim.setKeyValueAt(1.0, QPoint(to_x, base_y))
            anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

            def _on_hop_done():
                self._win_anims.remove(anim)
                self._cached_effective_bottom = None  # 位置变了，旧落点失效
                QTimer.singleShot(gap_ms, lambda: _hop(step + 1))

            anim.finished.connect(_on_hop_done)
            anim.start()
            self._win_anims.append(anim)

        _hop(0)
        return sentinel

    def fade_in(self, duration=300):
        """窗口淡入。"""
        self._cleanup_stopped_anims()
        self._window.setWindowOpacity(0.0)
        self._window.show()
        anim = QPropertyAnimation(self._window, b"windowOpacity")
        anim.setDuration(duration)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.start()
        self._win_anims.append(anim)
        return anim

    def fade_out(self, duration=300, callback=None):
        """窗口淡出。"""
        self._cleanup_stopped_anims()
        anim = QPropertyAnimation(self._window, b"windowOpacity")
        anim.setDuration(duration)
        anim.setStartValue(self._window.windowOpacity())
        anim.setEndValue(0.0)
        if callback:
            anim.finished.connect(callback)
        anim.start()
        self._win_anims.append(anim)
        return anim

    def bounce(self, dx=0, dy=-150, duration=500):
        self._cleanup_stopped_anims()
        self._anim.play("bounce", loop=True)
        original_pos = self._window.pos()
        target = QPoint(original_pos.x() + dx, original_pos.y() + dy)
        anim = QPropertyAnimation(self._window, b"pos")
        anim.setDuration(duration)
        anim.setStartValue(original_pos)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
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

    def sit(self, duration: float = -1):
        """坐下动作。duration 秒后自动回到 idle，-1 表示不限时。"""
        self._anim.play("sit")
        if duration > 0:
            QTimer.singleShot(int(duration * 1000), lambda: self._anim.play("idle"))

    def sleep(self, duration: float = -1):
        """睡觉动作。duration 秒后自动回到 idle，-1 表示不限时。"""
        self._anim.play("sleep")
        if duration > 0:
            QTimer.singleShot(int(duration * 1000), lambda: self._anim.play("idle"))

    def idle(self, duration: float = -1):
        """待机动作。duration 秒后自动回到 idle，-1 表示不限时。"""
        self._anim.play("idle")
        if duration > 0:
            QTimer.singleShot(int(duration * 1000), lambda: self._anim.play("idle"))

