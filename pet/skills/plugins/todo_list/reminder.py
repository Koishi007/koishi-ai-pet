"""Todo 提醒 — tick 回调 + 精确 ALARM 管理。"""

import logging
import time
from datetime import datetime, timedelta

from pet.skills.plugins.todo_list.storage import TodoStorage
from pet.skills.context import SKILL_CTX

logger = logging.getLogger(__name__)

# due_date 格式 → 提醒级别映射
# 含 "T" + 秒部分 → 精确时刻
# 含 "T"（无秒） → 时间级
# 仅日期 → 日期级


def _classify(due_date: str) -> str:
    """解析 due_date 格式，返回 'exact' / 'time' / 'date'。"""
    if not due_date:
        return "none"
    if "T" in due_date:
        # 按冒号数量判断精度
        time_part = due_date.split("T")[1]
        if time_part.count(":") >= 2:
            return "exact"
        return "time"
    return "date"


def _to_timestamp(due_date: str) -> int:
    """将 ISO datetime 转为毫秒时间戳。"""
    try:
        dt = datetime.fromisoformat(due_date)
        if dt.tzinfo is not None:
            dt = dt.astimezone(None).replace(tzinfo=None)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return 0


class ReminderManager:
    def __init__(self, storage: TodoStorage):
        self._storage = storage
        self._fired_tasks: set[int] = set()  # 本轮已提醒过的任务 ID

    def reset_fired(self, todo_id: int):
        """Allow a task to fire reminders again after due_date change."""
        self._fired_tasks.discard(todo_id)

    def _prune_fired(self):
        """Periodically remove stale IDs from _fired_tasks."""
        if len(self._fired_tasks) < 100:
            return
        existing = {t["id"] for t in self._storage.list(status="pending")}
        self._fired_tasks &= existing

    def stop(self):
        """Unregister tick callbacks from scheduler."""
        try:
            if SKILL_CTX._agent:
                SKILL_CTX._agent.scheduler.unregister("slow", self._check_date_tasks)
                SKILL_CTX._agent.scheduler.unregister("fast", self._check_time_tasks)
        except Exception:
            pass
        logger.info("[Reminder] stopped")

    def start(self):
        """启动时：注册 exact alarm + tick 轮询（tick 回调实时查 DB，覆盖动态新增任务）。"""
        tasks = self._storage.get_pending_alarms()
        for t in tasks:
            level = _classify(t.get("due_date", ""))
            if level == "exact":
                self._register_exact(t)

        # Catch-up: fire reminders for already-past date/time tasks (capped)
        now = datetime.now().isoformat()
        catch_up = []
        for t in tasks:
            level = _classify(t.get("due_date", ""))
            if level in ("date", "time") and t.get("due_date", "") and t["due_date"] <= now:
                catch_up.append(t)
        for t in catch_up[:5]:
            self._fire_reminder(t)

        # tick 回调每次都实时查 DB，因此动态新增的 date/time 级任务也会被覆盖
        SKILL_CTX.register_tick("slow", self._check_date_tasks)
        SKILL_CTX.register_tick("fast", self._check_time_tasks)
        logger.info("[Reminder] tick callbacks registered")

    def on_task_added(self, todo: dict):
        """运行时新增任务时调用，仅为 exact 级注册 alarm。"""
        level = _classify(todo.get("due_date", ""))
        if level == "exact":
            self._register_exact(todo)
        # date/time 级由 tick 回调定期查 DB，无需单独注册

    def _register_exact(self, todo: dict):
        """注册精准时刻 alarm。"""
        ts = _to_timestamp(todo["due_date"])
        if ts <= 0:
            return
        now_ms = int(time.time() * 1000)
        if ts <= now_ms:
            logger.info(f"[Reminder] exact task #{todo['id']} already past due, firing now")
            self._fire_reminder(todo)
            return

        def _alarm():
            # 触发前确认任务仍然 pending
            items = self._storage.list(status="pending")
            ids = {t["id"] for t in items}
            if todo["id"] not in ids:
                return
            self._fire_reminder(todo)

        alarm_key = f"todo_{todo['id']}"
        SKILL_CTX.register_alarm(ts, _alarm, key=alarm_key)
        logger.info(f"[Reminder] exact alarm set for #{todo['id']} at {todo['due_date']}")

    def _check_date_tasks(self):
        """slow_tick 回调：实时查 DB，检查日期级到期任务。"""
        self._prune_fired()
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        items = self._storage.get_due(today, precision_minutes=0)
        for t in items:
            level = _classify(t.get("due_date", ""))
            if level == "date" and t["id"] not in self._fired_tasks:
                self._fire_reminder(t)

    def _check_time_tasks(self):
        """fast_tick 回调：实时查 DB，检查时间级任务（±5分钟窗口）。"""
        self._prune_fired()
        now = datetime.now()
        window = (now + timedelta(minutes=5)).isoformat()
        items = self._storage.get_due(window, precision_minutes=5)
        for t in items:
            level = _classify(t.get("due_date", ""))
            if level == "time" and t["id"] not in self._fired_tasks:
                # 再次确认在 ±5 分钟窗口内
                try:
                    due_dt = datetime.fromisoformat(t["due_date"])
                    if due_dt.tzinfo is not None:
                        due_dt = due_dt.replace(tzinfo=None)
                    if abs((due_dt - now).total_seconds()) <= 300:
                        self._fire_reminder(t)
                except ValueError:
                    continue

    def _fire_reminder(self, todo: dict):
        self._fired_tasks.add(todo["id"])
        due = todo.get("due_date", "")
        if not due:
            return
        try:
            due_dt = datetime.fromisoformat(due)
            if due_dt.tzinfo is not None:
                due_dt = due_dt.replace(tzinfo=None)
            now = datetime.now()
            # For date-only strings, deadline is end of day
            if "T" not in due:
                due_dt = due_dt.replace(hour=23, minute=59, second=59)
            overdue = due_dt < now
        except ValueError:
            overdue = due < datetime.now().isoformat()

        label = "已过期" if overdue else "即将到期"
        hint = (
            f"提醒用户：任务「{todo['title']}」{label}，"
            f"截止时间 {due}，优先级 {todo['priority']}"
        )
        logger.info(f"[Reminder] firing: {hint}")
        SKILL_CTX.request_interact(hint)
