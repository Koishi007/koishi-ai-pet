"""复合行为（移动、弹跳等），通过 PetAnimator 播放帧动画。"""

import logging

from PySide6.QtCore import QPoint, QTimer, QPropertyAnimation, QEasingCurve, QObject, Signal
from PySide6.QtWidgets import QWidget
from pet.config import config
from pet.action.gravity import GravitySystem

logger = logging.getLogger(__name__)


class PetActions(QObject):
    """桌宠行为控制器 —— 管理复合动作。"""

    walk_finished = Signal()  # 普通行走完成时发出

    def __init__(self, window: QWidget, animator, parent=None):
        super().__init__(parent or window)
        self._window = window
        self._anim = animator

        self._win_anims: list[QPropertyAnimation] = []

        self.gravity = GravitySystem(window, animator, self._win_anims, parent=self)

        self._walking: bool = False
        self._walk_sign: int = 1       # 1=右, -1=左
        self._walk_speed: float = 3.0  # px/tick（约 100px/s @ 30ms tick）
        self._walk_target_x: int = 0
        self._walk_start_x: int = 0
        self._walk_timer = QTimer(self)
        self._walk_timer.setInterval(30)

    def _clamp_pos(self, pos: QPoint) -> QPoint:
        """将坐标限制在屏幕可用范围内。"""
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if not screen:
            return pos
        geo = screen.availableGeometry()
        x = max(geo.left(), min(pos.x(), geo.right() - self._window.width()))
        y = max(geo.top(), min(pos.y(), geo.bottom() - self._window.height()))
        return QPoint(x, y)

    def _cleanup_stopped_anims(self):
        self._win_anims[:] = [
            a for a in self._win_anims
            if a.state() == QPropertyAnimation.State.Running
        ]

    def stop_all_anims(self):
        """停止所有运行中的窗口位置动画（walk hop、move_to 等），防止 clear/stop 后宠物继续滑行。"""
        for anim in self._win_anims:
            if anim.state() == QPropertyAnimation.State.Running:
                anim.stop()
        self._win_anims.clear()

    def move_to(self, start_pos, end_pos, duration=500, callback=None):
        """将窗口从 start_pos 移动到 end_pos，自动限制在屏幕内。"""
        self._cleanup_stopped_anims()
        start_pos = self._clamp_pos(start_pos)
        end_pos = self._clamp_pos(end_pos)
        logger.info(f"from {start_pos}  move to: {end_pos}")
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

    def walk(self, direction: str = "right", distance: int = 400, bounce=25):
        """弹跳行走：每 50px 一跳，匀速，跳间留 250ms 供重力检测，悬空则自动取消。"""
        if direction not in ("left", "right"):
            logger.warning(f"[PetActions] walk: invalid direction {direction!r}, fallback to 'right'")
            direction = "right"

        walk_action = f"walk_{direction}"
        self._anim.play(walk_action)
        self._cleanup_stopped_anims()
        self.gravity.suppress_idle = True

        sign = 1 if direction == "right" else -1
        step_px = 50 * sign
        total_steps = max(1, distance // 50)
        hop_ms = 150   # 每跳动画时长，固定保证匀速
        gap_ms = 250   # 跳间停顿，供重力检测

        sentinel = QPropertyAnimation(self._window, b"objectName")
        sentinel.setStartValue(self._window.objectName())
        sentinel.setEndValue(self._window.objectName())
        sentinel.setDuration(100)
        sentinel.setLoopCount(-1)
        sentinel.start()

        def _finish(switch_idle: bool):
            """统一收尾：恢复 suppress_idle、按需切回 idle、停止 sentinel。"""
            self.gravity.suppress_idle = False
            if switch_idle:
                self._anim.play("idle")
            sentinel.stop()

        def _hop(step: int):
            if sentinel.state() != QPropertyAnimation.State.Running:
                # clear/stop 已终止此次行走，忽略残留的 singleShot 回调
                self.gravity.suppress_idle = False
                return
            if step >= total_steps:
                _finish(switch_idle=True)
                return
            if self.gravity.falling:
                # 已被重力接管，不覆盖动画（gravity 落地时会自行切 idle）
                _finish(switch_idle=False)
                return

            base_y = self._window.y()
            from_x = self._window.x()
            to_x = from_x + step_px
            mid_x = from_x + step_px // 2

            target = self._clamp_pos(QPoint(to_x, base_y))
            to_x, base_y = target.x(), target.y()

            # 碰到屏幕边缘无法前进：必须切回 idle，否则会卡在 walk 静帧上
            if abs(to_x - from_x) < 10:
                _finish(switch_idle=True)
                return

            anim = QPropertyAnimation(self._window, b"pos")
            anim.setDuration(hop_ms)
            anim.setKeyValueAt(0.0, QPoint(from_x, base_y))
            anim.setKeyValueAt(0.5, QPoint(mid_x, base_y - bounce))
            anim.setKeyValueAt(1.0, QPoint(to_x, base_y))
            anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

            def _on_hop_done():
                self._win_anims.remove(anim)
                self.gravity._cached_effective_bottom = None  # 位置变了，旧落点失效
                QTimer.singleShot(gap_ms, lambda: _hop(step + 1))

            anim.finished.connect(_on_hop_done)
            anim.start()
            self._win_anims.append(anim)

        # 首次 _hop 异步化：确保 walk() 已 return sentinel 给 ActionQueue
        # 完成 finished 信号连接后，再可能触发 sentinel.stop()。
        # 否则若起点就在屏幕边缘，_hop(0) 会同步 stop sentinel，
        # 而 QPropertyAnimation(loopCount=-1) 在 stop 时会同步 emit finished，
        # 那一刻 ActionQueue 还没连上信号 → 队列永久阻塞 + 桌宠停在 walk 静帧。
        QTimer.singleShot(0, lambda: _hop(0))
        return sentinel

    def drive(self, direction: str = "right", distance: int = 400):
        """骑小电驴"""
        if direction not in ("left", "right"):
            logger.warning(f"[PetActions] drive: invalid direction {direction!r}, fallback to 'right'")
            direction = "right"

        walk_action = f"driving_{direction}"
        self._anim.play(walk_action)
        self._cleanup_stopped_anims()

        self._walk_sign = 1 if direction == "right" else -1
        self._walk_start_x = self._window.x()
        self._walk_target_x = self._walk_start_x + self._walk_sign * distance
        self._walking = True
        self.gravity.suppress_idle = True
        self.gravity.pause_timer()  # 暂停重力，由本方法 timer 接管

        self._walk_timer.timeout.connect(self._driving_tick)
        self._walk_timer.start()
        logger.info(f"[PetActions] drive dir={direction} dist={distance} "
                     f"from={self._walk_start_x} to={self._walk_target_x}")
        return "drive"

    def _stop_drive(self, switch_idle: bool = True):
        """停止移动，恢复 gravity timer，emit walk_finished。"""
        if not self._walking:
            return
        self._walking = False
        self._walk_timer.stop()
        try:
            self._walk_timer.timeout.disconnect(self._driving_tick)
        except (TypeError, RuntimeError):
            pass
        self.gravity.suppress_idle = False
        self.gravity.resume_timer()
        if switch_idle:
            self._anim.play("idle")
        logger.info(f"[PetActions] _stop_drive at x={self._window.x()}")
        self.walk_finished.emit()

    def _driving_tick(self):
        """开车行驶 tick"""
        from PySide6.QtWidgets import QApplication
        from pet.brain.window_detector import get_visible_windows, get_window_rect, is_window_occluded

        g = self.gravity
        cur_x = self._window.x()
        cur_y = self._window.y()
        w = self._window.width()
        h = self._window.height()

        step = self._walk_speed * self._walk_sign
        new_x = cur_x + step

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

        # 垂直方向：复用 gravity 的重力加速度和地面检测
        g._vy = min(g._vy + g._GRAVITY_ACCEL, g._FALL_TERMINAL)
        new_y = cur_y + g._vy

        dpr = screen.devicePixelRatio() if screen else 1.0
        to_logical = lambda rect: tuple(v / dpr for v in rect)

        try:
            screen_bottom = screen.availableGeometry().bottom() - h if screen else cur_y
            was_at_bottom = g._cached_effective_bottom is not None and cur_y >= g._cached_effective_bottom
            if was_at_bottom and g._standing_hwnd:
                rect = get_window_rect(g._standing_hwnd)
                if rect is None or is_window_occluded(g._standing_hwnd, skip_hwnd=int(self._window.winId())):
                    # 站立窗口消失 → 停止行走，交由重力处理下落
                    lost_title = g._standing_title
                    g._standing_hwnd = 0
                    g._standing_title = ""
                    g._standing_rect = None
                    g._cached_effective_bottom = None
                    self._stop_drive(switch_idle=False)
                    g.standing_lost.emit(lost_title)
                    if not g._falling:
                        g._falling = True
                        g.falling_started.emit()
                        g._play_once("falling")
                    return
                else:
                    l_rect = to_logical(rect)
                    new_top = l_rect[1]
                    pet_w = self._window.width()
                    feet_l = int(new_x) + pet_w // 3
                    feet_r = int(new_x) + (2 * pet_w) // 3

                    off_edge = feet_l >= l_rect[2] or feet_r <= l_rect[0]
                    window_moved = new_top != g._cached_effective_bottom + h

                    if off_edge:
                        # 走出窗口范围 → 夹到边缘，再下落
                        if self._walk_sign > 0:
                            new_x = l_rect[2] - pet_w // 3
                        else:
                            new_x = l_rect[0] - (2 * pet_w) // 3
                        clamped = g._clamp_pos(QPoint(int(new_x), cur_y))
                        self._window.move(clamped.x(), clamped.y())
                        lost_title = g._standing_title
                        g._standing_hwnd = 0
                        g._standing_title = ""
                        g._standing_rect = None
                        g._cached_effective_bottom = None
                        self._stop_drive(switch_idle=False)
                        g.standing_lost.emit(lost_title)
                        if not g._falling:
                            g._falling = True
                            g.falling_started.emit()
                            g._play_once("falling")
                        return

                    if window_moved:
                        # 窗口垂直移动 → 停止行走，下落
                        lost_title = g._standing_title
                        g._standing_hwnd = 0
                        g._standing_title = ""
                        g._standing_rect = None
                        g._cached_effective_bottom = None
                        self._stop_drive(switch_idle=False)
                        g.standing_lost.emit(lost_title)
                        if not g._falling:
                            g._falling = True
                            g.falling_started.emit()
                            g._play_once("falling")
                        return

                    new_y = g._cached_effective_bottom
                    g._vy = 0.0
            else:
                pet_hwnd = int(self._window.winId())
                pet_self = (int(new_x), cur_y, int(new_x) + w, cur_y + h)
                feet_l = int(new_x) + w // 3
                feet_r = int(new_x) + (2 * w) // 3
                effective_bottom = screen_bottom
                found_hwnd = 0
                found_title = ""
                for win in get_visible_windows():
                    left, top, right, bottom = to_logical(win["rect"])
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
                g._cached_effective_bottom = effective_bottom
                if found_hwnd:
                    g._standing_hwnd = found_hwnd
                    g._standing_title = found_title
                    g._standing_rect = to_logical(get_window_rect(found_hwnd))
                elif effective_bottom == screen_bottom:
                    g._standing_hwnd = 0
                    g._standing_title = ""
                    g._standing_rect = None

                at_bottom = new_y >= effective_bottom
                if at_bottom:
                    new_y = effective_bottom
                    g._vy = 0.0
        except Exception:
            logger.exception("[PetActions] _driving_tick scan failed")

        clamped = g._clamp_pos(QPoint(int(new_x), int(new_y)))
        self._window.move(clamped.x(), clamped.y())

        if reached_target or hit_edge:
            self._stop_drive()

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
        self._cleanup_stopped_anims()
        anim = QPropertyAnimation(self._window, b"windowOpacity")
        anim.setDuration(duration)
        anim.setStartValue(self._window.windowOpacity())
        anim.setEndValue(0.0)
        if callback:
            anim.finished.connect(callback)
        anim.start()
        self._win_anims.append(anim)
    
        # 15s 后若仍处于不可见状态，自动 fade_in 找回宠物
        QTimer.singleShot(15000, self._fade_in_safety_check)
        return anim
    

    def bounce(self, direction="right", distance=0, height=150, duration=800):
        self._cleanup_stopped_anims()
        self._anim.play("bounce")
        original_pos = self._window.pos()

        sign = 1 if direction == "right" else -1
        dx = sign * distance

        # 限制不让弹跳弧线最高点超出屏幕上边界
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            max_height = original_pos.y() - screen.availableGeometry().top()
            if height > max_height:
                height = max_height
        dy = -height

        target = self._clamp_pos(QPoint(original_pos.x() + dx, original_pos.y() + dy))
        anim = QPropertyAnimation(self._window, b"pos")
        anim.setDuration(duration)
        anim.setStartValue(original_pos)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._win_anims.append(anim)
        return anim

    def sit(self, duration=None):
        self._anim.play("sit", duration=duration)

    def sleep(self, duration=None):
        self._anim.play("sleep", duration=duration)

    def idle(self, *_, **_kw):
        self._anim.play("idle")

    def shake_arms(self, *_, **_kw):
        self._anim.play("shake_arms")

    def look_around(self, *_, **_kw):
        self._anim.play("look_around")

    def stretch(self, *_, **_kw):
        self._anim.play("stretch")

    def thinking(self, duration=None):
        self._anim.play("thinking", duration=duration)

    def grim(self, *_, **_kw):
        self._anim.play("grim")

    def grabbed(self, *_, **_kw):
        self._cleanup_stopped_anims()
        self._anim.play("grabbed")

    def fishing(self, *_, **_kw):
        self._anim.play("fishing")

    def calling(self, duration=None):
        self._anim.play("calling", duration=duration)

    def finger_heart(self, duration=None):
        self._anim.play("finger_heart", duration=duration)

    def rotate(self, *_, **_kw):
        self._anim.play("rotate")
        
    def _fade_in_safety_check(self):
        try:
            if self._window.windowOpacity() < 0.1:
                logger.warning("[PetActions] fade_out safety net triggered, forcing fade_in")
                self.fade_in()
        except RuntimeError:
            pass    

