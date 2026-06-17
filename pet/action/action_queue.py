
import logging

from PySide6.QtCore import QObject, QPropertyAnimation, QTimer, Signal

from config import config

logger = logging.getLogger(__name__)


class ActionQueue(QObject):
    """行为队列控制器。"""

    changed = Signal()

    def __init__(self, actions, parent=None):
        super().__init__(parent)
        self._actions = actions
        self._queue: list[tuple[str, tuple, dict]] = []
        self._cursor = 0
        self._running = False
        self._stopped = False
        self._paused = False
        self._active_anim: QPropertyAnimation | None = None
        self._waiting_anim_finished: bool = False
        self._waiting_gravity_walk: bool = False
        # 超时保护：防止循环动作永不发 animation_finished 导致队列永久阻塞
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_action_timeout)

    def enqueue(self, method_name: str, *args, **kwargs):
        self._queue.append((method_name, args, kwargs))
        self.changed.emit()
        if not self._running and not self._stopped and not self._paused:
            self._run_next()

    def clear(self):
        self._queue.clear()
        self._cursor = 0
        self._disconnect_active()
        self._running = False
        self.changed.emit()

    def start(self):
        self._disconnect_active()
        self._running = False
        self._stopped = False
        self._cursor = 0
        self.changed.emit()
        if self._cursor < len(self._queue):
            self._run_next()

    def stop(self):
        self._disconnect_active()
        self._running = False
        self._stopped = True
        self.changed.emit()

    def pause(self):
        self._disconnect_active()
        self._running = False
        self._paused = True
        self.changed.emit()

    def resume(self):
        self._paused = False
        self.changed.emit()
        self._run_next()

    def _run_next(self):
        if self._paused:
            return
        self._disconnect_active()

        if self._cursor >= len(self._queue):
            self._queue.clear()
            self._cursor = 0
            self._running = False
            if not self._actions.gravity.falling:
                self._actions.idle()
            self.changed.emit()
            return

        self._running = True
        name, args, kwargs = self._queue[self._cursor]

        if self._actions.gravity.falling:
            self.pause()  # 等 PetWindow 的 landed→resume 继续
            return

        self._cursor += 1
        self.changed.emit()
        method = getattr(self._actions, name, None)

        if method is None:
            self._run_next()
            return

        try:
            logger.info(f"[ActionQueue] ▶ {self._format(name, args, kwargs)}")
            result = method(*args, **kwargs)
        except Exception as e:
            logger.error(f"[ActionQueue] ✗ {name} failed: {e}")
            self._run_next()
            return

        if isinstance(result, QPropertyAnimation):
            # 动画驱动：监听 QPropertyAnimation.finished
            self._active_anim = result
            result.finished.connect(self._on_action_done)
            return

        if result == "normal_walk":
            # 重力行走：监听 GravitySystem.walk_finished
            self._actions.gravity.walk_finished.connect(self._on_action_done)
            self._waiting_gravity_walk = True
            # 超时保护
            timeout_ms = max(1000, int(getattr(config, "ACTION_TIMEOUT_MS", 15000)))
            self._timeout_timer.start(timeout_ms)
            return

        # 时间驱动：监听 PetAnimator.animation_finished
        self._actions._anim.animation_finished.connect(self._on_action_done)
        self._waiting_anim_finished = True
        self._actions.gravity.suppress_idle = True  # 防止重力 tick 覆盖 sleep/sit/thinking
        # 启动超时定时器（仅针对时间驱动动作，如 LLM 未提供 duration 则可能循环播放）
        timeout_ms = max(1000, int(getattr(config, "ACTION_TIMEOUT_MS", 15000)))
        self._timeout_timer.start(timeout_ms)

    def _on_action_done(self, *args):
        self._disconnect_active()
        self._actions.gravity._tick()   #立即调用一次tick，防止队列执行下一个动作
        if self._actions.gravity.falling:
            return  # PetWindow 的 falling_started→pause + landed→resume 会继续队列
        self._run_next()

    def _on_action_timeout(self):
        """动作超时保护"""
        if not self._waiting_anim_finished and self._active_anim is None and not self._waiting_gravity_walk:
            return
        cur = self._queue[self._cursor - 1] if 0 < self._cursor <= len(self._queue) else None
        name = cur[0] if cur else "<unknown>"
        logger.warning(
            f"[ActionQueue] ⚠ action '{name}' timed out after "
            f"{self._timeout_timer.interval()}ms, advancing to next"
        )
        self._disconnect_active()
        self._run_next()

    def _disconnect_active(self):
        self._actions.gravity.suppress_idle = False
        if self._timeout_timer.isActive():
            self._timeout_timer.stop()
        if self._active_anim is not None:
            try:
                self._active_anim.finished.disconnect(self._on_action_done)
            except (TypeError, RuntimeError):
                pass
            self._active_anim.stop()
            self._active_anim = None
        if self._waiting_anim_finished:
            try:
                self._actions._anim.animation_finished.disconnect(self._on_action_done)
            except (TypeError, RuntimeError):
                pass
            self._waiting_anim_finished = False
        if self._waiting_gravity_walk:
            try:
                self._actions.gravity.walk_finished.disconnect(self._on_action_done)
            except (TypeError, RuntimeError):
                pass
            self._waiting_gravity_walk = False

    def describe(self) -> list[str]:
        """返回队列可视化列表，用于调试面板展示。"""
        result = []
        for i, (name, args, kwargs) in enumerate(self._queue):
            if i == self._cursor - 1 and self._running:
                prefix = "▶ "
            elif i < self._cursor:
                prefix = "· "
            else:
                prefix = "  "
            result.append(f"{prefix}{self._format(name, args, kwargs)}")
        return result

    @staticmethod
    def _format(name: str, args: tuple, kwargs: dict) -> str:
        parts = [name]
        if name == "normal_walk" or name == "jump_walk":
            parts.append(f"{args[0]} {args[1]}px" if len(args) >= 2 else "")
        elif name == "move_to":
            parts.append(f"({args[0].x()},{args[0].y()})→({args[1].x()},{args[1].y()})" if len(args) >= 2 else "")
        elif name == "bounce":
            d = kwargs.get("direction", "right")
            dist = kwargs.get("distance", 0)
            parts.append(f"{d} {dist}px h={kwargs.get('height', 150)}")
        elif name in ("sit", "sleep", "thinking"):
            dur = kwargs.get("duration")
            if dur:
                parts.append(f"{dur}s")
        return " ".join(parts)
