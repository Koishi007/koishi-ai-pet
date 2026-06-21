"""Todo 持久化存储 — SQLite 数据层。"""

from __future__ import annotations

import sqlite3
import threading
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_DB = str(Path(__file__).resolve().parent.parent.parent.parent.parent / "pet.db")


class TodoStorage:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _DEFAULT_DB
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_table()

    def _create_table(self):
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS todos (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    title       TEXT NOT NULL,
                    status      TEXT NOT NULL DEFAULT 'pending',
                    priority    TEXT NOT NULL DEFAULT 'medium',
                    category    TEXT DEFAULT '',
                    due_date    TEXT,
                    notes       TEXT DEFAULT '',
                    created_at  TEXT NOT NULL,
                    completed_at TEXT
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status)")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_todos_due ON todos(due_date)")
            self._conn.commit()

    def add(self, title: str, priority: str = "medium",
            category: str = "", due_date: str = "",
            notes: str = "") -> dict:
        """添加任务，返回完整行 dict。"""
        now = datetime.now().isoformat()
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO todos (title, priority, category, due_date,
                   notes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (title, priority, category, due_date or None, notes, now))
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM todos WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return dict(row) if row else {}

    def list(self, status: str = "pending", priority: str | None = None,
             category: str | None = None, limit: int = 20) -> list[dict]:
        """查询任务列表。"""
        conditions = ["status = ?"]
        params = [status]
        if priority:
            conditions.append("priority = ?")
            params.append(priority)
        if category:
            conditions.append("category = ?")
            params.append(category)
        where = " AND ".join(conditions)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM todos WHERE {where} "
                f"ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
                f"due_date ASC, created_at DESC LIMIT ?",
                params + [limit]
            ).fetchall()
        return [dict(r) for r in rows]

    def complete(self, todo_id: int) -> dict | None:
        """标记完成为 done。"""
        now = datetime.now().isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE todos SET status='done', completed_at=? WHERE id=?",
                (now, todo_id))
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM todos WHERE id=?", (todo_id,)
            ).fetchone()
        return dict(row) if row else None

    def delete(self, todo_id: int) -> bool:
        """删除任务。返回是否成功。"""
        with self._lock:
            cur = self._conn.execute("DELETE FROM todos WHERE id=?", (todo_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def update(self, todo_id: int, **fields) -> dict | None:
        """修改任务。fields 可含 title/priority/category/due_date/notes。"""
        allowed = {"title", "priority", "category", "due_date", "notes"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            row = self._conn.execute(
                "SELECT * FROM todos WHERE id=?", (todo_id,)).fetchone()
            return dict(row) if row else None
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [todo_id]
        with self._lock:
            self._conn.execute(
                f"UPDATE todos SET {set_clause} WHERE id=?", values)
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM todos WHERE id=?", (todo_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_due(self, now_iso: str, precision_minutes: int = 5) -> list[dict]:
        """查询已到期或将在 precision_minutes 内到期的未完成任务。"""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM todos
                   WHERE status='pending' AND due_date IS NOT NULL
                   AND due_date <= ?
                   ORDER BY due_date ASC""",
                (now_iso,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_pending_alarms(self) -> list[dict]:
        """启动时加载：所有未完成且带 due_date 的任务。"""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM todos
                   WHERE status='pending' AND due_date IS NOT NULL
                   AND due_date > datetime('now')
                   ORDER BY due_date ASC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        with self._lock:
            self._conn.close()
