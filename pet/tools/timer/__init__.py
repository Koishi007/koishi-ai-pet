"""timer 工具 — 倒计时定时器，到时间宠物主动提醒。支持持久化重启后恢复。"""

import logging
import re
import atexit

from pet.tools.timer.core import TimerTool
from pet.tools.timer.storage import TimerStorage
from pet.tools.context import TOOL_CTX
from pet.tools.output import ToolOutputDefinition

logger = logging.getLogger(__name__)

TOOL_NAME = "timer"
TOOL_DESCRIPTION = "倒计时定时器，设定后宠物会在指定时间后主动提醒。"
TOOL_GROUP = "productivity"

_DURATION_RE = re.compile(r"(\d+)([smh]?)")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}

SET_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "x-role": "headline",
                     "description": "确认设定的一句话"},
        "data": {"type": "object", "x-role": "data", "description": "定时器 id 与秒数"},
    },
}

LIST_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "x-role": "headline",
                     "description": "活跃定时器数量"},
        "body": {"type": "string", "x-role": "body", "description": "每个定时器一行"},
    },
}

CANCEL_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "x-role": "headline",
                     "description": "取消结果"},
        "data": {"type": "object", "x-role": "data", "description": "定时器 id"},
    },
}

CANCEL_ALL_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "x-role": "headline",
                     "description": "取消数量"},
    },
}

_instance: TimerTool | None = None


def parse_duration(text: str) -> int | None:
    """把 90 / 90s / 10m / 1h30m 解析成秒；无法解析或为 0 返回 None。"""
    value = str(text or "").strip().lower()
    if not value:
        return None
    total = 0
    pos = 0
    for match in _DURATION_RE.finditer(value):
        if match.start() != pos:
            return None
        unit = (match.group(2) or "s").lower()
        total += int(match.group(1)) * _UNIT_SECONDS[unit]
        pos = match.end()
    if pos != len(value) or total <= 0:
        return None
    return total


def _resolve_command(arg: str) -> tuple[str, dict]:
    """把 `:timer` 的参数翻成方法名与参数。"""
    parts = str(arg or "").split(None, 1)
    if not parts:
        raise ValueError("缺少参数：时长或子命令")
    head = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if head == "list":
        return "list", {}
    if head == "cancel":
        if not rest:
            raise ValueError("缺少参数：定时器 id")
        return "cancel", {"timer_id": rest}
    seconds = parse_duration(head)
    if seconds is None:
        raise ValueError(f"时长格式无效：「{head}」，支持 90、10m、1h30m")
    return "set", {"duration": seconds, "label": rest or "时间到"}


def _ensure() -> TimerTool | None:
    global _instance
    if _instance is None:
        try:
            storage = TimerStorage()
            _instance = TimerTool(storage=storage)
            _instance.restore_from_storage()
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


def _cancel_all() -> dict:
    """取消所有定时器。"""
    if not (t := _ensure()):
        return {"error": "定时器未初始化"}
    TOOL_CTX.speech_random(["全部取消…", "清空啦…", "都忘掉…"])
    return t.cancel_all()


def register_commands(registry):
    """注册终端的 `:timer` 命令：只翻译参数，实际调用走通用执行器。"""
    registry.add_command(
        TOOL_NAME,
        name="timer",
        group="计时与待办",
        rows=((":timer <duration> [message]", "设定倒计时，duration 支持 90、10m、1h30m"),
              (":timer list", "列出未到期的倒计时"),
              (":timer cancel <id>", "取消倒计时，id 见 :timer list")),
        resolve=_resolve_command,
        words=("list", "cancel"),
        examples=(":timer 10m 喝水",),
    )


def register(registry):
    tool = registry.register(TOOL_NAME, TOOL_DESCRIPTION, side_effect=True)

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
        output=ToolOutputDefinition(schema=SET_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "list",
        "查看当前所有活跃的定时器。",
        handler=_list,
        output=ToolOutputDefinition(schema=LIST_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "cancel",
        "取消指定定时器。",
        handler=_cancel,
        args={
            "timer_id": {"type": "str", "required": True, "desc": "定时器ID"},
        },
        output=ToolOutputDefinition(schema=CANCEL_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "cancel_all",
        "取消所有活跃的定时器。",
        handler=_cancel_all,
        output=ToolOutputDefinition(schema=CANCEL_ALL_OUTPUT),
    )

    register_commands(registry)

    logger.info("[timer] tool registered")
