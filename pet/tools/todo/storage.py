"""Todo 持久化存储 — SQLite 数据层。"""

from __future__ import annotations

import sqlite3
import threading
import logging
from datetime import datetime

from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)


class TodoStorage:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or TOOL_CTX.db_path()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_table()

    def _create_table(self):
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS todos (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    title      TEXT NOT NULL,
                    status     TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL
                )
            """)
            self._conn.commit()

    def add(self, title: str) -> dict:
        """添加任务，返回完整行 dict。"""
        now = datetime.now().isoformat()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO todos (title, created_at) VALUES (?, ?)",
                (title.strip(), now))
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM todos WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return dict(row) if row else {}

    def list(self, status: str | None = None) -> list[dict]:
        """查询任务列表。status=None 时返回全部。"""
        with self._lock:
            if status is not None:
                rows = self._conn.execute(
                    "SELECT * FROM todos WHERE status=? ORDER BY created_at DESC",
                    (status,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM todos ORDER BY created_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def toggle(self, todo_id: int) -> dict | None:
        """切换任务完成状态 pending ↔ done。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM todos WHERE id=?", (todo_id,)
            ).fetchone()
            if not row:
                return None
            new_status = "done" if row["status"] == "pending" else "pending"
            self._conn.execute(
                "UPDATE todos SET status=? WHERE id=?", (new_status, todo_id))
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM todos WHERE id=?", (todo_id,)
            ).fetchone()
        return dict(row) if row else None

    def update(self, todo_id: int, title: str) -> dict | None:
        """修改任务标题。"""
        if not title.strip():
            return None
        with self._lock:
            self._conn.execute(
                "UPDATE todos SET title=? WHERE id=?", (title.strip(), todo_id))
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

    def close(self):
        with self._lock:
            self._conn.close()
