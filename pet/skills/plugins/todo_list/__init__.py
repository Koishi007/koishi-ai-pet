"""todo_list 技能 — 待办事项管理。
支持添加/查看/完成/删除任务，分类、优先级、截止日期，到点通过 LLM 提醒。
"""

from __future__ import annotations

import logging

from pet.skills.plugins.todo_list.core import TodoListTool
from pet.skills.plugins.todo_list.reminder import ReminderManager
from pet.skills.context import SKILL_CTX

logger = logging.getLogger(__name__)

SKILL_NAME = "todo"
SKILL_DESCRIPTION = (
    "待办事项管理。支持添加、查看、修改、完成、删除任务，"
    "可按状态/优先级/分类筛选，支持设置截止日期（到点提醒）。"
)

try:
    _instance = TodoListTool()
except Exception as e:
    logger.error(f"[todo] Failed to initialize TodoListTool: {e}")
    _instance = None
_reminder: ReminderManager | None = None


def _show_panel():
    """右键菜单「查看待办」回调 — 弹出任务管理面板。"""
    from pet.skills.plugins.todo_list.panel import TodoPanel
    panel = TodoPanel()
    panel.show()


def _add_with_reminder(title: str, priority: str = "medium",
                        category: str = "", due_date: str = "",
                        notes: str = "") -> dict:
    """添加待办并注册提醒。"""
    result = _instance.add(title=title, priority=priority,
                           category=category, due_date=due_date, notes=notes)
    if result and not result.get("error") and result.get("due_date") and _reminder:
        _reminder.on_task_added(result)
    return result


def _update_with_reset(todo_id: int, title: str = "", priority: str = "",
                        category: str = "", due_date: str = "",
                        notes: str = "") -> dict:
    """Update a todo and reset/reschedule reminders if due_date changed."""
    result = _instance.update(todo_id=todo_id, title=title, priority=priority,
                              category=category, due_date=due_date, notes=notes)
    if result and not result.get("error") and due_date and _reminder:
        _reminder.reset_fired(todo_id)
        _reminder.on_task_added(result.get("item", result))
    return result


def _quick_add():
    """右键菜单「添加待办」回调 — 弹出快速添加输入框。"""
    from PySide6.QtWidgets import QInputDialog
    # 获取活跃窗口作为 parent（可能为 None，仍可工作）
    title, ok = QInputDialog.getText(
        None, "添加待办", "任务标题:")
    if ok and title.strip():
        result = _instance.add(title=title.strip())
        if "summary" in result:
            logger.info(f"[todo] quick add: {result['summary']}")
        # 触发提醒注册（add 返回的 dict 即 todo 行数据，直接含 due_date 等字段）
        if result and result.get("due_date") and _reminder:
            _reminder.on_task_added(result)


def register(registry):
    if _instance is None:
        logger.error("[todo] Skipping registration — TodoListTool init failed")
        return
    skill = registry.register(SKILL_NAME, SKILL_DESCRIPTION)
    skill.when = "用户需要管理待办事项、记录任务、或查看任务列表时"

    # ── LLM 方法 ──

    registry.add_method(
        SKILL_NAME, "add",
        "添加新待办事项",
        handler=_add_with_reminder,
        when="用户说「帮我记一个待办」「添加任务」时",
        args={
            "title": {"type": "str", "required": True, "desc": "任务标题"},
            "priority": {"type": "str", "required": False, "default": "medium",
                         "desc": "优先级: high/medium/low"},
            "category": {"type": "str", "required": False, "default": "",
                         "desc": "分类标签，如: 工作/个人/学习"},
            "due_date": {"type": "str", "required": False, "default": "",
                         "desc": "截止日期(ISO格式)，日期级如2026-06-25，"
                                 "时间级如2026-06-25T15:00，精确级如2026-06-25T14:00:00"},
            "notes": {"type": "str", "required": False, "default": "",
                      "desc": "备注详情"},
        },
    )

    registry.add_method(
        SKILL_NAME, "list",
        "查询任务列表，可按状态/优先级/分类筛选",
        handler=_instance.list_todos,
        when="用户问「待办有什么」「任务列表」「我还有哪些没做」时",
        args={
            "status": {"type": "str", "required": False, "default": "pending",
                       "desc": "状态: pending/done"},
            "priority": {"type": "str", "required": False, "default": "",
                         "desc": "优先级筛选: high/medium/low，留空=全部"},
            "category": {"type": "str", "required": False, "default": "",
                         "desc": "分类标签筛选，留空=全部"},
            "limit": {"type": "int", "required": False, "default": 20,
                      "desc": "最大返回条数"},
        },
    )

    registry.add_method(
        SKILL_NAME, "complete",
        "将指定任务标记为已完成",
        handler=_instance.complete,
        when="用户说「完成了」「做完了」「done」时",
        args={
            "id": {"type": "int", "required": True, "desc": "任务ID"},
        },
    )

    registry.add_method(
        SKILL_NAME, "delete",
        "删除指定任务",
        handler=_instance.delete,
        when="用户说「删除任务」「取消这个待办」时",
        args={
            "id": {"type": "int", "required": True, "desc": "任务ID"},
        },
    )

    registry.add_method(
        SKILL_NAME, "update",
        "修改已有任务的内容、优先级、截止日期等",
        handler=_update_with_reset,
        when="用户说「修改待办」「把xxx改成」时",
        args={
            "id": {"type": "int", "required": True, "desc": "任务ID"},
            "title": {"type": "str", "required": False, "default": "", "desc": "新标题"},
            "priority": {"type": "str", "required": False, "default": "", "desc": "新优先级"},
            "category": {"type": "str", "required": False, "default": "", "desc": "新分类"},
            "due_date": {"type": "str", "required": False, "default": "", "desc": "新截止日期"},
            "notes": {"type": "str", "required": False, "default": "", "desc": "新备注"},
        },
    )

    registry.add_method(
        SKILL_NAME, "check_due",
        "查询已到期/即将到期的任务，用于判断是否需要提醒用户",
        handler=_instance.check_due,
        when="LLM 想要检查是否有到期任务需要提醒用户时",
        args={},
    )

    # ── 右键菜单 ──

    registry.add_menu_action(SKILL_NAME, "查看待办", _show_panel)
    registry.add_menu_action(SKILL_NAME, "添加待办", _quick_add)

    # ── 面板注册 ──

    SKILL_CTX.register_panel(SKILL_NAME, _show_panel)

    # ── 提醒初始化 ──

    global _reminder
    _reminder = ReminderManager(_instance._storage)
    SKILL_CTX.on_bind(lambda: _reminder.start())
    logger.info("[todo] skill registered (reminders deferred to post-bind)")
