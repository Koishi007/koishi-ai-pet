"""定时器持久化 — SQLite 存储，重启后可恢复未完成的定时器。"""

import logging
import sqlite3
import threading
import time
from datetime import datetime

from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)


class TimerStorage:
    """定时器数据的 SQLite 持久化层。"""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or TOOL_CTX.db_path()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_table()

    def _create_table(self):
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS timer_entries (
                    id         TEXT PRIMARY KEY,
                    key        TEXT NOT NULL,
                    label      TEXT NOT NULL,
                    duration_s INTEGER NOT NULL,
                    fire_at    REAL NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS timer_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            self._conn.commit()

    # ── 写操作 ──

    def save(self, timer_id: str, key: str, label: str, duration_s: int, fire_at: float):
        now = datetime.now().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO timer_entries (id, key, label, duration_s, fire_at, created_at) VALUES (?,?,?,?,?,?)",
                (timer_id, key, label, duration_s, fire_at, now),
            )
            self._conn.commit()

    def remove(self, timer_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM timer_entries WHERE id=?", (timer_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def clear_all(self):
        with self._lock:
            self._conn.execute("DELETE FROM timer_entries")
            self._conn.commit()

    def save_shutdown_time(self):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO timer_meta (key, value) VALUES ('shutdown_time', ?)",
                (datetime.now().isoformat(),),
            )
            self._conn.commit()

    # ── 读操作 ──

    def load_all(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM timer_entries ORDER BY fire_at ASC").fetchall()
        return [dict(r) for r in rows]

    def load_shutdown_time(self) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM timer_meta WHERE key='shutdown_time'").fetchone()
        return row["value"] if row else None

    def close(self):
        with self._lock:
            self._conn.close()
