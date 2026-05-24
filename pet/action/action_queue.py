

from PySide6.QtCore import QObject, QTimer, QPropertyAnimation, Signal


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
        self._active_anim: QPropertyAnimation | None = None
        self._next_timer: QTimer | None = None

    def enqueue(self, method_name: str, *args, **kwargs):
        self._queue.append((method_name, args, kwargs))
        self.changed.emit()
        if not self._running and not self._stopped:
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

    def _run_next(self):
        self._disconnect_active()

        if self._cursor >= len(self._queue):
            self._queue.clear()
            self._cursor = 0
            self._running = False
            if not self._actions._gravity_falling:
                self._actions.idle()
            self.changed.emit()
            return

        self._running = True
        name, args, kwargs = self._queue[self._cursor]
        self._cursor += 1
        self.changed.emit()
        method = getattr(self._actions, name, None)

        if method is None:
            self._run_next()
            return

        try:
            print(f"[ActionQueue] ▶ {self._format(name, args, kwargs)}")
            result = method(*args, **kwargs)
        except Exception as e:
            print(f"[ActionQueue] ✗ {name} failed: {e}")
            self._run_next()
            return

        if isinstance(result, QPropertyAnimation):
            self._active_anim = result
            result.finished.connect(self._on_action_done)
            return

        duration = kwargs.get("duration", -1)
        if duration > 0:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._on_action_done)
            timer.start(int(duration * 1000))
            self._next_timer = timer
            return

        if duration == -1:
            self._running = False
            return

        self._run_next()

    def _on_action_done(self):
        self._active_anim = None
        self._next_timer = None
        self._run_next()

    def _disconnect_active(self):
        if self._active_anim is not None:
            try:
                self._active_anim.finished.disconnect(self._on_action_done)
            except (TypeError, RuntimeError):
                pass
            self._active_anim.stop()
            self._active_anim = None
        if self._next_timer is not None:
            self._next_timer.stop()
            self._next_timer = None

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
        if name == "walk":
            parts.append(f"{args[0]} {args[1]}px" if len(args) >= 2 else "")
        elif name == "move_to":
            parts.append(f"({args[0].x()},{args[0].y()})→({args[1].x()},{args[1].y()})" if len(args) >= 2 else "")
        elif name == "bounce":
            parts.append(f"dx={kwargs.get('dx',0)} dy={kwargs.get('dy',-150)}")
        elif name in ("sit", "sleep", "idle"):
            d = kwargs.get("duration", -1)
            if d > 0:
                parts.append(f"{d}s")
        return " ".join(parts)
