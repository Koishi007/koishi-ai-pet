"""LLM 调用计数器 —— SQLite 持久化。"""

import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class LlmStats:
    """记录 LLM 累计调用次数，写入 pet.db。"""

    _TABLE_SQL = "CREATE TABLE IF NOT EXISTS llm_stats (key TEXT PRIMARY KEY, value INTEGER)"

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = str(Path(__file__).resolve().parent.parent.parent / "pet.db")
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(self._TABLE_SQL)
        self._conn.commit()
        self._total = self._load("total_calls")
        logger.info(f"[LlmStats] loaded: total_calls={self._total}")

    # ── 读写 ──

    def increment(self):
        self._total += 1

    @property
    def total(self) -> int:
        return self._total

    # ── 持久化 ──

    def save(self):
        self._conn.execute(
            "INSERT OR REPLACE INTO llm_stats (key, value) VALUES ('total_calls', ?)",
            (self._total,),
        )
        self._conn.commit()

    def close(self):
        self.save()
        self._conn.close()
        logger.info(f"[LlmStats] saved: total_calls={self._total}")

    # ── 内部 ──

    def _load(self, key: str) -> int:
        row = self._conn.execute(
            "SELECT value FROM llm_stats WHERE key=?", (key,)
        ).fetchone()
        return row[0] if row else 0
