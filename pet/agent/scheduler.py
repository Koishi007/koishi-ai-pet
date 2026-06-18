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

    _VALID_NAMES = ("fast", "mid", "slow")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timers: dict[str, QTimer] = {}
        self._manually_paused: set[str] = set()
        self._idle_paused = False
        self._idle_check = QTimer(self)
        self._idle_check.timeout.connect(self._check_idle)
        self._idle_timeout_ms = config.SCHEDULER_IDLE_TIMEOUT_MS
        self._initialized = False
        logger.debug("[Scheduler] Created")

    def _get_signal_map(self):
        return {"fast": self.fast_tick, "mid": self.mid_tick, "slow": self.slow_tick}

    def init(self, auto_fast: bool = True, auto_mid: bool = True, auto_slow: bool = True):
        """初始化调度器：始终创建全部三个 timer，auto_xxx 控制初始是否运行。

        Args:
            auto_fast: fast_tick 是否自动启动
            auto_mid:  mid_tick 是否自动启动
            auto_slow: slow_tick 是否自动启动
        """
        # 清理旧 timer
        for t in self._timers.values():
            t.stop()
            t.deleteLater()
        self._timers.clear()
        self._manually_paused.clear()

        signal_map = self._get_signal_map()
        intervals = {"fast": config.SCHEDULER_FAST_MS, "mid": config.SCHEDULER_MID_MS, "slow": config.SCHEDULER_SLOW_MS}
        auto_flags = {"fast": auto_fast, "mid": auto_mid, "slow": auto_slow}

        for name in self._VALID_NAMES:
            ms = intervals[name]
            t = QTimer(self)
            t.setInterval(ms)
            t.timeout.connect(signal_map[name])
            self._timers[name] = t

            if auto_flags[name]:
                t.start()
            else:
                self._manually_paused.add(name)

        # 空闲检测：每 30 秒轮询一次系统输入状态
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

    def stop(self):
        """停止全部定时器与空闲检测。"""
        self._idle_check.stop()
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

    def is_running(self) -> bool:
        """调度器是否已初始化。"""
        return self._initialized

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


    def pause_mid(self):
        self.pause("mid")

    def resume_mid(self):
        self.resume("mid")

    def is_mid_paused(self) -> bool:
        return self.is_paused("mid")