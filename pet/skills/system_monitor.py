"""系统监控技能 —— 监控 CPU、内存等系统指标。"""

import psutil
import logging

logger = logging.getLogger(__name__)


class SystemMonitor:
    def __init__(self):
        self._enabled = False

    def enable(self):
        self._enabled = True
        logger.info("系统监控已启用")

    def disable(self):
        self._enabled = False
        logger.info("系统监控已禁用")

    def get_cpu_usage(self) -> float:
        if not self._enabled:
            return 0.0
        return psutil.cpu_percent(interval=0.1)

    def get_memory_usage(self) -> float:
        if not self._enabled:
            return 0.0
        return psutil.virtual_memory().percent

    def get_stats(self) -> dict:
        if not self._enabled:
            return {}
        return {
            "cpu": self.get_cpu_usage(),
            "memory": self.get_memory_usage(),
        }
