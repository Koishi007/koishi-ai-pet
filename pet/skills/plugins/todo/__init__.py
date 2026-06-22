"""todo 技能 — 极简单代办事项管理。
支持添加/查看/完成/删除任务。
"""

from __future__ import annotations

import logging
import atexit

from pet.skills.plugins.todo.core import TodoListTool
from pet.skills.context import SKILL_CTX

logger = logging.getLogger(__name__)

SKILL_NAME = "todo"
SKILL_DESCRIPTION = "待办事项管理。支持添加、查看、完成、删除任务。"

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
    from pet.skills.plugins.todo.panel import TodoPanel

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
    result = _instance.add(title=title)
    if "error" not in result:
        SKILL_CTX.notify("待办已添加", title.strip())
    return result


def _complete_with_notify(todo_id: int) -> dict:
    """切换完成状态 + Windows 通知。"""
    result = _instance.toggle(todo_id)
    if "error" not in result:
        item = result.get("item", {})
        label = "已完成" if item.get("status") == "done" else "已恢复"
        SKILL_CTX.notify(f"待办{label}", item.get("title", ""))
    return result


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
        handler=_add_with_notify,
        when="用户说「帮我记一下xxx」「添加任务xxx」时",
        args={
            "title": {"type": "str", "required": True, "desc": "任务标题"},
        },
    )

    registry.add_method(
        SKILL_NAME, "list",
        "查询任务列表",
        handler=_instance.list_todos,
        when="用户问「待办有什么」「任务列表」「我还有哪些没做」时",
        args={
            "status": {"type": "str", "required": False, "default": "pending",
                       "desc": "状态: pending/done/all"},
        },
    )

    registry.add_method(
        SKILL_NAME, "toggle",
        "切换任务完成状态（已完成↔恢复待办）",
        handler=_complete_with_notify,
        when="用户说「完成了」「做完了」「恢复这个任务」时",
        args={
            "todo_id": {"type": "int", "required": True, "desc": "任务ID"},
        },
    )

    registry.add_method(
        SKILL_NAME, "delete",
        "删除指定任务",
        handler=_instance.delete,
        when="用户说「删除任务」「取消这个待办」时",
        args={
            "todo_id": {"type": "int", "required": True, "desc": "任务ID"},
        },
    )

    registry.add_method(
        SKILL_NAME, "update",
        "修改已有任务的标题",
        handler=_instance.update,
        when="用户说「修改待办」「把xxx改成」时",
        args={
            "todo_id": {"type": "int", "required": True, "desc": "任务ID"},
            "title": {"type": "str", "required": True, "desc": "新标题"},
        },
    )

    # ── 右键菜单 ──

    registry.add_menu_action(SKILL_NAME, "查看待办", _show_panel)

    # ── 面板注册 ──

    SKILL_CTX.register_panel(SKILL_NAME, _show_panel)

    logger.info("[todo] skill registered")
