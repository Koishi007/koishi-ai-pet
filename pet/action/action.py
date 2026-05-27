"""桌宠行为动作模块 —— 复合行为（移动、弹跳等），通过 PetAnimator 播放帧动画。"""

import logging

from PySide6.QtCore import QPoint, QTimer, QPropertyAnimation, QEasingCurve, QObject
from PySide6.QtWidgets import QWidget
from config import config
from pet.action.gravity import GravitySystem

logger = logging.getLogger(__name__)


class PetActions(QObject):
    """桌宠行为控制器 —— 管理复合动作。"""

    def __init__(self, window: QWidget, animator, parent=None):
        super().__init__(parent or window)
        self._window = window
        self._anim = animator  # PetAnimator instance for frame playback

        self._win_anims: list[QPropertyAnimation] = []

        self.gravity = GravitySystem(window, animator, self._win_anims, parent=self)

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
            raise ValueError(f"direction must be 'left' or 'right', got '{direction}'")

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

        def _hop(step: int):
            if step >= total_steps:
                self.gravity.suppress_idle = False
                self._anim.play("idle")
                sentinel.stop()
                return
            if self.gravity.falling:
                # 已被重力接管，不覆盖动画
                self.gravity.suppress_idle = False
                sentinel.stop()
                return

            base_y = self._window.y()
            from_x = self._window.x()
            to_x = from_x + step_px
            mid_x = from_x + step_px // 2

            target = self._clamp_pos(QPoint(to_x, base_y))
            to_x, base_y = target.x(), target.y()

            # 碰到屏幕边缘无法前进，停止后续
            if abs(to_x - from_x) < 10:
                self.gravity.suppress_idle = False
                sentinel.stop()
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
        self._anim.play("bounce")
        original_pos = self._window.pos()

        # 限制 dy 不让弹跳弧线最高点超出屏幕上边界
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            max_dy_up = -(original_pos.y() - screen.availableGeometry().top())
            if dy < max_dy_up:
                dy = max_dy_up

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

    def idle(self):
        self._anim.play("idle")

    def look_around(self, duration=None):
        self._anim.play("look_around", duration=duration)

    def stretch(self, duration=None):
        self._anim.play("stretch", duration=duration)

    def thinking(self, duration=None):
        self._anim.play("thinking", duration=duration)

    def caught(self):
        self._cleanup_stopped_anims()
        self._anim.play("caught")

