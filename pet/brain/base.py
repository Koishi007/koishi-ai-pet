import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

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

    @property
    def _MAX_ENTRIES(self) -> int:
        return config.CONTEXT_MAX_ENTRIES

    @property
    def _MAX_SUMMARIES(self) -> int:
        return config.CONTEXT_MAX_SUMMARIES

    @property
    def _MAX_HISTORY_SUMMARIES(self) -> int:
        return max(2, int(config.CONTEXT_HISTORY_ENTRIES * 0.2))
    _DEDUP_NGRAM_SIZE = 3
    _DEDUP_THRESHOLD = 0.35
    _MAX_PENDING_QUEUE = 50  # 防止摘要队列无限增长
    _MAX_DEDUP_COMPARE = 3   # 摘要去重时比较最近的条目数

    def __init__(self, db_path: Optional[str] = None):
        self._context: List[ContextEntry] = []
        self._ctx_lock = threading.RLock()  # 使用可重入锁
        self._db_path = db_path
        self._db_conn: Optional[sqlite3.Connection] = None
        self._save_debounce_timer: Optional[threading.Timer] = None
        self._pending_summary_queue: List[str] = []

        if db_path and config.CONTEXT_PERSIST_ENABLED:
            self._init_db()
            self._load_context()

    # ── 持久化 ──

    def _init_db(self):
        """初始化上下文持久化表。"""
        try:
            self._db_conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._db_conn.row_factory = sqlite3.Row
            # 启用 WAL 模式提升并发性能
            self._db_conn.execute("PRAGMA journal_mode=WAL;")
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
                        role=r["role"], content=r["content"],
                        timestamp=r["timestamp"], is_summary=bool(r["is_summary"])
                    ) for r in rows
                ]

            meta = self._db_conn.execute(
                "SELECT value FROM context_meta WHERE key='shutdown_time'"
            ).fetchone()
            
            if meta:
                try:
                    shutdown_time = datetime.fromisoformat(meta["value"])
                    away_seconds = (datetime.now() - shutdown_time).total_seconds()
                    if away_seconds > 60:
                        away_str = self._format_duration(away_seconds)
                        self.add_context(role="system", content=f"用户离开了 {away_str}，刚刚回来")
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
            self._do_save(record_shutdown=True)
            return

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
                # 快照数据，减少锁占用时间
                entries = [(e.role, e.content, e.timestamp, int(e.is_summary)) for e in self._context]

            # 使用事务和 executemany 提升写入性能
            with self._db_conn:  # 自动提交或回滚
                self._db_conn.execute("DELETE FROM context_entries")
                self._db_conn.executemany(
                    "INSERT INTO context_entries (role, content, timestamp, is_summary) VALUES (?,?,?,?)",
                    entries
                )
                if record_shutdown:
                    self._db_conn.execute(
                        "INSERT OR REPLACE INTO context_meta (key, value) VALUES ('shutdown_time', ?)",
                        (datetime.now().isoformat(),)
                    )
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
            if is_summary:
                # 与最近 _MAX_DEDUP_COMPARE 条 summary 去重
                compared = 0
                replaced = False
                for i in range(len(self._context) - 1, -1, -1):
                    e = self._context[i]
                    if e.is_summary:
                        compared += 1
                        if self._text_similarity(content, e.content) >= self._DEDUP_THRESHOLD:
                            self._context[i] = ContextEntry(role=role, content=content, is_summary=True)
                            replaced = True
                            logger.debug(f"[BrainMixin] summary replaced: {content[:30]}...")
                            break
                        if compared >= self._MAX_DEDUP_COMPARE:
                            break
                
                if replaced:
                    if self._db_conn:
                        self._save_context()
                    return

            self._context.append(ContextEntry(role=role, content=content, is_summary=is_summary))
            self._evict_context()
            
        if self._db_conn:
            self._save_context()

    def clear_context(self):
        with self._ctx_lock:
            self._context.clear()
            self._pending_summary_queue.clear()
        
        if self._save_debounce_timer:
            self._save_debounce_timer.cancel()
            self._save_debounce_timer = None

        if self._db_conn:
            self._do_save()

    def context_count(self) -> int:
        with self._ctx_lock:
            return len(self._context)

    def get_multi_turn_messages(self, max_entries: int = 10, skip_last: int = 0, token_budget: int = 0) -> List[dict]:
        """构建多轮消息列表。时间顺序优先，打分仅用于淘汰决策。"""
        with self._ctx_lock:
            if not self._context:
                return []

            end = -skip_last if skip_last > 0 else len(self._context)
            available = self._context[:end]
            if not available:
                return []

            # 1. 保持时间正序
            selected = sorted(available, key=lambda e: e.timestamp)

            # 2. 条数淘汰：优先淘汰最旧的普通对话
            if len(selected) > max_entries:
                need_drop = len(selected) - max_entries
                dropped = set()

                # 优先按时间从旧到新淘汰普通对话
                normal = [(i, e) for i, e in enumerate(selected) if not e.is_summary and e.role != "system"]
                normal.sort(key=lambda x: x[1].timestamp)
                for i, _ in normal[:need_drop]:
                    dropped.add(i)
                    need_drop -= 1

                # 如果普通对话都淘汰完了还是超限，再按分数淘汰摘要和系统消息（低分优先）
                if need_drop > 0:
                    score_candidates = [(i, e) for i, e in enumerate(selected) if i not in dropped and (e.is_summary or e.role == "system")]
                    score_candidates.sort(key=lambda x: self._score_entry(x[1]))
                    for i, _ in score_candidates[:need_drop]:
                        dropped.add(i)

                selected = [e for i, e in enumerate(selected) if i not in dropped]
                logger.debug(f"[BrainMixin] count eviction: dropped {len(available) - len(selected)}, kept {len(selected)}")

            # 3. Token 预算淘汰：优先丢弃最旧的普通对话，不够再丢摘要/system
            before_count = len(selected)
            if token_budget > 0 and selected:
                total_tokens = sum(self._estimate_tokens(e.content) for e in selected)
                
                # 先遍历弹出最旧的普通对话
                idx = 0
                while total_tokens > token_budget and len(selected) > 1 and idx < len(selected):
                    e = selected[idx]
                    if not e.is_summary and e.role != "system":
                        total_tokens -= self._estimate_tokens(e.content)
                        selected.pop(idx)
                    else:
                        idx += 1
                
                # 如果还是超，再按时间从旧到新强制弹出（包含摘要和 system）
                while total_tokens > token_budget and len(selected) > 1:
                    removed = selected.pop(0)
                    total_tokens -= self._estimate_tokens(removed.content)

                if len(selected) < before_count:
                    logger.debug(f"[BrainMixin] token budget ({token_budget}) truncated {before_count}→{len(selected)} entries")

            # 4. 构建消息：保持时间顺序，system/摘要/对话各归其位
            messages = []
            for e in selected:
                time_str = self._format_context_time(e.timestamp)
                prefix = f"[{time_str}] " if time_str else ""
                content_with_time = prefix + e.content

                if e.is_summary:
                    messages.append({"role": "system", "content": content_with_time})
                elif e.role == "system":
                    messages.append({"role": "system", "content": content_with_time})
                else:
                    messages.append({"role": e.role, "content": content_with_time})

            return messages



    @staticmethod
    def _format_context_time(timestamp: float) -> str:
        """将 float 时间戳转为绝对时间：同天→HH:MM，昨天→昨天 HH:MM，前天→前天 HH:MM，更早→MM-DD HH:MM"""
        ref = datetime.fromtimestamp(timestamp)
        now = datetime.now()
        day_diff = (now.date() - ref.date()).days
        if day_diff == 0:
            return ref.strftime("%H:%M")
        elif day_diff == 1:
            return ref.strftime("昨天 %H:%M")
        elif day_diff == 2:
            return ref.strftime("前天 %H:%M")
        else:
            return ref.strftime("%m-%d %H:%M")

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """token 估算"""
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        other_chars = len(text) - chinese_chars
        # 中文约1.5 token/字，英文约0.25 token/字符
        return max(1, int(chinese_chars * 1.5 + other_chars * 0.25))

    def _score_entry(self, entry: ContextEntry) -> float:
        """对单条上下文打分"""
        role_score = float(self._ROLE_WEIGHTS.get(entry.role, 1))
        # 工具调用时效性短，大幅降权，避免挤占 token 预算
        if entry.content.startswith("[工具调用]"):
            role_score *= 0.1
        age = time.time() - entry.timestamp
        half_life = config.CONTEXT_HALF_LIFE_S
        time_score = 2.0 * (0.5 ** (age / half_life))
        density_score = 1.0 if len(entry.content) > 30 else 0.0
        return role_score + time_score + density_score

    @classmethod
    def _char_ngrams(cls, text: str) -> set:
        text = re.sub(r'[^\w]', '', text.lower())
        n = cls._DEDUP_NGRAM_SIZE
        if len(text) < n:
            return {text}
        return {text[i:i + n] for i in range(len(text) - n + 1)}

    @classmethod
    def _text_similarity(cls, a: str, b: str) -> float:
        sa, sb = cls._char_ngrams(a), cls._char_ngrams(b)
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / len(sa | sb)

    def _evict_context(self):
        """超过上限时淘汰。工具调用独立配额，避免挤占正常对话空间。"""
        summaries = [e for e in self._context if e.is_summary]
        ordinary = [e for e in self._context if not e.is_summary]

        decision_summaries = [e for e in summaries if not e.content.startswith("[历史摘要]")]
        history_summaries = [e for e in summaries if e.content.startswith("[历史摘要]")]

        if len(decision_summaries) > self._MAX_SUMMARIES:
            decision_summaries.sort(key=self._score_entry, reverse=True)
            decision_summaries = decision_summaries[:self._MAX_SUMMARIES]

        history_summaries.sort(key=lambda e: e.timestamp, reverse=True)
        history_summaries = history_summaries[:self._MAX_HISTORY_SUMMARIES]

        summaries = decision_summaries + history_summaries

        # 工具调用单独管理：只保留最近 3 条，老的直接丢弃
        tool_calls = [e for e in ordinary if e.content.startswith("[工具调用]")]
        normal_chats = [e for e in ordinary if not e.content.startswith("[工具调用]")]
        tool_calls.sort(key=lambda e: e.timestamp, reverse=True)
        tool_calls = tool_calls[:3]

        # 正常聊天的空间 = 总空间 - 摘要空间 - 工具调用空间
        base_limit = self._MAX_ENTRIES - len(summaries) - len(tool_calls)
        soft_limit = base_limit + 3
        if len(normal_chats) > soft_limit:
            normal_chats.sort(key=self._score_entry, reverse=True)
            evicted = normal_chats[base_limit:]
            if evicted:
                candidates = [e for e in evicted]
                candidates.sort(key=self._score_entry, reverse=True)

                queue_size = min(10, max(3, len(evicted) // 2))
                queued = candidates[:queue_size]

                if queued:
                    self._pending_summary_queue.extend(f"[{e.role}] {e.content}" for e in queued)
                    if len(self._pending_summary_queue) > self._MAX_PENDING_QUEUE:
                        self._pending_summary_queue = self._pending_summary_queue[-self._MAX_PENDING_QUEUE:]
                    logger.info(f"[BrainMixin] evicted {len(evicted)}, queued {len(queued)} for summarization")

                normal_chats = normal_chats[:base_limit]

        self._context = sorted(summaries + tool_calls + normal_chats, key=lambda e: e.timestamp)

    def drain_pending_summaries(self) -> List[str]:
        """取出并清空待摘要队列。"""
        with self._ctx_lock:
            items = self._pending_summary_queue[:]
            self._pending_summary_queue.clear()
            return items

    @staticmethod
    def _build_fallback_summary(items: List[str]) -> str:
        """LLM 不可用时的兜底摘要。"""
        return " | ".join(item[:50] for item in items[:5])
