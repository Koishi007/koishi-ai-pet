"""系统监控技能 —— 监控 CPU、内存等系统指标。"""

import psutil
import logging

logger = logging.getLogger(__name__)

SKILL_NAME = "system_monitor"
SKILL_DESCRIPTION = "系统资源监控（CPU、内存使用率）"


class SystemMonitor:
    def __init__(self):
        self._enabled = True

    def enable(self):
        self._enabled = True

    def disable(self):
        self._enabled = False

    def get_cpu_usage(self) -> float:
        return psutil.cpu_percent(interval=0.1)

    def get_memory_usage(self) -> float:
        return psutil.virtual_memory().percent

    def get_stats(self) -> dict:
        return {
            "cpu": self.get_cpu_usage(),
            "memory": self.get_memory_usage(),
        }


# 模块级实例（加载时创建）
_instance = SystemMonitor()


def register(registry):
    """插件接口 — 由 SkillLoader 调用。"""
    registry.register(SKILL_NAME, SKILL_DESCRIPTION)
    registry.add_method(
        SKILL_NAME, "get_stats",
        "获取当前CPU使用率和内存使用率",
        handler=_instance.get_stats,
    )
    registry.add_method(
        SKILL_NAME, "get_cpu_usage",
        "获取当前CPU使用率(%)",
        handler=_instance.get_cpu_usage,
    )
    registry.add_method(
        SKILL_NAME, "get_memory_usage",
        "获取当前内存使用率(%)",
        handler=_instance.get_memory_usage,
    )
