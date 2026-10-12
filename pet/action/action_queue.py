import logging

from PySide6.QtCore import QObject, QPropertyAnimation, QTimer, Signal

from pet.config import config

logger = logging.getLogger(__name__)


class ActionQueue(QObject):
    """行为队列控制器。"""

    changed = Signal()
    action_started = Signal(str)   # 动作开始执行（参数：动作名）
    action_finished = Signal(str)  # 动作正常结束；被打断时不发

    def __init__(self, actions, parent=None):
        super().__init__(parent)
        self._actions = actions
        self._queue: list[tuple[str, tuple, dict]] = []
        self._cursor = 0
        self._running = False
        self._stopped = False
        self._paused = False
        self._active_anim: QPropertyAnimation | None = None
        self._active_name: str | None = None  # 正在执行的动作名，让位时结算
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
        self._actions._stop_drive(switch_idle=False)
        # 停止所有运行中的位置动画（walk hop 等），防止 clear 后宠物继续滑行
        self._actions.stop_all_anims()
        # 停止正在播放的帧动画，防止 clear 后无人监听 animation_finished 导致卡帧
        self._actions._anim.stop()
        self._active_name = None  # 中断：不结算
        self._running = False
        self._paused = False  # 清空即恢复可运行，需要按住暂停的调用方随后自行 pause
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
        self._actions._stop_drive(switch_idle=False)
        self._actions.stop_all_anims()
        self._actions._anim.stop()
        self._active_name = None  # 中断：不结算
        self._running = False
        self._stopped = True
        self.changed.emit()

    def pause(self):
        self._disconnect_active()
        self._active_name = None  # 打断，不结算
        self._running = False
        self._paused = True
        self.changed.emit()

    def resume(self):
        if self._stopped:
            return  # 显式停止不被落地/松手等外部恢复越过
        self._paused = False
        self.changed.emit()
        self._run_next()

    def _run_next(self):
        if self._paused:
            return
        self._disconnect_active()
        self._settle_current()

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
            # 调用成功才算真正开始：失败的动作不发开始信号，也不参与结算
            self.action_started.emit(name)
            self._active_name = name
        except Exception as e:
            logger.error(f"[ActionQueue] ✗ {name} failed: {e}")
            self._run_next()
            return

        if isinstance(result, QPropertyAnimation):
            self._active_anim = result
            result.finished.connect(self._on_action_done)
            return

        if result == "drive":
            self._actions.walk_finished.connect(self._on_action_done)
            self._waiting_gravity_walk = True
            # 超时保护
            self._timeout_timer.start(self._anim_timeout_ms(kwargs))
            return

        # 贴图缺失/配置损坏时动画起不来，直接推进，别干等超时
        if not self._actions._anim.is_playing:
            logger.warning(f"[ActionQueue] '{name}' 没有可播放的帧动画，跳过")
            self._run_next()
            return

        # 时间驱动：监听 PetAnimator.animation_finished
        self._actions._anim.animation_finished.connect(self._on_action_done)
        self._waiting_anim_finished = True
        self._actions.gravity.suppress_idle = True  # 防止重力 tick 覆盖 sleep/sit/thinking
        self._timeout_timer.start(self._anim_timeout_ms(kwargs))

    def _settle_current(self):
        """上一个动作让位给下一个动作：发出 finished。中断路径只清记录，不走这里。"""
        name, self._active_name = self._active_name, None
        if name is not None:
            self.action_finished.emit(name)

    def _on_action_done(self, *args):
        self._disconnect_active()
        self._actions.gravity._tick()   #立即调用一次tick，防止队列执行下一个动作
        if self._actions.gravity.falling:
            # 动画已播完，先结算再交给 pause/resume，否则这次产出会随 pause 一起丢掉
            self._settle_current()
            return
        self._run_next()

    @staticmethod
    def _anim_timeout_ms(kwargs: dict) -> int:
        """动画超时兜底：动作自带 duration 时按它放宽。

        sleep/bathing 允许的时长（最长 216s / 270s）本来就超过默认 90s，
        不放宽它们会在超时点被强行打断。
        """
        base = max(1000, int(getattr(config, "ACTION_TIMEOUT_MS", 15000)))
        duration = (kwargs or {}).get("duration")
        if isinstance(duration, (int, float)) and duration > 0:
            return max(base, int(duration * 1000) + 2000)
        return base

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
                self._actions.walk_finished.disconnect(self._on_action_done)
            except (TypeError, RuntimeError):
                pass
            self._waiting_gravity_walk = False

    def current_action_name(self) -> str | None:
        """返回当前正在执行的动作名，无动作时返回 None。"""
        if self._running and 0 < self._cursor <= len(self._queue):
            return self._queue[self._cursor - 1][0]
        return None

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
        if args:
            parts.extend(str(a) for a in args)
        for k, v in kwargs.items():
            if k == "duration":
                parts.append(f"{v}s")
            else:
                parts.append(f"{k}={v}")
        return " ".join(parts)