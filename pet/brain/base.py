import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from pet.config import config
from pet.db import get_conn
from pet.brain.events import EventStore
from pet.brain.projection import Budget, project
from pet.brain.tool_channel import current_profile

logger = logging.getLogger(__name__)


@dataclass
class ContextEntry:
    """一条上下文记录；`seq` 指向事件日志，仅调试快照使用。"""
    role: str           # "user" | "assistant" | "system"
    content: str
    timestamp: float = field(default_factory=time.time)
    is_summary: bool = False
    seq: int = 0


class BrainMixin:
    """为 Behavior 提供结构化上下文存储与加权检索。"""

    @property
    def _MAX_ENTRIES(self) -> int:
        # 候选池容量与每轮注入上限是同一个值：池内条目一定有机会入选本轮，
        # 被淘汰的条目一定进摘要队列，不存在"留在池里却永远选不进"的死区。
        return config.CONTEXT_HISTORY_ENTRIES

    @property
    def _MAX_POOL_ENTRIES(self) -> int:
        """注入时该取多少条：池子在常态下的上界，即 `_evict_context` 的批量淘汰软上限。

        池子在 `_MAX_ENTRIES` 与 `_MAX_ENTRIES + _EVICT_BATCH_SIZE` 之间震荡，注入上限取
        池子的常态上界，池内条目才不会被一个更小的数字每轮排除。

        注意这不是池子的硬上界：工具调用没有条数配额，最近 `CONTEXT_HALF_LIFE_S` 内的工具调用
        超过 `_MAX_ENTRIES` 减去摘要数时池子会突破它，此时 `get_multi_turn_messages` 的条数裁剪
        仍会兜底（丢掉最旧的若干条）。
        """
        return self._MAX_ENTRIES + self._EVICT_BATCH_SIZE

    @property
    def _MAX_HISTORY_SUMMARIES(self) -> int:
        return max(2, int(config.CONTEXT_HISTORY_ENTRIES * 0.2))

    _ROLE_WEIGHTS = {"user": 3, "system": 2, "assistant": 1}
    _DEDUP_NGRAM_SIZE = 3
    _DEDUP_THRESHOLD = 0.35
    _MAX_PENDING_QUEUE = 50  # 防止摘要队列无限增长
    _MAX_DEDUP_COMPARE = 3   # 摘要去重时比较最近的条目数
    _EVICT_BATCH_SIZE = 6    # 每次淘汰时触发摘要的软上限

    def __init__(self, db_path: Optional[str] = None):
        self._context: List[ContextEntry] = []
        self._ctx_lock = threading.RLock()  # 使用可重入锁
        self._db_path = db_path
        self._events: Optional[EventStore] = None
        self._save_debounce_timer: Optional[threading.Timer] = None
        self._pending_summary_queue: List[tuple] = []  # (start_seq, end_seq, 文本)
        self._drained_covers: List[tuple] = []
        self._reported_groups: set = set()

        if db_path and config.CONTEXT_PERSIST_ENABLED:
            self._init_db()
            self._load_context()


    def _init_db(self):
        """初始化事件日志；旧 context_entries 表保留但不再读写。"""
        try:
            self._events = EventStore(self._db_path)
        except Exception as e:
            logger.warning(f"[BrainMixin] persistence init failed: {e}")
            self._events = None

    def _load_context(self):
        """启动时重建调试快照、处理离开时长与未闭合工具组。"""
        if self._events is None or not self._events.available:
            return
        self._rebuild_context_snapshot()
        self._scan_unclosed_groups()
        meta = self._events.meta_get("shutdown_time")
        if not meta:
            return
        try:
            shutdown_time = datetime.fromisoformat(meta)
            away_seconds = (datetime.now() - shutdown_time).total_seconds()
            if away_seconds > 60:
                away_str = self.format_duration(away_seconds)
                self.add_context(role="system", content=f"用户离开了 {away_str}，刚刚回来")
                logger.info(f"[BrainMixin] user was away for {away_str}")
        except Exception:
            pass

    def _rebuild_context_snapshot(self):
        """从当前会话事件重建调试面板用的内存快照，不触发淘汰与摘要。"""
        try:
            events = self._events.load()
        except Exception as e:
            logger.warning(f"[BrainMixin] rebuild context snapshot failed: {e}")
            return
        entries: List[ContextEntry] = []
        for event in events:
            text = event.payload.get("text") or ""
            if not text:
                continue
            if event.type == "user_msg":
                entries.append(ContextEntry("user", text, event.ts, False, event.seq))
            elif event.type == "assistant_turn":
                entries.append(ContextEntry("assistant", text, event.ts, False, event.seq))
            elif event.type == "system_note":
                entries.append(ContextEntry("system", text, event.ts, False, event.seq))
            elif event.type == "summary":
                entries.append(ContextEntry("system", text, event.ts, True, event.seq))
        # 只取池子上限内的条目，避免重启后第一条消息就触发淘汰与摘要
        with self._ctx_lock:
            self._context = entries[-self._MAX_ENTRIES:]

    def _scan_unclosed_groups(self):
        """崩溃窗口：未闭合组只记录，不重放、不重试。"""
        try:
            groups = self._events.unclosed_groups()
        except Exception as e:
            logger.warning(f"[BrainMixin] scan unclosed groups failed: {e}")
            return
        for group in groups:
            if group["group_id"] in self._reported_groups:
                continue
            self._reported_groups.add(group["group_id"])
            logger.warning(f"[BrainMixin] 未闭合工具组 {group['group_id']}: "
                           f"calls={group['calls']} results={group['results']}，不重放")

    def close_events(self):
        """关闭事件日志连接。"""
        if self._events is not None:
            self._events.close()

    def maintenance(self):
        """slow_tick：清理过期 artifact、回收被覆盖事件并复查未闭合组。"""
        if self._events is None or not self._events.available:
            return
        try:
            self._events.cleanup_artifacts()
        except Exception as e:
            logger.warning(f"[BrainMixin] artifact cleanup failed: {e}")
        try:
            self._events.purge_covered()
        except Exception as e:
            logger.warning(f"[BrainMixin] covered event purge failed: {e}")
        self._scan_unclosed_groups()

    def dump_events(self, limit: int = 50) -> List[dict]:
        """调试入口：按时间列出最近事件。"""
        if self._events is None or not self._events.available:
            return []
        rows = self._events.load()
        return [{"seq": e.seq, "ts": e.ts, "type": e.type, "group_id": e.group_id,
                 "payload": e.payload, "refs": e.refs} for e in rows[-limit:]]

    def dump_projection(self) -> List[dict]:
        """调试入口：按当前 Profile 与配置预算投影一次。"""
        return self.get_multi_turn_messages(
            max_entries=self._MAX_POOL_ENTRIES,
            token_budget=config.CONTEXT_TOKEN_BUDGET,
        )

    def _save_context(self, record_shutdown: bool = False):
        """记录关机时刻，供下次启动计算离开时长。"""
        if self._events is None or not self._events.available or not record_shutdown:
            return
        try:
            self._events.meta_set("shutdown_time", datetime.now().isoformat())
        except Exception as e:
            logger.warning(f"[BrainMixin] save context failed: {e}")

    def _do_save(self, record_shutdown: bool = False):
        """兼容旧调用名。"""
        self._save_context(record_shutdown=record_shutdown)

    @staticmethod
    def format_duration(seconds: float) -> str:
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


    def add_context(self, role: str, content: str, is_summary: bool = False,
                    kind: str = "", turn_id: str = ""):
        """写一条事件，并维护供调试面板查看的内存快照。

        `kind` 标记 assistant 内容的用途：speech（台词）/ summary（本轮摘要），
        供投影决定渲染成哪一行；`turn_id` 把同一回合的两次写入合并成一条历史消息。
        """
        with self._ctx_lock:
            if is_summary and self._replace_similar_summary(role, content):
                return
            event = self._write_event(role, content, is_summary, kind, turn_id)
            self._context.append(ContextEntry(
                role=role, content=content, is_summary=is_summary,
                seq=event.seq if event else 0))
            self._evict_context()

    def _replace_similar_summary(self, role: str, content: str) -> bool:
        """与最近 _MAX_DEDUP_COMPARE 条摘要去重；命中则只替换快照。"""
        compared = 0
        for i in range(len(self._context) - 1, -1, -1):
            entry = self._context[i]
            if not entry.is_summary:
                continue
            compared += 1
            if self._text_similarity(content, entry.content) >= self._DEDUP_THRESHOLD:
                self._context[i] = ContextEntry(role=role, content=content,
                                                is_summary=True, seq=entry.seq)
                logger.debug(f"[BrainMixin] summary replaced: {content[:30]}...")
                return True
            if compared >= self._MAX_DEDUP_COMPARE:
                break
        return False

    def _write_event(self, role: str, content: str, is_summary: bool, kind: str = "",
                     turn_id: str = ""):
        """把一次上下文写入映射为事件；失败不影响主流程。"""
        if self._events is None or not self._events.available:
            return None
        try:
            if is_summary:
                covers = self._consume_covers()
                refs = {"covers": list(covers)} if covers else None
                return self._events.append("summary", {"text": content}, refs=refs)
            payload = {"text": content}
            if kind:
                payload["kind"] = kind
            if turn_id:
                payload["turn_id"] = turn_id
            if role == "user":
                return self._events.append("user_msg", payload)
            if role == "assistant":
                return self._events.append("assistant_turn", payload)
            return self._events.append("system_note", payload)
        except Exception as e:
            logger.warning(f"[BrainMixin] append event failed: {e}")
            return None

    def _consume_covers(self) -> Optional[tuple]:
        """摘要覆盖区间：取本轮淘汰事件的最小/最大 seq。"""
        with self._ctx_lock:
            ranges = list(self._drained_covers)
            self._drained_covers.clear()
        starts = [a for a, _ in ranges if a]
        ends = [b for _, b in ranges if b]
        if not starts or not ends:
            return None
        return (min(starts), max(ends))

    def record_failed_turn(self, text: str, finish_reason: str = "", tag: str = ""):
        """解析失败的响应只留档，不进入任何视图。"""
        if self._events is None or not self._events.available or not text:
            return
        try:
            self._events.append("failed_turn", {
                "text": text[:1000], "finish_reason": finish_reason, "tag": tag})
        except Exception as e:
            logger.warning(f"[BrainMixin] append failed_turn failed: {e}")

    def record_tool_events(self, group_id: str, calls: list, results: list,
                           asides: Optional[list] = None):
        """把一轮工具调用写成同组事件，供投影聚合成原子组。"""
        if self._events is None or not self._events.available:
            return
        for call_id, text in (asides or []):
            try:
                self._events.append("assistant_speech", {"text": text},
                                    group_id=group_id,
                                    refs={"tool_call_id": call_id})
            except Exception as e:
                logger.warning(f"[BrainMixin] append assistant_speech failed: {e}")
        for call in calls:
            call_id = call.get("id", "")
            try:
                self._events.append(
                    "tool_call",
                    {"name": call.get("name", ""),
                     "args_digest": call.get("args_digest", "-"),
                     "side_effect": bool(call.get("side_effect", False))},
                    group_id=group_id, refs={"tool_call_id": call_id})
            except Exception as e:
                logger.warning(f"[BrainMixin] append tool_call failed: {e}")
        for result in results:
            call_id = result.get("tool_call_id", "")
            artifact_id = None
            raw = result.get("raw") or ""
            if raw:
                artifact_id = self._store_artifact(raw)
            try:
                self._events.append(
                    "tool_result",
                    {"ok": bool(result.get("ok", False)),
                     "brief": result.get("brief", ""),
                     "artifact_id": artifact_id},
                    group_id=group_id, refs={"tool_call_id": call_id})
            except Exception as e:
                logger.warning(f"[BrainMixin] append tool_result failed: {e}")

    def _store_artifact(self, raw: str) -> Optional[str]:
        """原文超过内联阈值时落 artifacts，返回 id；否则不建。"""
        try:
            return self._events.put_artifact(raw)
        except Exception as e:
            logger.warning(f"[BrainMixin] put artifact failed: {e}")
            return None

    def clear_context(self):
        """清空上下文：结束当前会话，并删除历史会话的事件与 artifact。"""
        with self._ctx_lock:
            self._context.clear()
            self._pending_summary_queue.clear()
            self._drained_covers.clear()

        if self._save_debounce_timer:
            self._save_debounce_timer.cancel()
            self._save_debounce_timer = None

        if self._events is not None and self._events.available:
            # 先切到新会话再删：在途请求的 append 落到新会话，不会复活已删的旧会话
            session_id = self._events.new_session()
            self._events.purge_sessions(session_id)

    def context_count(self) -> int:
        with self._ctx_lock:
            return len(self._context)

    def context_entries(self) -> List[ContextEntry]:
        """上下文条目只读快照，供调试面板消费。"""
        with self._ctx_lock:
            return list(self._context)

    def score_entry(self, entry: ContextEntry) -> float:
        """单条上下文的淘汰打分，供调试面板消费。"""
        return self._score_entry(entry)

    def save_context(self, record_shutdown: bool = False):
        """立即落盘上下文，退出流程使用。"""
        self._save_context(record_shutdown=record_shutdown)

    def get_multi_turn_messages(self, max_entries: int = 10, skip_last: int = 0,
                                token_budget: int = 0) -> List[dict]:
        """从事件日志投影出多轮消息；无持久化时退回内存快照。"""
        if self._events is None or not self._events.available:
            return self._legacy_multi_turn_messages(max_entries, skip_last, token_budget)

        now = time.time()
        stats: dict = {}
        started = time.perf_counter()
        try:
            budget = Budget(
                max_entries=max_entries,
                max_tokens=token_budget,
                max_summaries=self._MAX_HISTORY_SUMMARIES,
                evict_before_ts=now - config.CONTEXT_HALF_LIFE_S,
                now=now,
                skip_last=skip_last,
            )
            profile = current_profile()
            messages = project(self._events.load(), profile, budget, stats)
        except Exception as e:
            logger.warning(f"[BrainMixin] projection failed: {e}")
            return []
        logger.debug(
            f"[BrainMixin] project session={self._events.current_session_id()[:8]} "
            f"profile={profile.name} messages={len(messages)} "
            f"dropped={stats.get('dropped_groups', 0)} orphan={stats.get('orphan_groups', 0)} "
            f"sanitized={stats.get('sanitized_lines', 0)} "
            f"elapsed={int((time.perf_counter() - started) * 1000)}ms")
        return messages

    def _legacy_multi_turn_messages(self, max_entries: int = 10, skip_last: int = 0,
                                    token_budget: int = 0) -> List[dict]:
        """内存快照路径，仅用于未启用持久化的调试与测试。"""
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
                time_str = self.format_context_time(e.timestamp)
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
    def format_context_time(timestamp: float) -> str:
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
        density_score = min(2.0, 0.4 * ((max(0, len(entry.content) - 10) + 9) // 10))
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

        summaries.sort(key=self._score_entry, reverse=True)
        summaries = summaries[:self._MAX_HISTORY_SUMMARIES]

        # 工具调用按时间抛弃：超过半衰期（默认 30min）直接丢弃
        _now = time.time()
        _max_age = config.CONTEXT_HALF_LIFE_S
        tool_calls = [
            e for e in ordinary
            if e.content.startswith("[工具调用]") and _now - e.timestamp < _max_age
        ]
        normal_chats = [e for e in ordinary if not e.content.startswith("[工具调用]")]

        # 正常聊天的空间 = 总空间 - 摘要空间 - 工具调用空间
        # clamp 到 0：预留席位超限时普通对话要全部进摘要队列，
        # 否则负数切片会按"从倒数第 N 个开始"的语义误保留一批
        base_limit = max(0, self._MAX_ENTRIES - len(summaries) - len(tool_calls))
        soft_limit = base_limit + self._EVICT_BATCH_SIZE
        if len(normal_chats) > soft_limit:
            normal_chats.sort(key=lambda e: e.timestamp, reverse=True)
            evicted = normal_chats[base_limit:]
            if evicted:
                self._pending_summary_queue.extend(
                    (e.seq, e.seq, f"[{e.role}] {e.content}") for e in evicted)
                if len(self._pending_summary_queue) > self._MAX_PENDING_QUEUE:
                    self._pending_summary_queue = self._pending_summary_queue[-self._MAX_PENDING_QUEUE:]
                logger.info(f"[BrainMixin] evicted {len(evicted)}, queued for summarization")

            normal_chats = normal_chats[:base_limit]

        self._context = sorted(summaries + tool_calls + normal_chats, key=lambda e: e.timestamp)

    def drain_pending_summaries(self) -> List[str]:
        """取出待摘要文本，并记住对应 seq 区间供摘要事件标记 covers。"""
        with self._ctx_lock:
            items = self._pending_summary_queue[:]
            self._pending_summary_queue.clear()
            self._drained_covers = [(a, b) for a, b, _ in items if a or b]
        return [text for _, _, text in items]

    @staticmethod
    def _build_fallback_summary(items: List[str]) -> str:
        """LLM 不可用时的兜底摘要。"""
        return " | ".join(item[:50] for item in items[:5])
