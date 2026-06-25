"""todo 工具 — 极简单代办事项管理。
支持添加/查看/完成/删除任务。
"""

from __future__ import annotations

import logging
import atexit

from pet.tools.todo.core import TodoListTool
from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)

TOOL_NAME = "todo"
TOOL_DESCRIPTION = "待办事项管理。支持添加、查看、完成、删除任务。"

try:
    _instance = TodoListTool()
except Exception as e:
    logger.error(f"[todo] Failed to initialize TodoListTool: {e}")
    _instance = None
else:
    atexit.register(_instance.close)

# 持有面板引用防 GC 回收
_panel = None


def _show_panel():
    """右键菜单「查看待办」回调 — 弹出任务管理面板。"""
    global _panel
    from pet.tools.todo.panel import TodoPanel

    if _panel is not None:
        try:
            alive = _panel.isVisible()
        except RuntimeError:
            alive = False
        if not alive:
            _panel.deleteLater()
            _panel = None

    if _panel is None:
        _panel = TodoPanel()

    _panel.show()
    _panel.raise_()


def _add_with_notify(title: str) -> dict:
    """添加待办 + Windows 通知。"""
    TOOL_CTX.speech_random(["记一下…", "写下来…", "别忘了…", "记一下…"])
    result = _instance.add(title=title)
    if "error" not in result:
        TOOL_CTX.notify("待办已添加", title.strip())
    return result


def _list_todos(**kw):
    TOOL_CTX.speech_random(["看看还有什么事…", "翻翻待办…", "什么事没做…", "看看待办…"])
    return _instance.list_todos(**kw)


def _complete_with_notify(todo_id: int) -> dict:
    """切换完成状态 + Windows 通知。"""
    TOOL_CTX.speech_random(["完成…", "搞定…", "好耶…", "做完啦…"])
    result = _instance.toggle(todo_id)
    if "error" not in result:
        item = result.get("item", {})
        label = "已完成" if item.get("status") == "done" else "已恢复"
        TOOL_CTX.notify(f"待办{label}", item.get("title", ""))
    return result


def _delete(**kw):
    TOOL_CTX.speech_random(["删掉…", "划掉…", "不要了…", "去掉…"])
    return _instance.delete(**kw)


def _update(**kw):
    TOOL_CTX.speech_random(["改一下…", "修修看…", "调整一下…", "改改…"])
    return _instance.update(**kw)


def register(registry):
    if _instance is None:
        logger.error("[todo] Skipping registration — TodoListTool init failed")
        return
    tool = registry.register(TOOL_NAME, TOOL_DESCRIPTION)

    # ── LLM 方法 ──

    registry.add_method(
        TOOL_NAME, "add",
        "添加新待办事项",
        handler=_add_with_notify,
        args={
            "title": {"type": "str", "required": True, "desc": "任务标题"},
        },
    )

    registry.add_method(
        TOOL_NAME, "list",
        "查询任务列表",
        handler=_list_todos,
        args={
            "status": {"type": "str", "required": False, "default": "pending",
                       "desc": "状态: pending/done/all",
                       "enum": ["pending", "done", "all"]},
        },
    )

    registry.add_method(
        TOOL_NAME, "toggle",
        "切换任务完成状态（已完成↔恢复待办）",
        handler=_complete_with_notify,
        args={
            "todo_id": {"type": "int", "required": True, "desc": "任务ID"},
        },
    )

    registry.add_method(
        TOOL_NAME, "delete",
        "删除指定任务",
        handler=_delete,
        args={
            "todo_id": {"type": "int", "required": True, "desc": "任务ID"},
        },
    )

    registry.add_method(
        TOOL_NAME, "update",
        "修改已有任务的标题",
        handler=_update,
        args={
            "todo_id": {"type": "int", "required": True, "desc": "任务ID"},
            "title": {"type": "str", "required": True, "desc": "新标题"},
        },
    )

    # ── 右键菜单 ──

    registry.add_menu_action(TOOL_NAME, "查看待办", _show_panel)

    # ── 面板注册 ──

    TOOL_CTX.register_panel(TOOL_NAME, _show_panel)

    logger.info("[todo] tool registered")
