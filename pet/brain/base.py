import json
import logging
import math
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from pet.config import config

logger = logging.getLogger(__name__)


@dataclass
class ContextEntry:
    """一条结构化的上下文记录。"""
    role: str           # "user" | "assistant" | "system"
    content: str
    timestamp: float = field(default_factory=time.time)
    is_summary: bool = False


class BrainMixin:
    """为 Behavior 提供结构化上下文存储与加权检索。"""

    # 以下值从 config 动态读取
    @property
    def _MAX_ENTRIES(self) -> int:
        return config.CONTEXT_MAX_ENTRIES

    @property
    def _MAX_SUMMARIES(self) -> int:
        return config.CONTEXT_MAX_SUMMARIES

    # 角色权重：用户意图 > 系统通知 > 宠物行为
    _ROLE_WEIGHTS = {"user": 3, "system": 2, "assistant": 1}

    def __init__(self, db_path: str | None = None):
        self._context: list[ContextEntry] = []
        self._ctx_lock = threading.Lock()
        self._db_path = db_path
        self._db_conn: sqlite3.Connection | None = None
        self._save_debounce_timer: threading.Timer | None = None

        if db_path and config.CONTEXT_PERSIST_ENABLED:
            self._init_db()
            self._load_context()

    # ── 持久化 ──

    def _init_db(self):
        """初始化上下文持久化表。"""
        try:
            self._db_conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._db_conn.row_factory = sqlite3.Row
            self._db_conn.execute("""
                CREATE TABLE IF NOT EXISTS context_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    is_summary INTEGER DEFAULT 0
                )
            """)
            self._db_conn.execute("""
                CREATE TABLE IF NOT EXISTS context_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            self._db_conn.commit()
        except Exception as e:
            logger.warning(f"[BrainMixin] persistence init failed: {e}")
            self._db_conn = None

    def _load_context(self):
        """启动时从 SQLite 加载上下文 + 计算离开时长。"""
        if not self._db_conn:
            return
        try:
            rows = self._db_conn.execute(
                "SELECT role, content, timestamp, is_summary FROM context_entries ORDER BY timestamp ASC"
            ).fetchall()
            with self._ctx_lock:
                self._context = [
                    ContextEntry(
                        role=r["role"],
                        content=r["content"],
                        timestamp=r["timestamp"],
                        is_summary=bool(r["is_summary"]),
                    )
                    for r in rows
                ]

            # 读取关闭时间，计算离开时长
            meta = self._db_conn.execute(
                "SELECT value FROM context_meta WHERE key='shutdown_time'"
            ).fetchone()
            if meta:
                try:
                    shutdown_time = datetime.fromisoformat(meta["value"])
                    away_seconds = (datetime.now() - shutdown_time).total_seconds()
                    if away_seconds > 60:  # 超过1分钟才提示
                        away_str = self._format_duration(away_seconds)
                        self.add_context(
                            role="system",
                            content=f"用户离开了 {away_str}，刚刚回来",
                        )
                        logger.info(f"[BrainMixin] user was away for {away_str}")
                except Exception:
                    pass

            logger.info(f"[BrainMixin] loaded {len(self._context)} context entries from DB")
        except Exception as e:
            logger.warning(f"[BrainMixin] load context failed: {e}")

    def _save_context(self, record_shutdown: bool = False):
        """保存上下文到 SQLite（debounce 5 秒避免频繁写盘）。"""
        if not self._db_conn:
            return

        if record_shutdown:
            # 立即保存并记录关闭时间
            self._do_save(record_shutdown=True)
            return

        # Debounce：取消之前的定时器，5秒后执行
        if self._save_debounce_timer:
            self._save_debounce_timer.cancel()
        self._save_debounce_timer = threading.Timer(5.0, self._do_save)
        self._save_debounce_timer.daemon = True
        self._save_debounce_timer.start()

    def _do_save(self, record_shutdown: bool = False):
        """实际执行保存。"""
        if not self._db_conn:
            return
        try:
            with self._ctx_lock:
                entries = list(self._context)

            self._db_conn.execute("DELETE FROM context_entries")
            for e in entries:
                self._db_conn.execute(
                    "INSERT INTO context_entries (role, content, timestamp, is_summary) VALUES (?,?,?,?)",
                    (e.role, e.content, e.timestamp, int(e.is_summary)),
                )

            if record_shutdown:
                self._db_conn.execute(
                    "INSERT OR REPLACE INTO context_meta (key, value) VALUES ('shutdown_time', ?)",
                    (datetime.now().isoformat(),),
                )

            self._db_conn.commit()
        except Exception as e:
            logger.warning(f"[BrainMixin] save context failed: {e}")

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """将秒数格式化为人类可读的时长。"""
        if seconds < 3600:
            return f"{int(seconds // 60)} 分钟"
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        if hours < 24:
            return f"{hours} 小时 {minutes} 分钟" if minutes else f"{hours} 小时"
        days = int(hours // 24)
        remaining_hours = hours % 24
        return f"{days} 天 {remaining_hours} 小时" if remaining_hours else f"{days} 天"

    # ── 上下文管理 ──

    def add_context(self, role: str, content: str, is_summary: bool = False):
        with self._ctx_lock:
            self._context.append(ContextEntry(
                role=role, content=content, is_summary=is_summary,
            ))
            self._trim()
        if self._db_conn:
            self._save_context()

    def clear_context(self):
        with self._ctx_lock:
            self._context.clear()
        if self._db_conn:
            self._do_save()  # 立即写入，绕过 debounce

    def context_count(self) -> int:
        with self._ctx_lock:
            return len(self._context)

    def get_multi_turn_messages(self, max_entries: int = 10,
                                skip_last: int = 0,
                                token_budget: int = 0) -> list[dict]:
        """构建多轮消息列表。
        返回 [{"role": "user"/"assistant", "content": "..."}, ...]
        system 角色条目合并到相邻的 user 消息中。"""
        with self._ctx_lock:
            if not self._context:
                return []
            end = -skip_last if skip_last > 0 else len(self._context)
            available = self._context[:end]
            if not available:
                return []

            # 按分排序选取
            scored = sorted(available, key=self._score_entry, reverse=True)
            selected = scored[:max_entries]
            selected.sort(key=lambda e: e.timestamp)  # 按时间排序

            # token 预算截断
            if token_budget > 0:
                total_tokens = 0
                truncated = []
                for e in selected:
                    est = self._estimate_tokens(e.content)
                    if total_tokens + est > token_budget:
                        break
                    total_tokens += est
                    truncated.append(e)
                selected = truncated

            # 映射为多轮消息，合并 system 到相邻 user
            messages = []
            for e in selected:
                if e.is_summary:
                    # 摘要合并到最近的 assistant 消息
                    if messages and messages[-1]["role"] == "assistant":
                        messages[-1]["content"] += f"\n[摘要] {e.content}"
                    else:
                        messages.append({"role": "assistant", "content": f"[摘要] {e.content}"})
                elif e.role == "system":
                    # system 合并到相邻 user
                    if messages and messages[-1]["role"] == "user":
                        messages[-1]["content"] += f"\n[系统] {e.content}"
                    else:
                        messages.append({"role": "user", "content": f"[系统] {e.content}"})
                else:
                    messages.append({"role": e.role, "content": e.content})

            # 合并连续同角色消息
            merged = []
            for msg in messages:
                if merged and merged[-1]["role"] == msg["role"]:
                    merged[-1]["content"] += "\n" + msg["content"]
                else:
                    merged.append(dict(msg))

            return merged

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """粗略估算 token 数（中文约 3 字/token，英文约 4 字符/token）。"""
        return max(1, len(text) // 3)

    def _score_entry(self, entry: ContextEntry) -> float:
        """对单条上下文打分"""
        # 1. 角色权重
        role_score = float(self._ROLE_WEIGHTS.get(entry.role, 1))

        # 2. 时间衰减：半衰期从 config 读取
        age = time.time() - entry.timestamp
        half_life = config.CONTEXT_HALF_LIFE_S
        time_score = 5.0 * (0.5 ** (age / half_life))

        # 3. 内容密度：长消息通常信息量更大
        density_score = 1.0 if len(entry.content) > 30 else 0.0

        return role_score + time_score + density_score


    def _trim(self):
        """超过上限时裁剪：低分条目压缩为摘要后保留，不完全丢弃。"""
        summaries = [e for e in self._context if e.is_summary]
        ordinary = [e for e in self._context if not e.is_summary]

        if len(summaries) > self._MAX_SUMMARIES:
            summaries.sort(key=self._score_entry, reverse=True)
            # 被淘汰的摘要压缩为一条
            evicted = summaries[self._MAX_SUMMARIES:]
            if evicted:
                compressed = " | ".join(e.content[:50] for e in evicted)
                summaries = summaries[:self._MAX_SUMMARIES]
                summaries.append(ContextEntry(
                    role="assistant", content=f"[历史摘要] {compressed}",
                    timestamp=evicted[-1].timestamp, is_summary=True,
                ))
            else:
                summaries = summaries[:self._MAX_SUMMARIES]

        max_ordinary = self._MAX_ENTRIES - len(summaries)
        if len(ordinary) > max_ordinary:
            ordinary.sort(key=self._score_entry, reverse=True)
            # 被淘汰的普通条目压缩为一条摘要
            evicted = ordinary[max_ordinary:]
            if evicted:
                compressed = " | ".join(e.content[:50] for e in evicted)
                ordinary = ordinary[:max_ordinary]
                summaries.append(ContextEntry(
                    role="assistant", content=f"[历史摘要] {compressed}",
                    timestamp=evicted[-1].timestamp, is_summary=True,
                ))

        self._context = sorted(summaries + ordinary, key=lambda e: e.timestamp)
