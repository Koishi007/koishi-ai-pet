"""会话事件日志：只追加的事件存储，`Message[]` 由 `projection.project` 从这里投影。"""

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from pet.db import get_conn

logger = logging.getLogger(__name__)

EVENT_TYPES = frozenset({
    "user_msg", "assistant_turn", "assistant_speech", "tool_call",
    "tool_result", "failed_turn", "system_note", "summary", "snapshot",
})

# 结果原文超过该长度才落 artifacts
ARTIFACT_INLINE_LIMIT = 2048
ARTIFACT_MAX_BYTES = 64 * 1024
ARTIFACT_TTL_DAYS = 3
ARTIFACT_TOTAL_BYTES = 100 * 1024 * 1024

_MAX_DIGEST_KEYS = 3
_MAX_DIGEST_CHARS = 40


@dataclass(frozen=True)
class Event:
    """一条不可变事件。"""
    seq: int
    session_id: str
    ts: float
    type: str
    group_id: Optional[str]
    payload: dict
    refs: dict


def new_group_id() -> str:
    """工具轮次组标识。"""
    return uuid.uuid4().hex[:12]


def args_digest(args: dict | None) -> str:
    """参数要点：最多 3 个顶层标量，字符串截 40 字符，嵌套只写 {…}。"""
    if not args:
        return "-"
    parts = []
    for key, value in args.items():
        if len(parts) >= _MAX_DIGEST_KEYS:
            break
        if isinstance(value, dict) or isinstance(value, list):
            parts.append(f"{key}={{…}}")
        elif isinstance(value, str):
            text = value[:_MAX_DIGEST_CHARS]
            parts.append(f"{key}={text}{'…' if len(value) > _MAX_DIGEST_CHARS else ''}")
        elif value is None or isinstance(value, (int, float, bool)):
            parts.append(f"{key}={value}")
    return ", ".join(parts) or "-"


class EventStore:
    """事件表、会话元数据与 artifact 存储；对外只提供追加接口。"""

    def __init__(self, db_path: Optional[str] = None):
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        try:
            self._conn = get_conn(db_path)
            self._conn.row_factory = sqlite3.Row
            self._init_schema()
        except Exception as e:
            logger.warning(f"[EventStore] init failed: {e}")
            self._conn = None

    def _init_schema(self):
        """建表；旧 context_entries / context_meta 保留但不再读写。"""
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS session_events (
                    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    ts         REAL NOT NULL,
                    type       TEXT NOT NULL,
                    group_id   TEXT,
                    payload    TEXT NOT NULL,
                    refs       TEXT NOT NULL DEFAULT '{}'
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_events_session_seq
                ON session_events(session_id, seq)
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_events_group
                ON session_events(session_id, group_id)
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS session_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS session_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    created_at  REAL NOT NULL,
                    mime        TEXT NOT NULL DEFAULT 'text/plain',
                    size        INTEGER NOT NULL DEFAULT 0,
                    body        TEXT NOT NULL
                )
            """)
            self._conn.commit()

    @property
    def available(self) -> bool:
        return self._conn is not None

    def meta_get(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM session_meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def meta_set(self, key: str, value: str):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO session_meta (key, value) VALUES (?, ?)", (key, value))
            self._conn.commit()

    def current_session_id(self) -> str:
        """当前会话 id；没有则新建。重启不换会话。"""
        sid = self.meta_get("current_session")
        return sid or self.new_session()

    def new_session(self) -> str:
        """切换会话：旧事件永久保留，不再投影。"""
        sid = uuid.uuid4().hex
        self.meta_set("current_session", sid)
        return sid

    def append(self, type: str, payload: dict | None = None, *,
               group_id: Optional[str] = None, refs: dict | None = None,
               ts: Optional[float] = None, session_id: Optional[str] = None) -> Optional[Event]:
        """追加一条事件；同一会话内 seq 单调递增。"""
        if self._conn is None:
            return None
        if type not in EVENT_TYPES:
            raise ValueError(f"unknown event type: {type}")
        sid = session_id or self.current_session_id()
        stamp = time.time() if ts is None else ts
        body = payload or {}
        link = refs or {}
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO session_events (session_id, ts, type, group_id, payload, refs) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sid, stamp, type, group_id,
                 json.dumps(body, ensure_ascii=False),
                 json.dumps(link, ensure_ascii=False)),
            )
            self._conn.commit()
            return Event(cur.lastrowid, sid, stamp, type, group_id, body, link)

    def load(self, after_seq: int = 0, limit: int = 0,
             session_id: Optional[str] = None) -> list[Event]:
        """按 seq 升序读取事件。"""
        if self._conn is None:
            return []
        sid = session_id or self.current_session_id()
        sql = ("SELECT seq, session_id, ts, type, group_id, payload, refs "
               "FROM session_events WHERE session_id=? AND seq>? ORDER BY seq ASC")
        params: list = [sid, after_seq]
        if limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def last_seq(self, session_id: Optional[str] = None) -> int:
        if self._conn is None:
            return 0
        sid = session_id or self.current_session_id()
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(seq) AS s FROM session_events WHERE session_id=?", (sid,)).fetchone()
        return int(row["s"] or 0) if row else 0

    def count(self, session_id: Optional[str] = None) -> int:
        if self._conn is None:
            return 0
        sid = session_id or self.current_session_id()
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM session_events WHERE session_id=?", (sid,)).fetchone()
        return int(row["c"] or 0) if row else 0

    def unclosed_groups(self, session_id: Optional[str] = None) -> list[dict]:
        """未闭合的工具组：调用数与结果数不一致。"""
        if self._conn is None:
            return []
        sid = session_id or self.current_session_id()
        with self._lock:
            rows = self._conn.execute(
                "SELECT group_id, "
                "SUM(CASE WHEN type='tool_call' THEN 1 ELSE 0 END) AS calls, "
                "SUM(CASE WHEN type='tool_result' THEN 1 ELSE 0 END) AS results "
                "FROM session_events WHERE session_id=? AND group_id IS NOT NULL "
                "AND type IN ('tool_call','tool_result') GROUP BY group_id",
                (sid,),
            ).fetchall()
        return [{"group_id": r["group_id"], "calls": int(r["calls"]),
                 "results": int(r["results"])}
                for r in rows if int(r["calls"]) != int(r["results"])]

    def put_artifact(self, body: str, mime: str = "text/plain") -> Optional[str]:
        """超过内联阈值才落 artifact，返回 id；内联够用返回 None。"""
        if self._conn is None or len(body) <= ARTIFACT_INLINE_LIMIT:
            return None
        text = body[:ARTIFACT_MAX_BYTES]
        artifact_id = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO session_artifacts (artifact_id, created_at, mime, size, body) "
                "VALUES (?, ?, ?, ?, ?)",
                (artifact_id, time.time(), mime, len(text), text),
            )
            self._conn.commit()
        return artifact_id

    def get_artifact(self, artifact_id: str) -> Optional[str]:
        if self._conn is None or not artifact_id:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT body FROM session_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        return row["body"] if row else None

    def cleanup_artifacts(self, now: Optional[float] = None) -> int:
        """按 TTL 与总体积上限清理 artifact，返回删除条数。"""
        if self._conn is None:
            return 0
        now = time.time() if now is None else now
        cutoff = now - ARTIFACT_TTL_DAYS * 86400
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM session_artifacts WHERE created_at < ?", (cutoff,))
            removed = cur.rowcount or 0
            row = self._conn.execute(
                "SELECT COALESCE(SUM(size), 0) AS total FROM session_artifacts").fetchone()
            total = int(row["total"] or 0) if row else 0
            while total > ARTIFACT_TOTAL_BYTES:
                oldest = self._conn.execute(
                    "SELECT artifact_id, size FROM session_artifacts "
                    "ORDER BY created_at ASC LIMIT 1").fetchone()
                if not oldest:
                    break
                self._conn.execute(
                    "DELETE FROM session_artifacts WHERE artifact_id=?", (oldest["artifact_id"],))
                total -= int(oldest["size"] or 0)
                removed += 1
            self._conn.commit()
        if removed:
            logger.info(f"[EventStore] cleaned {removed} artifact(s)")
        return removed

    def close(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        try:
            payload = json.loads(row["payload"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        try:
            refs = json.loads(row["refs"] or "{}")
        except json.JSONDecodeError:
            refs = {}
        return Event(int(row["seq"]), row["session_id"], float(row["ts"]),
                     row["type"], row["group_id"], payload, refs)
