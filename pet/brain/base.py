import math
import threading
import time
from dataclasses import dataclass, field


@dataclass
class ContextEntry:
    """一条结构化的上下文记录。"""
    role: str           # "user" | "assistant" | "system"
    content: str
    timestamp: float = field(default_factory=time.time)
    is_summary: bool = False


class BrainMixin:
    """为 Behavior 提供结构化上下文存储与加权检索。"""

    _MAX_ENTRIES = 30
    _MAX_SUMMARIES = 5

    # 角色权重：用户意图 > 系统通知 > 宠物行为
    _ROLE_WEIGHTS = {"user": 3, "system": 2, "assistant": 1}

    def __init__(self):
        self._context: list[ContextEntry] = []
        self._ctx_lock = threading.Lock()


    def add_context(self, role: str, content: str, is_summary: bool = False):
        with self._ctx_lock:
            self._context.append(ContextEntry(
                role=role, content=content, is_summary=is_summary,
            ))
            self._trim()

    def clear_context(self):
        with self._ctx_lock:
            self._context.clear()


    def context_count(self) -> int:
        with self._ctx_lock:
            return len(self._context)

    def get_context_for_llm(self, max_entries: int = 10,
                            skip_last: int = 0) -> str:
        """加权检索 + 格式化，给 chat 模式用"""
        selected = self._select_entries(max_entries, skip_last)
        if not selected:
            return ""
        return "\n".join(self._format_entries(selected))

    def get_context_inline(self, max_entries: int = 6,
                           skip_last: int = 0) -> str:
        """加权检索 + 紧凑单行格式，给 decide 模式的 user prompt 用。"""
        selected = self._select_entries(max_entries, skip_last)
        if not selected:
            return ""
        return " | ".join(self._format_entries(selected))

    def get_recent_user_messages(self, max_entries: int = 3,
                                 skip_last: int = 0) -> str:
        """获取最近的用户消息"""
        with self._ctx_lock:
            if not self._context:
                return ""
            end = -skip_last if skip_last > 0 else len(self._context)
            available = self._context[:end]
            user_msgs = [e for e in available if e.role == "user"]
            if not user_msgs:
                return ""
            user_msgs.sort(key=self._score_entry, reverse=True)
            parts = [e.content for e in user_msgs[:max_entries]]
        return " | ".join(parts)

    def _select_entries(self, max_entries: int, skip_last: int) -> list[ContextEntry]:
        """加权选择：summary 硬优先 + 普通条目按分排序后截断。"""
        with self._ctx_lock:
            if not self._context:
                return []

            end = -skip_last if skip_last > 0 else len(self._context)
            available = self._context[:end]
            if not available:
                return []

            summaries = [e for e in available if e.is_summary]
            ordinary = [e for e in available if not e.is_summary]

            # summary：按分排序，取前 N
            summaries.sort(key=self._score_entry, reverse=True)
            summaries = summaries[:self._MAX_SUMMARIES]

            # 普通条目：按分排序，填满剩余配额
            quota = max_entries - len(summaries)
            if quota > 0 and ordinary:
                ordinary.sort(key=self._score_entry, reverse=True)
                ordinary = ordinary[:quota]
            else:
                ordinary = []

            # 最终按时间排序输出
            return sorted(summaries + ordinary, key=lambda e: e.timestamp)

    def _score_entry(self, entry: ContextEntry) -> float:
        """对单条上下文打分"""
        # 1. 角色权重
        role_score = float(self._ROLE_WEIGHTS.get(entry.role, 1))

        # 2. 时间衰减：半衰期 300 秒，新消息 ≈5 分，5 分钟前的 ≈2.5 分
        age = time.time() - entry.timestamp
        time_score = 5.0 * (0.5 ** (age / 300.0))

        # 3. 内容密度：长消息通常信息量更大
        density_score = 1.0 if len(entry.content) > 30 else 0.0

        return role_score + time_score + density_score


    _ROLE_LABELS = {"user": "用户", "assistant": "宠物", "system": "系统"}

    def _format_entries(self, entries: list[ContextEntry]) -> list[str]:
        parts = []
        for e in entries:
            prefix = "摘要" if e.is_summary else self._ROLE_LABELS.get(e.role, e.role)
            parts.append(f"[{prefix}] {e.content}")
        return parts


    def _trim(self):
        """超过上限时裁剪：按分排序，保留高分条目。"""
        summaries = [e for e in self._context if e.is_summary]
        ordinary = [e for e in self._context if not e.is_summary]

        if len(summaries) > self._MAX_SUMMARIES:
            summaries.sort(key=self._score_entry, reverse=True)
            summaries = summaries[:self._MAX_SUMMARIES]

        max_ordinary = self._MAX_ENTRIES - len(summaries)
        if len(ordinary) > max_ordinary:
            ordinary.sort(key=self._score_entry, reverse=True)
            ordinary = ordinary[:max_ordinary]

        self._context = sorted(summaries + ordinary, key=lambda e: e.timestamp)
