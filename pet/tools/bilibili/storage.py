"""B 站工具的持久化层：判重表、每日轮次、写操作台账。

写操作台账按 (uid, kind, target) 统计有效记录数，与调用方给的 limit 比较后占位；
单实例内由实例锁串行，跨进程不保证。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta

from pet.db import get_conn
from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)

# 有效状态：计入目标去重与每日额度。unknown 表示已发出但没拿到回执，不再重试
_LIVE_STATUS = ("pending", "done", "visible", "hidden", "unverified", "unknown")
_RETENTION_DAYS = 90
# 占位后超过此时长仍未结案，视为调用被中断
_PENDING_TIMEOUT_MIN = 10


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ago(minutes: int) -> str:
    """与 `_now()` 同格式的过去时刻。"""
    return (datetime.now() - timedelta(minutes=int(minutes))).isoformat(timespec="seconds")


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
                    root       INTEGER NOT NULL DEFAULT 0,
                    reason     TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_bili_actions_target
                    ON bili_actions(uid, kind, target)
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS bili_mentions (
                    msg_id   INTEGER PRIMARY KEY,
                    uid      INTEGER NOT NULL DEFAULT 0,
                    bvid     TEXT NOT NULL DEFAULT '',
                    title    TEXT NOT NULL DEFAULT '',
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

    def has_mention(self, msg_id: int) -> bool:
        """该条 @ 是否已入库。"""
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM bili_mentions WHERE msg_id = ?",
                                     (int(msg_id),)).fetchone()
        return row is not None

    def add_mention(self, msg_id: int, uid: int, bvid: str, title: str, rpid: int,
                    name: str, content: str):
        """记录一条 @；已入库时只补标题与 BV 号。"""
        with self._lock:
            self._conn.execute(
                "INSERT INTO bili_mentions"
                " (msg_id, uid, bvid, title, rpid, name, content, seen_at)"
                " VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(msg_id) DO UPDATE SET"
                " bvid = excluded.bvid, title = excluded.title",
                (int(msg_id), int(uid), bvid, title[:80], int(rpid), name[:40],
                 content[:120], _now()),
            )
            self._conn.commit()

    def unreplied_mentions(self, uid: int, limit: int = 3) -> list[dict]:
        """未回复的 @，最近的在前。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT msg_id, bvid, title, rpid, name, content, seen_at"
                " FROM bili_mentions WHERE handled = 0 AND bvid != '' AND uid = ?"
                " ORDER BY seen_at DESC, msg_id DESC LIMIT ?",
                (int(uid), int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

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
        """占位成功返回台账 id；有效记录达到 limit 时返回 None，limit 为 0 时不按目标去重。"""
        now = _now()
        with self._lock:
            self._reclaim_pending(now)
            if limit > 0 and self._live_count(uid, kind, target) >= int(limit):
                return None
            cur = self._conn.execute(
                "INSERT INTO bili_actions"
                " (uid, kind, target, content, status, created_at, updated_at)"
                " VALUES (?,?,?,?,'pending',?,?)",
                (int(uid), kind, target, content, now, now),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def _reclaim_pending(self, now: str):
        """超时未结案的占位记为 failed：调用被中断时目标不会永久占额。"""
        self._conn.execute(
            "UPDATE bili_actions SET status = 'failed', reason = 'pending_timeout',"
            " updated_at = ? WHERE status = 'pending' AND created_at < ?",
            (now, _ago(_PENDING_TIMEOUT_MIN)),
        )
        self._conn.commit()

    def finish_action(self, action_id: int, status: str, *, remote_id: str = "",
                      reason: str = "", root: int = 0):
        with self._lock:
            self._conn.execute(
                "UPDATE bili_actions SET status = ?, remote_id = ?, root = ?, reason = ?,"
                " updated_at = ? WHERE id = ?",
                (status, remote_id, int(root), reason, _now(), int(action_id)),
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
        """已发出且有远端 id 的待复核记录：未复核的 done 优先，其次 unverified，最后 hidden。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM bili_actions WHERE uid = ? AND kind = ?"
                " AND status IN ('done', 'unverified', 'hidden') AND remote_id != ''"
                " ORDER BY CASE status WHEN 'done' THEN 0 WHEN 'unverified' THEN 1 ELSE 2 END,"
                " id DESC LIMIT ?",
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

    def last_comment_at(self, uid: int) -> str:
        """该账号最近一次评论的开始时间；没评论过返回空串。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(created_at) AS t FROM bili_actions"
                " WHERE uid = ? AND kind = 'comment'",
                (int(uid),),
            ).fetchone()
        return str(row["t"] or "") if row else ""

    def prune(self, days: int = _RETENTION_DAYS):
        """删掉过期的判重与已结案的台账；pending 留给复核处理。"""
        threshold = _ago(int(days) * 24 * 60)
        with self._lock:
            self._conn.execute("DELETE FROM bili_seen WHERE seen_at < ?", (threshold,))
            self._conn.execute(
                "DELETE FROM bili_actions WHERE created_at < ? AND status != 'pending'",
                (threshold,),
            )
            self._conn.commit()
