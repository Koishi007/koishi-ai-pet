"""对话历史持久化存储 — 记录所有 speech 输出和用户 chat 输入，按天切分，至多保存 7 天。"""

import logging
import sqlite3
import threading
from datetime import datetime, timedelta

from pet.db import get_db_path

logger = logging.getLogger(__name__)


class ConversationStore:
    """对话历史存储层"""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or get_db_path()
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        try:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=5.0)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._create_table()
            self._cleanup_old(7)
        except Exception as e:
            logger.warning(f"[ConversationStore] init failed: {e}")
            self._conn = None

    def _create_table(self):
        """创建 chat_history 表和索引。"""
        if not self._conn:
            return
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    role       TEXT NOT NULL,
                    content    TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_history_date
                ON chat_history(date(created_at))
            """)
            self._conn.commit()

    def _cleanup_old(self, days: int = 7):
        """清理超过指定天数的过期记录"""
        if not self._conn:
            return
        try:
            cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            with self._lock:
                self._conn.execute(
                    "DELETE FROM chat_history WHERE date(created_at) < ?",
                    (cutoff,),
                )
                self._conn.commit()
        except Exception as e:
            logger.warning(f"[ConversationStore] cleanup failed: {e}")

    def add(self, role: str, content: str):
        """写入一条对话记录。"""
        if not self._conn or not content:
            return
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO chat_history (role, content, created_at) VALUES (?, ?, ?)",
                    (role, content, datetime.now().isoformat()),
                )
                self._conn.commit()
        except Exception as e:
            logger.warning(f"[ConversationStore] add failed: {e}")

    def query_by_date(self, date_str: str) -> list[dict]:
        """按日期（YYYY-MM-DD）查询对话记录，按创建时间升序。"""
        if not self._conn:
            return []
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT role, content, created_at FROM chat_history "
                    "WHERE date(created_at) = ? ORDER BY created_at ASC",
                    (date_str,),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"[ConversationStore] query_by_date failed: {e}")
            return []

    def get_available_dates(self) -> list[str]:
        """返回有记录的日期列表（降序），格式 YYYY-MM-DD。"""
        if not self._conn:
            return []
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT DISTINCT date(created_at) AS d FROM chat_history ORDER BY d DESC"
                ).fetchall()
            return [r["d"] for r in rows]
        except Exception as e:
            logger.warning(f"[ConversationStore] get_available_dates failed: {e}")
            return []

    def close(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
