"""多频率 Tick 调度器 — fast、mid、slow，间隔从 config 读取。支持空闲暂停。"""

import logging
import sys
from datetime import datetime
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
    """基于 QTimer 的多频率调度器，支持空闲超时暂停。"""

    fast_tick = Signal()
    mid_tick  = Signal()
    slow_tick = Signal()
    idle_paused = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timers: dict[str, QTimer] = {}
        self._manually_paused: set[str] = set()
        self._idle_paused = False
        self._idle_check = QTimer(self)
        self._idle_check.timeout.connect(self._check_idle)
        self._idle_timeout_ms = config.SCHEDULER_IDLE_TIMEOUT_MS
        logger.debug("[Scheduler] Created")

    def start(self, fast_ms: int | None = None, mid_ms: int | None = None,
              slow_ms: int | None = None):
        fast_ms = fast_ms if fast_ms is not None else config.SCHEDULER_FAST_MS
        mid_ms = mid_ms if mid_ms is not None else config.SCHEDULER_MID_MS
        slow_ms = slow_ms if slow_ms is not None else config.SCHEDULER_SLOW_MS
        self.stop()
        ts = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{ts}] [Scheduler] start(fast={fast_ms}ms, mid={mid_ms}ms, slow={slow_ms}ms)")

        if fast_ms > 0:
            t = QTimer(self)
            t.setInterval(fast_ms)
            t.timeout.connect(self.fast_tick)
            t.start()
            self._timers["fast"] = t

        if mid_ms > 0:
            t = QTimer(self)
            t.setInterval(mid_ms)
            t.timeout.connect(self.mid_tick)
            t.start()
            self._timers["mid"] = t

        if slow_ms > 0:
            t = QTimer(self)
            t.setInterval(slow_ms)
            t.timeout.connect(self.slow_tick)
            t.start()
            self._timers["slow"] = t

        # 空闲检测：每 30 秒轮询一次系统输入状态
        self._idle_check.start(30000)
        self._idle_paused = False

    def stop(self):
        self._idle_check.stop()
        if not self._timers:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        names = list(self._timers.keys())
        for t in self._timers.values():
            t.stop()
            t.deleteLater()
        self._timers.clear()
        self._idle_paused = False
        logger.info(f"[{ts}] [Scheduler] stop() — stopped {names}")

    def is_running(self) -> bool:
        return bool(self._timers)

    def _check_idle(self):
        """检查系统空闲状态，超时则暂停三个调度计时器。"""
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
                    continue  # 被手动暂停的定时器，空闲恢复时不自动重启
                t.start()
            self._idle_paused = False
            self.idle_paused.emit(False)
            logger.info(f"[Scheduler] activity resumed — timers restarted")

    def is_idle_paused(self) -> bool:
        return self._idle_paused

    _VALID_NAMES = ("fast", "mid", "slow")

    def pause(self, name: str):
        """暂停指定定时器（fast/mid/slow）。"""
        if name not in self._VALID_NAMES:
            raise ValueError(f"Invalid timer name '{name}', must be one of {self._VALID_NAMES}")
        t = self._timers.get(name)
        if t and t.isActive():
            t.stop()
        self._manually_paused.add(name)
        logger.info(f"[Scheduler] {name}_tick paused")

    def resume(self, name: str):
        """恢复指定定时器（fast/mid/slow）。"""
        if name not in self._VALID_NAMES:
            raise ValueError(f"Invalid timer name '{name}', must be one of {self._VALID_NAMES}")
        t = self._timers.get(name)
        if t and not t.isActive():
            t.start()
        self._manually_paused.discard(name)
        logger.info(f"[Scheduler] {name}_tick resumed")

    def is_paused(self, name: str) -> bool:
        """指定定时器是否被手动暂停。"""
        if name not in self._VALID_NAMES:
            raise ValueError(f"Invalid timer name '{name}', must be one of {self._VALID_NAMES}")
        return name in self._manually_paused

    # ── 便捷别名 ──

    def pause_mid(self):
        self.pause("mid")

    def resume_mid(self):
        self.resume("mid")

    def is_mid_paused(self) -> bool:
        return self.is_paused("mid")
