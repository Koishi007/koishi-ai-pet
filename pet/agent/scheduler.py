"""多频率 Tick 调度器"""

import logging
import sys
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from config import config

logger = logging.getLogger(__name__)


def _get_idle_ms() -> int:
    """返回系统级无输入空闲时长（毫秒），Windows / macOS 双平台。"""
    if sys.platform == "darwin":
        import ctypes
        import ctypes.util
        cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
        seconds = cg.CGEventSourceSecondsSinceLastEventType(
            ctypes.c_int(1),  # kCGEventSourceStateCombinedSessionState
            ctypes.c_ulong(0xFFFFFFFFFFFFFFFF),  # ~0 = kCGAnyInputEventType
        )
        return int(seconds * 1000)
    else:
        import ctypes
        from ctypes import wintypes

        class _LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return ctypes.windll.kernel32.GetTickCount() - lii.dwTime
        return 0


class Scheduler(QObject):
    """基于注册机制的多频率调度器"""

    idle_paused = Signal(bool)

    _VALID_NAMES = ("fast", "mid", "slow")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._callbacks: dict[str, list[Callable]] = {n: [] for n in self._VALID_NAMES}
        self._timers: dict[str, QTimer] = {}
        self._manually_paused: set[str] = set()
        self._idle_paused = False
        self._idle_check = QTimer(self)
        self._idle_check.timeout.connect(self._check_idle)
        self._idle_timeout_ms = config.SCHEDULER_IDLE_TIMEOUT_MS
        self._initialized = False
        self._alarm_timers: dict[str, QTimer] = {}
        logger.debug("[Scheduler] Created")

    # 注册 / 注销 

    def register(self, name: str, callback: Callable[[], None]):
        """注册一个回调到指定 tick（fast/mid/slow），同一回调重复注册会被忽略。"""
        if name not in self._VALID_NAMES:
            raise ValueError(f"Invalid tick name '{name}', must be one of {self._VALID_NAMES}")
        if callback not in self._callbacks[name]:
            self._callbacks[name].append(callback)
            logger.debug(f"[Scheduler] registered {callback.__name__} -> {name}_tick")

    def unregister(self, name: str, callback: Callable[[], None]):
        """注销指定 tick 上的回调。"""
        if name not in self._VALID_NAMES:
            raise ValueError(f"Invalid tick name '{name}', must be one of {self._VALID_NAMES}")
        try:
            self._callbacks[name].remove(callback)
            logger.debug(f"[Scheduler] unregistered {callback.__name__} from {name}_tick")
        except ValueError:
            pass

    def _fire(self, name: str):
        """触发指定 tick 上所有已注册的回调（遍历副本，防回调内 register/unregister 修改列表）。"""
        for cb in list(self._callbacks[name]):
            try:
                cb()
            except Exception:
                logger.exception(f"[Scheduler] {name}_tick callback {cb.__name__} error")

    # 生命周期 

    def init(self, auto_fast: bool = True, auto_mid: bool = True, auto_slow: bool = True):
        for t in self._timers.values():
            t.stop()
            t.deleteLater()
        self._timers.clear()
        self._manually_paused.clear()

        intervals = {
            "fast": config.SCHEDULER_FAST_MS,
            "mid": config.SCHEDULER_MID_MS,
            "slow": config.SCHEDULER_SLOW_MS,
        }
        auto_flags = {"fast": auto_fast, "mid": auto_mid, "slow": auto_slow}

        for name in self._VALID_NAMES:
            t = QTimer(self)
            t.setInterval(intervals[name])
            t.timeout.connect(lambda n=name: self._fire(n))
            self._timers[name] = t
            if auto_flags[name]:
                t.start()
            else:
                self._manually_paused.add(name)

        self._idle_check.start(30000)
        self._idle_paused = False
        self._initialized = True

        ts = datetime.now().strftime("%H:%M:%S")
        logger.info(
            f"[{ts}] [Scheduler] init — "
            f"fast={'ON' if auto_fast else 'PAUSED'}({intervals['fast']}ms), "
            f"mid={'ON' if auto_mid else 'PAUSED'}({intervals['mid']}ms), "
            f"slow={'ON' if auto_slow else 'PAUSED'}({intervals['slow']}ms)"
        )

    def update_config(self):
        """运行时更新调度器配置（设置界面修改调度参数后调用）。"""
        if not self._initialized:
            return
        auto_fast = "fast" not in self._manually_paused
        auto_mid = "mid" not in self._manually_paused
        auto_slow = "slow" not in self._manually_paused
        self._idle_timeout_ms = config.SCHEDULER_IDLE_TIMEOUT_MS
        self.init(auto_fast=auto_fast, auto_mid=auto_mid, auto_slow=auto_slow)
        logger.info("[Scheduler] config updated — timers rebuilt")


    def stop(self):
        self._idle_check.stop()
        for key in list(self._alarm_timers):
            self._cleanup_alarm_timer(key)
        if not self._timers:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        names = list(self._timers.keys())
        for t in self._timers.values():
            t.stop()
            t.deleteLater()
        self._timers.clear()
        self._manually_paused.clear()
        self._idle_paused = False
        self._initialized = False
        logger.info(f"[{ts}] [Scheduler] stop() — stopped {names}")

    # 空闲暂停

    def _check_idle(self):
        idle_ms = _get_idle_ms()
        if idle_ms >= self._idle_timeout_ms and not self._idle_paused:
            for t in self._timers.values():
                t.stop()
            self._idle_paused = True
            self.idle_paused.emit(True)
            logger.info(f"[Scheduler] idle {idle_ms // 1000}s >= {self._idle_timeout_ms // 1000}s — paused")
        elif idle_ms < self._idle_timeout_ms and self._idle_paused:
            for name, t in self._timers.items():
                if self.is_paused(name):
                    continue
                t.start()
            self._idle_paused = False
            self.idle_paused.emit(False)
            logger.info(f"[Scheduler] activity resumed — timers restarted")

    def is_idle_paused(self) -> bool:
        return self._idle_paused

    # 手动暂停 / 恢复 

    def pause(self, name: str):
        if name not in self._VALID_NAMES:
            raise ValueError(f"Invalid timer name '{name}', must be one of {self._VALID_NAMES}")
        if name not in self._timers:
            logger.warning(f"[Scheduler] pause('{name}') ignored — scheduler not initialized")
            return
        t = self._timers[name]
        if t.isActive():
            t.stop()
        self._manually_paused.add(name)
        logger.info(f"[Scheduler] {name}_tick paused")

    def resume(self, name: str):
        if name not in self._VALID_NAMES:
            raise ValueError(f"Invalid timer name '{name}', must be one of {self._VALID_NAMES}")
        if name not in self._timers:
            logger.warning(f"[Scheduler] resume('{name}') ignored — scheduler not initialized")
            return
        t = self._timers[name]
        if not t.isActive():
            t.start()
        self._manually_paused.discard(name)
        logger.info(f"[Scheduler] {name}_tick resumed")

    def is_paused(self, name: str) -> bool:
        if name not in self._VALID_NAMES:
            raise ValueError(f"Invalid timer name '{name}', must be one of {self._VALID_NAMES}")
        return name in self._manually_paused

    def pause_mid(self):
        self.pause("mid")

    def resume_mid(self):
        self.resume("mid")

    def is_mid_paused(self) -> bool:
        return self.is_paused("mid")

    def schedule_at(self, timestamp_ms: int, callback: Callable[[], None],
                    key: str | None = None):
        """在指定绝对时间戳（ms）精准触发一次性回调。
        已过期则立即触发，同 ID 重复注册会覆盖旧的。"""
        import time
        now_ms = int(time.time() * 1000)
        delay_ms = max(0, timestamp_ms - now_ms)

        if key is None:
            key = str(id(callback))

        # 覆盖旧 timer
        self._cleanup_alarm_timer(key)

        def _fire():
            self._cleanup_alarm_timer(key)
            try:
                callback()
            except Exception:
                logger.exception(f"[Scheduler] alarm callback {key} error")

        t = QTimer(self)
        t.setSingleShot(True)
        t.timeout.connect(_fire)
        t.start(delay_ms)
        self._alarm_timers[key] = t
        logger.info(f"[Scheduler] alarm '{key}' scheduled in {delay_ms}ms")

    def _cleanup_alarm_timer(self, key: str):
        """Stop and delete a single alarm timer by key (idempotent)."""
        t = self._alarm_timers.pop(key, None)
        if t is not None:
            t.stop()
            t.deleteLater()
