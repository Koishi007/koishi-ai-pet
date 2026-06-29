"""timer 工具 — 倒计时定时器，到时间宠物主动提醒。"""

import logging
import atexit

from pet.tools.timer.core import TimerTool
from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)

TOOL_NAME = "timer"
TOOL_DESCRIPTION = "倒计时定时器，设定后宠物会在指定时间后主动提醒。"

_instance: TimerTool | None = None


def _ensure() -> TimerTool | None:
    global _instance
    if _instance is None:
        try:
            _instance = TimerTool()
            atexit.register(_instance.close)
        except Exception as e:
            logger.error(f"[timer] Failed to initialize: {e}")
    return _instance


def _set(duration: int, label: str = "时间到") -> dict:
    """设置定时器。"""
    if not (t := _ensure()):
        return {"error": "定时器未初始化"}
    if not label.strip():
        label = "时间到"
    TOOL_CTX.speech_random(["设好了…", "到时间喊你…", "记下啦…"])
    return t.set_timer(duration=duration, label=label.strip())


def _list() -> dict:
    """查看活跃定时器。"""
    if not (t := _ensure()):
        return {"error": "定时器未初始化"}
    return t.list_timers()


def _cancel(timer_id: str) -> dict:
    """取消定时器。"""
    if not (t := _ensure()):
        return {"error": "定时器未初始化"}
    TOOL_CTX.speech_random(["取消啦…", "不提醒了…", "忘掉它…"])
    return t.cancel_timer(timer_id)


def register(registry):
    tool = registry.register(TOOL_NAME, TOOL_DESCRIPTION)

    registry.add_method(
        TOOL_NAME, "set",
        "设置一个倒计时定时器。当用户说「提醒我X分钟后Y」「X分钟后喊我」时调用。",
        handler=_set,
        args={
            "duration": {"type": "int", "required": True,
                         "desc": "倒计时秒数(1~86400)"},
            "label": {"type": "str", "required": False, "default": "时间到",
                      "desc": "提醒内容标题"},
        },
        timeout=5.0,
    )

    registry.add_method(
        TOOL_NAME, "list",
        "查看当前所有活跃的定时器。",
        handler=_list,
    )

    registry.add_method(
        TOOL_NAME, "cancel",
        "取消指定定时器。",
        handler=_cancel,
        args={
            "timer_id": {"type": "str", "required": True, "desc": "定时器ID"},
        },
    )

    logger.info("[timer] tool registered")
