"""B 站工具的持久化层：判重表、每日轮次、写操作台账。

写操作台账的唯一索引保证同一账号、同一目标、同一操作只有一条有效记录，
超时后重复调用不会产生第二条评论或第二次投币。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime

from pet.db import get_conn
from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)

# 有效状态：占住唯一索引，阻止重复写入。unknown 表示请求可能已达服务端，
# 在复核或人工收敛前不重试。
_LIVE_STATUS = ("pending", "done", "visible", "hidden", "unknown")
_RETENTION_DAYS = 90


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class BiliStorage:
    """四张表的读写，连接与锁随实例持有。"""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or TOOL_CTX.db_path()
        self._conn = get_conn(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._create_tables()

    def _create_tables(self):
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS bili_seen (
                    bvid    TEXT PRIMARY KEY,
                    title   TEXT NOT NULL DEFAULT '',
                    up      TEXT NOT NULL DEFAULT '',
                    pubdate INTEGER NOT NULL DEFAULT 0,
                    verdict TEXT NOT NULL DEFAULT 'skipped',
                    note    TEXT NOT NULL DEFAULT '',
                    seen_at TEXT NOT NULL
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS bili_rounds (
                    day      TEXT NOT NULL,
                    uid      INTEGER NOT NULL DEFAULT 0,
                    picked   INTEGER NOT NULL DEFAULT 0,
                    requests INTEGER NOT NULL DEFAULT 0,
                    last_run TEXT NOT NULL,
                    PRIMARY KEY (day, uid)
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS bili_actions (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid        INTEGER NOT NULL,
                    kind       TEXT NOT NULL,
                    target     TEXT NOT NULL,
                    content    TEXT NOT NULL DEFAULT '',
                    status     TEXT NOT NULL,
                    remote_id  TEXT NOT NULL DEFAULT '',
                    reason     TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            self._conn.execute("DROP INDEX IF EXISTS idx_bili_actions_live")
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_bili_actions_target
                    ON bili_actions(uid, kind, target)
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS bili_mentions (
                    msg_id   INTEGER PRIMARY KEY,
                    uid      INTEGER NOT NULL DEFAULT 0,
                    bvid     TEXT NOT NULL DEFAULT '',
                    rpid     INTEGER NOT NULL DEFAULT 0,
                    name     TEXT NOT NULL DEFAULT '',
                    content  TEXT NOT NULL DEFAULT '',
                    handled  INTEGER NOT NULL DEFAULT 0,
                    seen_at  TEXT NOT NULL
                )
            """)
            self._conn.commit()

    def is_seen(self, bvid: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM bili_seen WHERE bvid = ?", (bvid,)).fetchone()
        return row is not None

    def unseen(self, bvids: list[str]) -> list[str]:
        if not bvids:
            return []
        marks = ",".join(["?"] * len(bvids))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT bvid FROM bili_seen WHERE bvid IN ({marks})", tuple(bvids)
            ).fetchall()
        seen = {r["bvid"] for r in rows}
        return [b for b in bvids if b not in seen]

    def mark_seen(self, items: list[dict]):
        if not items:
            return
        now = _now()
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO bili_seen (bvid, title, up, pubdate, verdict, note, seen_at)"
                " VALUES (?,?,?,?,?,?,?)",
                [
                    (it.get("bvid", ""), it.get("title", ""), it.get("up", ""),
                     int(it.get("pubdate") or 0), it.get("verdict", "skipped"),
                     it.get("note", ""), now)
                    for it in items if it.get("bvid")
                ],
            )
            self._conn.commit()

    def save_round(self, uid: int, day: str, picked: int, requests: int):
        with self._lock:
            self._conn.execute(
                "INSERT INTO bili_rounds (day, uid, picked, requests, last_run) VALUES (?,?,?,?,?)"
                " ON CONFLICT(day, uid) DO UPDATE SET"
                " picked = picked + excluded.picked,"
                " requests = requests + excluded.requests,"
                " last_run = excluded.last_run",
                (day, int(uid), int(picked), int(requests), _now()),
            )
            self._conn.commit()

    def get_round(self, uid: int, day: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM bili_rounds WHERE day = ? AND uid = ?", (day, int(uid))
            ).fetchone()
        return dict(row) if row else {"day": day, "uid": int(uid), "picked": 0,
                                      "requests": 0, "last_run": ""}

    def claim_mention(self, msg_id: int, uid: int, bvid: str, rpid: int,
                      name: str, content: str) -> bool:
        """记录一条 @，已存在返回 False。"""
        with self._lock:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO bili_mentions"
                "(msg_id, uid, bvid, rpid, name, content, seen_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(msg_id), int(uid), bvid, int(rpid), name[:40],
                 content[:120], _now()),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def mention_for_bvid(self, bvid: str) -> dict | None:
        """取该视频下尚未回复的 @。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT msg_id, rpid, name FROM bili_mentions"
                " WHERE bvid = ? AND handled = 0 ORDER BY seen_at DESC LIMIT 1",
                (bvid,),
            ).fetchone()
        return dict(row) if row else None

    def mark_mention_replied(self, msg_id: int):
        with self._lock:
            self._conn.execute("UPDATE bili_mentions SET handled = 1 WHERE msg_id = ?",
                               (int(msg_id),))
            self._conn.commit()

    def count_mentions_today(self, day: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM bili_mentions WHERE seen_at LIKE ?",
                (f"{day}%",),
            ).fetchone()
        return int(row["n"] or 0)

    def _live_count(self, uid: int, kind: str, target: str) -> int:
        marks = ",".join(["?"] * len(_LIVE_STATUS))
        row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM bili_actions"
            f" WHERE uid = ? AND kind = ? AND target = ? AND status IN ({marks})",
            (int(uid), kind, target, *_LIVE_STATUS),
        ).fetchone()
        return int(row["n"] or 0)

    def count_live(self, uid: int, kind: str, target: str) -> int:
        """该目标上仍然有效的记录数。"""
        with self._lock:
            return self._live_count(uid, kind, target)

    def claim_action(self, uid: int, kind: str, target: str, content: str = "",
                     limit: int = 1) -> int | None:
        """占位成功返回台账 id；有效记录达到 limit 时返回 None。"""
        now = _now()
        with self._lock:
            if self._live_count(uid, kind, target) >= max(1, int(limit)):
                return None
            cur = self._conn.execute(
                "INSERT INTO bili_actions"
                " (uid, kind, target, content, status, created_at, updated_at)"
                " VALUES (?,?,?,?,'pending',?,?)",
                (int(uid), kind, target, content, now, now),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def finish_action(self, action_id: int, status: str, *, remote_id: str = "",
                      reason: str = ""):
        with self._lock:
            self._conn.execute(
                "UPDATE bili_actions SET status = ?, remote_id = ?, reason = ?, updated_at = ?"
                " WHERE id = ?",
                (status, remote_id, reason, _now(), int(action_id)),
            )
            self._conn.commit()

    def find_live_action(self, uid: int, kind: str, target: str) -> dict | None:
        marks = ",".join(["?"] * len(_LIVE_STATUS))
        with self._lock:
            row = self._conn.execute(
                f"SELECT * FROM bili_actions WHERE uid = ? AND kind = ? AND target = ?"
                f" AND status IN ({marks}) ORDER BY id DESC LIMIT 1",
                (int(uid), kind, target, *_LIVE_STATUS),
            ).fetchone()
        return dict(row) if row else None

    def count_today(self, uid: int, kind: str, day: str) -> int:
        """当日该操作的有效条数，含进行中的。"""
        marks = ",".join(["?"] * len(_LIVE_STATUS))
        with self._lock:
            row = self._conn.execute(
                f"SELECT COUNT(*) AS n FROM bili_actions"
                f" WHERE uid = ? AND kind = ? AND status IN ({marks})"
                f" AND substr(created_at, 1, 10) = ?",
                (int(uid), kind, *_LIVE_STATUS, day),
            ).fetchone()
        return int(row["n"] if row else 0)

    def pending_actions(self, uid: int, kind: str, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM bili_actions WHERE uid = ? AND kind = ? AND status = 'pending'"
                " ORDER BY id DESC LIMIT ?",
                (int(uid), kind, int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def reviewable_actions(self, uid: int, kind: str, limit: int = 10) -> list[dict]:
        """已发出且有远端 id、尚未确认可见的记录。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM bili_actions WHERE uid = ? AND kind = ?"
                " AND status = 'done' AND remote_id != ''"
                " ORDER BY id DESC LIMIT ?",
                (int(uid), kind, int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def mention_root(self, bvid: str) -> int:
        """该视频下 @ 我们的那条评论 id，用作楼中楼复核的 root。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT rpid FROM bili_mentions WHERE bvid = ? AND rpid > 0"
                " ORDER BY seen_at DESC LIMIT 1",
                (bvid,),
            ).fetchone()
        return int(row["rpid"]) if row else 0

    def recent_contents(self, uid: int, kind: str, limit: int = 20) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT content FROM bili_actions WHERE uid = ? AND kind = ?"
                " AND content != '' ORDER BY id DESC LIMIT ?",
                (int(uid), kind, int(limit)),
            ).fetchall()
        return [r["content"] for r in rows]

    def prune(self, days: int = _RETENTION_DAYS):
        """删掉过期的判重与已结案的台账；pending 留给复核处理。"""
        with self._lock:
            self._conn.execute(
                "DELETE FROM bili_seen WHERE seen_at < datetime('now', ?)",
                (f"-{int(days)} days",),
            )
            self._conn.execute(
                "DELETE FROM bili_actions WHERE created_at < datetime('now', ?)"
                " AND status NOT IN ('pending')",
                (f"-{int(days)} days",),
            )
            self._conn.commit()
