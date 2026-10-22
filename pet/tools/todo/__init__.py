"""todo 工具 — 极简单代办事项管理。
支持添加/查看/完成/删除任务。
"""

from __future__ import annotations

import logging

from pet.tools.context import TOOL_CTX
from pet.tools.output import ToolOutputDefinition
from pet.tools.todo import store

logger = logging.getLogger(__name__)

TOOL_NAME = "todo"
TOOL_DESCRIPTION = "待办事项管理。支持添加、查看、完成、删除任务。"
TOOL_GROUP = "productivity"

ADD_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "maxLength": 120, "x-role": "headline",
                     "description": "确认添加的一句话"},
        "data": {"type": "object", "x-role": "data", "description": "任务 id 与标题"},
    },
}

LIST_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "maxLength": 120, "x-role": "headline",
                     "description": "待办条数"},
        "body": {"type": "string", "x-role": "body", "description": "每条一行，含状态标记"},
    },
}

TOGGLE_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "maxLength": 120, "x-role": "headline",
                     "description": "状态变化与任务名"},
        "data": {"type": "object", "x-role": "data", "description": "任务 id 与新状态"},
    },
}

DELETE_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "maxLength": 120, "x-role": "headline",
                     "description": "删除结果"},
    },
}

UPDATE_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "maxLength": 120, "x-role": "headline",
                     "description": "更新结果"},
        "data": {"type": "object", "x-role": "data", "description": "任务 id"},
    },
}


def _show_panel():
    """右键菜单「查看待办」回调 — 弹出任务管理面板。"""
    # 面板依赖 Qt，延迟到实际弹出时导入
    from pet.tools.todo.panel import show_panel

    show_panel(store.get_instance())


def _add_with_notify(title: str) -> dict:
    """添加待办 + Windows 通知。"""
    TOOL_CTX.speech_random(["记一下…", "写下来…", "别忘了…", "记一下…"])
    result = store.get_instance().add(title=title)
    if "error" not in result:
        TOOL_CTX.notify("待办已添加", title.strip())
    return result


def _list_todos(**kw):
    TOOL_CTX.speech_random(["看看还有什么事…", "翻翻待办…", "什么事没做…", "看看待办…"])
    return store.get_instance().list_todos(**kw)


def _complete_with_notify(todo_id: int) -> dict:
    """切换完成状态 + Windows 通知。"""
    TOOL_CTX.speech_random(["完成…", "搞定…", "好耶…", "做完啦…"])
    result = store.get_instance().toggle(todo_id)
    if "error" not in result:
        item = result.get("item", {})
        label = "已完成" if item.get("status") == "done" else "已恢复"
        TOOL_CTX.notify(f"待办{label}", item.get("title", ""))
        TOOL_CTX.note_event("todo", f"待办「{item.get('title', '')}」{label}")
    return result


def _delete(**kw):
    TOOL_CTX.speech_random(["删掉…", "划掉…", "不要了…", "去掉…"])
    return store.get_instance().delete(**kw)


def _update(**kw):
    TOOL_CTX.speech_random(["改一下…", "修修看…", "调整一下…", "改改…"])
    return store.get_instance().update(**kw)


def register(registry):
    try:
        store.init_instance()
    except Exception as e:
        logger.error(f"[todo] Failed to initialize TodoListTool: {e}")
        return
    tool = registry.register(TOOL_NAME, TOOL_DESCRIPTION, side_effect=True)


    registry.add_method(
        TOOL_NAME, "add",
        "添加新待办事项",
        handler=_add_with_notify,
        args={
            "title": {"type": "str", "required": True, "desc": "任务标题"},
        },
        output=ToolOutputDefinition(schema=ADD_OUTPUT),
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
        output=ToolOutputDefinition(schema=LIST_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "toggle",
        "切换任务完成状态（已完成↔恢复待办）",
        handler=_complete_with_notify,
        args={
            "todo_id": {"type": "int", "required": True, "desc": "任务ID"},
        },
        output=ToolOutputDefinition(schema=TOGGLE_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "delete",
        "删除指定任务",
        handler=_delete,
        args={
            "todo_id": {"type": "int", "required": True, "desc": "任务ID"},
        },
        output=ToolOutputDefinition(schema=DELETE_OUTPUT),
    )

    registry.add_method(
        TOOL_NAME, "update",
        "修改已有任务的标题",
        handler=_update,
        args={
            "todo_id": {"type": "int", "required": True, "desc": "任务ID"},
            "title": {"type": "str", "required": True, "desc": "新标题"},
        },
        output=ToolOutputDefinition(schema=UPDATE_OUTPUT),
    )


    registry.add_menu_action(TOOL_NAME, "查看待办", _show_panel)


    logger.info("[todo] tool registered")
