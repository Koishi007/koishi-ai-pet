"""TodoList 核心处理逻辑 — LLM 可见方法实现。"""

import logging

from pet.tools.todo.storage import TodoStorage

logger = logging.getLogger(__name__)


class TodoListTool:
    def __init__(self):
        self._storage = TodoStorage()

    def add(self, title: str) -> dict:
        if not title or not title.strip():
            return {"error": "标题不能为空"}
        todo = self._storage.add(title=title.strip())
        todo["todo_id"] = todo.pop("id")
        return {
            "headline": f"已添加待办 #{todo['todo_id']}「{todo['title']}」",
            "data": {"id": todo["todo_id"], "title": todo["title"]},
        }

    def list_todos(self, status: str = "pending") -> dict:
        items = self._storage.list(status=None if status == "all" else status)
        if not items:
            return {"headline": "没有待办事项"}
        lines = []
        for t in items:
            t["todo_id"] = t.pop("id")
            mark = "✓" if t["status"] == "done" else "○"
            lines.append(f"#{t['todo_id']} {mark} {t['title']}")
        return {
            "headline": f"共 {len(items)} 条待办",
            "body": "\n".join(lines),
        }

    def toggle(self, todo_id: int) -> dict:
        """切换任务完成状态。「帮我完成/恢复 #3」→ 切换 pending↔done。"""
        result = self._storage.toggle(todo_id)
        if result is None:
            return {"error": f"未找到 id={todo_id} 的任务"}
        label = "已完成" if result["status"] == "done" else "已恢复为待办"
        result["todo_id"] = result.pop("id")
        return {
            "headline": f"{label}：#{todo_id}「{result['title']}」",
            "data": {"id": todo_id, "status": result["status"]},
        }

    def delete(self, todo_id: int) -> dict:
        ok = self._storage.delete(todo_id)
        if not ok:
            return {"error": f"未找到 id={todo_id} 的任务或删除失败"}
        return {"headline": f"已删除任务 #{todo_id}"}

    def update(self, todo_id: int, title: str = "") -> dict:
        if not title or not title.strip():
            return {"error": "标题不能为空"}
        result = self._storage.update(todo_id, title.strip())
        if result is None:
            return {"error": f"未找到 id={todo_id} 的任务"}
        result["todo_id"] = result.pop("id")
        return {
            "headline": f"已更新 #{todo_id}「{result['title']}」",
            "data": {"id": todo_id},
        }

    def close(self):
        self._storage.close()
