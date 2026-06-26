"""SQLite 持久化记忆存储"""

import sqlite3
import re
import math
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from difflib import SequenceMatcher
from typing import Optional, List, Tuple
from abc import ABC, abstractmethod

from pet.config import config

logger = logging.getLogger(__name__)

# 尝试导入 jieba，如果未安装则降级
try:
    import jieba
    import jieba.analyse
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False
    logger.info("jieba 未安装，关键词提取将使用正则降级方案")

STOP_WORDS = {
    # ── 助词 ──
    "的", "地", "得", "了", "着", "过", "吗", "呢", "吧", "啊", "呀", "哦", "哇", "嘛", "呗", "么",
    # ── 代词 ──
    "我", "你", "他", "她", "它", "我们", "你们", "他们", "她们", "它们",
    "这", "那", "这个", "那个", "这些", "那些", "这里", "那里", "这样", "那样",
    "自己", "别人", "大家", "俺", "咱", "谁", "什么", "怎么", "怎样", "为什么", "哪", "哪里",
    # ── 介词 / 连词 ──
    "在", "和", "与", "及", "或", "把", "被", "让", "给", "对", "从", "向", "往", "于",
    "以", "为", "由", "跟", "同", "至于", "关于", "除了",
    "因为", "所以", "如果", "虽然", "但是", "而且", "并且", "还是", "或者", "然后", "接着", "由于", "即使", "只要", "只有",
    # ── 副词 ──
    "很", "非常", "太", "更", "最", "也", "还", "就", "都", "已经", "正在", "将要", "马上", "立刻",
    "不", "没", "没有", "不是", "不要", "不能", "别", "勿", "未", "莫",
    "会", "能", "可以", "应该", "可能", "必须", "需要", "或许", "也许",
    "又", "再", "只", "只是", "只有", "仅仅", "甚至", "其实", "确实", "真的", "当然",
    "一定", "肯定", "大概", "也许", "经常", "偶尔", "一直", "总是", "从不", "永远",
    "比如", "例如", "其实", "不过", "此外", "另外",
    # ── 高频无意义动词 ──
    "是", "有", "说", "做", "看", "想", "觉得", "知道", "感觉", "认为", "需要", "要", "去", "来", "到", "上", "下", "进", "出",
    # ── 量词 / 数量词 ──
    "个", "些", "种", "类", "一", "二", "三", "几", "多", "少",
    # ── 时间泛指 ──
    "现在", "以前", "以后", "之前", "之后", "今天", "明天", "昨天", "刚才", "马上", "未来",
}



class LightweightDeduplicator:

    def __init__(self, ngram_size: int = 2, sim_threshold: float = 0.6):
        self.ngram_size = ngram_size
        self.sim_threshold = sim_threshold

    def _get_char_ngrams(self, text: str) -> set:
        text = re.sub(r'[^\w]', '', text.lower())
        if len(text) < self.ngram_size:
            return {text}
        return {text[i:i+self.ngram_size] for i in range(len(text) - self.ngram_size + 1)}

    def _jaccard_similarity(self, set1: set, set2: set) -> float:
        if not set1 or not set2:
            return 0.0
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        return intersection / union if union else 0.0

    def compute_similarity(self, text1: str, text2: str) -> float:
        """综合相似度：Jaccard(抗增删) + Sequence(抗语序打乱)"""
        ngrams1 = self._get_char_ngrams(text1)
        ngrams2 = self._get_char_ngrams(text2)
        jaccard_sim = self._jaccard_similarity(ngrams1, ngrams2)

        seq_sim = SequenceMatcher(None, text1, text2).ratio()

        # 加权融合：Jaccard 占大头，因为对短文本增删更鲁棒
        return 0.6 * jaccard_sim + 0.4 * seq_sim

    def find_duplicates(self, new_text: str, existing_texts: List[str]) -> List[Tuple[int, float]]:
        results = []
        for i, text in enumerate(existing_texts):
            sim = self.compute_similarity(new_text, text)
            if sim >= self.sim_threshold:
                results.append((i, sim))

        results.sort(key=lambda x: x[1], reverse=True)
        return results


def _escape_like(s: str) -> str:
    """转义 LIKE 通配符，防止关键词中的 % 和 _ 被误解析。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ── Abstract base with shared logic ──

class _MemoryRetriever(ABC):
    """Abstract base for memory retrieval strategies with shared logic."""

    # 记忆被召回后多长时间内禁止被 LLM 再次保存
    # 以下值从 config 动态读取，通过 property 暴露

    _BLOCKED_TTL = 120  # 被拦截的记忆内容保留时间（秒）
    _DUPLICATE_THRESHOLD = 0.85  # 近似重复阈值，高于此值才触发冷却拦截

    @property
    def MAX_MEMORIES(self) -> int:
        from pet.config import config
        return config.MEMORY_MAX_CAPACITY

    @property
    def RECALL_COOLDOWN_SECONDS(self) -> int:
        from pet.config import config
        return config.MEMORY_RECALL_COOLDOWN_S

    def __init__(self, conn: sqlite3.Connection, dedup_threshold: float = 0.6):
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        # RLock（可重入）：允许同一线程多次获取，防止公共方法互调时死锁
        # 约定：公共方法自行获取锁，_ 前缀私有方法假设调用方已持锁
        self._lock = threading.RLock()

        # 召回冷却：记录每条记忆最近一次被召回的时间戳
        self._recall_times: dict[int, datetime] = {}

        # 最近被冷却拦截的记忆内容（用于上下文反馈，避免 LLM 重复输出）
        self._recently_blocked: list[tuple[str, datetime]] = []

        self._deduplicator = LightweightDeduplicator(sim_threshold=dedup_threshold)
        logger.info(f"[{self.__class__.__name__}] 初始化完成，轻量去重阈值: {dedup_threshold}")

    # ── Abstract methods (subclass-specific) ──

    @abstractmethod
    def save(self, category: str, content: str, keywords: list[str], importance: int, level: str = "L2"): ...

    @abstractmethod
    def find_similar(self, content: str, keywords: list[str]) -> Tuple[Optional[dict], float]: ...

    @abstractmethod
    def query_by_text(self, text: str, limit: int = 3) -> list[dict]: ...

    # ── 记忆分级与衰减 ──

    _LEVEL_ORDER = {"L1": 0, "L2": 1, "L3": 2}
    _HALF_LIFE = {
        "L1": {},  # L1 不衰减
        "L2": {5: 90, 4: 60, 3: 45, 2: 30, 1: 21},
        "L3": {5: 3, 4: 3, 3: 2, 2: 1, 1: 1},
    }

    def _half_life(self, row: dict) -> float:
        """返回半衰期天数，L1 返回 inf。"""
        level = row.get("level", "L2")
        if level == "L1":
            return float("inf")
        importance = row.get("importance", 3)
        return self._HALF_LIFE.get(level, self._HALF_LIFE["L2"]).get(importance, 45)

    def _effective_importance(self, row: dict) -> float:
        """计算有效重要性：base * decay(基于创建时间) + recency_bonus(基于访问频次)。"""
        base = row.get("importance", 3)
        access_count = row.get("access_count", 0)

        half_life = self._half_life(row)
        if half_life == float("inf"):
            # L1 不衰减，仅加 recency_bonus
            recency_bonus = min(0.5, math.log2(1 + access_count) * 0.1)
            return min(5.0, base + recency_bonus)

        # 衰减基于 created_at（信息自然老化，不因访问而重置）
        time_str = row.get("created_at")
        if not time_str:
            return base
        try:
            ref_time = datetime.fromisoformat(time_str)
        except Exception:
            return base
        age_days = (datetime.now() - ref_time).total_seconds() / 86400
        decay = 0.5 ** (age_days / half_life)

        # 访问频次作为独立加成（回忆强化），上限 0.5
        recency_bonus = min(0.5, math.log2(1 + access_count) * 0.1)

        return base * decay + recency_bonus

    @staticmethod
    def _merge_level(existing_level: str, new_level: str) -> str:
        """合并时取较高 level（L1 > L2 > L3）。"""
        return min(existing_level, new_level, key=lambda l: _MemoryRetriever._LEVEL_ORDER.get(l, 1))

    # ── Shared concrete methods ──

    def _is_in_cooldown(self, memory_id: int, content: str = "") -> bool:
        """检查记忆是否在召回冷却期内。content 用于记录被拦截的内容。"""
        last_recall = self._recall_times.get(memory_id)
        if last_recall:
            elapsed = (datetime.now() - last_recall).total_seconds()
            if elapsed < self.RECALL_COOLDOWN_SECONDS:
                logger.info(
                    f"[{self.__class__.__name__}] 记忆冷却中，跳过保存 (距召回 {elapsed:.0f}s)"
                )
                if content:
                    self._record_blocked(content)
                return True
        return False

    def _record_blocked(self, content: str):
        """记录被冷却拦截的记忆内容，供上下文反馈使用。"""
        self._recently_blocked.append((content, datetime.now()))
        # 清理过期记录
        cutoff = datetime.now() - timedelta(seconds=self._BLOCKED_TTL)
        self._recently_blocked = [(c, t) for c, t in self._recently_blocked if t > cutoff]

    def get_recently_blocked(self) -> list[str]:
        """返回最近被拦截的记忆内容列表（未过期的）。"""
        cutoff = datetime.now() - timedelta(seconds=self._BLOCKED_TTL)
        return [c for c, t in self._recently_blocked if t > cutoff]

    @staticmethod
    def _do_merge(existing, content: str, keywords: list[str], importance: int, level: str = "L2"):
        """合并策略：保留较长内容和合并关键词，取较高 level，返回 (content, keywords, importance, level, content_changed)。"""
        merged_content = content if len(content) >= len(existing["content"]) else existing["content"]
        merged_keywords = list(set(existing["keywords"].split(",") + keywords))
        merged_importance = existing["importance"]
        merged_level = _MemoryRetriever._merge_level(existing.get("level", "L2"), level)
        content_changed = len(content) > len(existing["content"])
        if content_changed:
            merged_importance = max(existing["importance"], importance)
        return merged_content, merged_keywords, merged_importance, merged_level, content_changed

    def save_from_line(self, line: str):
        """Parse and save a memory from LLM output line."""
        line = line.strip()
        cat_match = re.match(r"\[(\w+)\][:：]?\s*(.+)", line)
        if not cat_match:
            cat_match = re.match(r"(\w+)\s+(.+)", line)
        if not cat_match:
            logger.warning(f"[{self.__class__.__name__}] 无法解析 memory 行: {line}")
            return

        category = cat_match.group(1)
        rest = cat_match.group(2)

        parts = [p.strip() for p in rest.split("|")]
        content = parts[0] if parts else rest
        keywords = []
        importance = 3
        level = "L2"

        for part in parts[1:]:
            part_stripped = part.strip()
            if part_stripped.startswith("keywords:"):
                kw_text = part_stripped[9:].strip()
                keywords = [k.strip() for k in kw_text.split(",") if k.strip()]
            elif part_stripped.startswith("importance:"):
                try:
                    importance = int(part_stripped[11:].strip())
                except ValueError:
                    pass
            elif part_stripped.startswith("level:"):
                lvl = part_stripped[6:].strip().upper()
                if lvl in ("L1", "L2", "L3"):
                    level = lvl

        if not keywords:
            keywords = self._extract_keywords(content)

        importance = max(1, min(5, importance))
        # 级别-重要性一致性兜底
        # L1 是核心事实，importance 至少为 3
        if level == "L1" and importance < 3:
            importance = 3
        # L3 是临时信息，importance 最高不超过 4（5 仅留给 L1/L2 核心记忆）
        if level == "L3" and importance > 4:
            importance = 4
        # 自动降级：importance <= 2 且非 L1 → L3
        if importance <= 2 and level != "L1":
            level = "L3"
        self.save(category, content, keywords, importance, level)

    def query_core(self, limit: int = 5) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE level != 'L3' ORDER BY importance DESC, created_at DESC LIMIT ?",
                (limit * 5,)
            ).fetchall()
        result_dicts = [dict(r) for r in rows]
        # 按 effective_importance 过滤和排序
        scored = [(r, self._effective_importance(r)) for r in result_dicts]
        filtered = [r for r, s in scored if s >= 3.5]
        filtered.sort(key=lambda r: self._effective_importance(r), reverse=True)
        filtered = filtered[:limit]
        self.touch(filtered)
        return filtered

    def query_recent(self, hours: int = 24, limit: int = 3) -> list[dict]:
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
                (since, limit)
            ).fetchall()
        self.touch(rows)
        return [dict(r) for r in rows]

    def retrieve_context(self, user_message: str) -> str:
        seen_ids = set()
        results = []

        for m in self.query_core(5):
            if m["id"] not in seen_ids:
                seen_ids.add(m["id"])
                results.append(m)

        for m in self.query_recent(24, 3):
            if m["id"] not in seen_ids:
                seen_ids.add(m["id"])
                results.append(m)

        for m in self.query_by_text(user_message, 3):
            if m["id"] not in seen_ids:
                seen_ids.add(m["id"])
                results.append(m)

        if not results:
            return ""

        # 记录被召回的记忆 ID 和时间，用于冷却期去重（加锁保护防止与 save() 竞争）
        now = datetime.now()
        with self._lock:
            for m in results:
                self._recall_times[m["id"]] = now

        lines = []
        for m in results:
            tag = "（重要）" if self._effective_importance(m) >= 3.5 else ""
            lines.append(f"- {m['content']}{tag}")

        # 附加最近被拦截的记忆，提示 LLM 不要重复输出
        blocked = self.get_recently_blocked()
        if blocked:
            lines.append("")
            lines.append("（以下信息已记录或正在保存，请勿重复输出 Memory 行）")
            for b in blocked:
                lines.append(f"- {b}")

        return "\n".join(lines)

    def _extract_keywords(self, text: str) -> list[str]:
        if JIEBA_AVAILABLE:
            keywords = jieba.analyse.extract_tags(text, topK=5)
            if keywords:
                return keywords

        # 降级方案：正则提取
        tokens = re.split(r"[\s,，。！？、；：\n]+", text)
        keywords = [
            t for t in tokens
            if len(t) >= 2 and t not in STOP_WORDS and not t.isdigit()
        ][:5]
        return keywords

    def _keyword_find_similar(self, content: str, keywords: list[str]) -> Tuple[Optional[dict], float]:
        """关键词捞取候选集 + 轻量文本相似度（两个子类的共享 fallback 逻辑）。"""
        candidate_rows = []

        if keywords:
            conditions = " OR ".join(["keywords LIKE ? ESCAPE '\\'" for _ in keywords])
            params = [f"%{_escape_like(kw)}%" for kw in keywords]
            candidate_rows = self._conn.execute(
                f"SELECT * FROM memories WHERE {conditions} LIMIT 20", params
            ).fetchall()

        if len(candidate_rows) < 3:
            recent_rows = self._conn.execute(
                "SELECT * FROM memories ORDER BY created_at DESC LIMIT 10"
            ).fetchall()
            existing_ids = {row["id"] for row in candidate_rows}
            for row in recent_rows:
                if row["id"] not in existing_ids:
                    candidate_rows.append(row)

        if not candidate_rows:
            return None, 0.0

        existing_texts = [row["content"] for row in candidate_rows]
        duplicates = self._deduplicator.find_duplicates(content, existing_texts)

        if duplicates:
            best_idx, best_score = duplicates[0]
            return dict(candidate_rows[best_idx]), best_score

        return None, 0.0

    def _keyword_query(self, text: str, limit: int = 3) -> list[dict]:
        """关键词查询的共享实现（VectorRetriever 的 fallback 也使用）。"""
        keywords = self._extract_keywords(text)
        if not keywords:
            return []
        conditions = " OR ".join(["keywords LIKE ? ESCAPE '\\'" for _ in keywords])
        params = [f"%{_escape_like(kw)}%" for kw in keywords]

        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM memories WHERE {conditions} AND level != 'L3' ORDER BY importance DESC, created_at DESC LIMIT ?",
                params + [limit * 3]
            ).fetchall()

        def match_score(row):
            row_kws = set(row["keywords"].split(","))
            return len(row_kws & set(keywords))

        result_dicts = [dict(r) for r in rows]
        # 先按关键词命中数筛选，再按 effective_importance 排序
        result_dicts.sort(key=lambda r: (match_score(r), self._effective_importance(r)), reverse=True)
        result_dicts = result_dicts[:limit]
        self.touch(result_dicts)
        return result_dicts

    def touch(self, ids_or_rows):
        if isinstance(ids_or_rows, int):
            ids_or_rows = [ids_or_rows]
        if not ids_or_rows:
            return
        if isinstance(ids_or_rows[0], int):
            ids = ids_or_rows
        else:
            ids = [r["id"] for r in ids_or_rows]
        now = datetime.now().isoformat()
        with self._lock:
            placeholders = ",".join(["?"] * len(ids))
            self._conn.execute(
                f"UPDATE memories SET access_count = access_count + 1, last_accessed_at = ? WHERE id IN ({placeholders})",
                [now] + ids
            )
            self._conn.commit()

    def enforce_capacity(self):
        """容量控制（需在已有 lock 内调用）"""
        count = self._conn.execute("SELECT COUNT(*) FROM memories").fetchall()[0][0]
        if count <= self.MAX_MEMORIES:
            # 未超容量，但仍执行轻量 L3 硬清理（仅当存在过期 L3 时才写）
            cutoff_l3 = (datetime.now() - timedelta(days=config.MEMORY_L3_EXPIRE_DAYS)).isoformat()
            stale = self._conn.execute(
                "SELECT 1 FROM memories WHERE level='L3' AND COALESCE(last_accessed_at, created_at) < ? LIMIT 1",
                (cutoff_l3,)
            ).fetchone()
            if stale:
                self._conn.execute(
                    "DELETE FROM memories WHERE level='L3' AND COALESCE(last_accessed_at, created_at) < ?",
                    (cutoff_l3,)
                )
                self._conn.commit()
            return

        # 超容量：多阶段清理，统一在最后一次 commit
        # 阶段 0：L3 硬清理
        cutoff_l3 = (datetime.now() - timedelta(days=config.MEMORY_L3_EXPIRE_DAYS)).isoformat()
        self._conn.execute(
            "DELETE FROM memories WHERE level='L3' AND COALESCE(last_accessed_at, created_at) < ?",
            (cutoff_l3,)
        )

        count = self._conn.execute("SELECT COUNT(*) FROM memories").fetchall()[0][0]
        if count <= self.MAX_MEMORIES:
            self._conn.commit()
            return

        # 阶段 1：删除 L3 中 access_count <= 1 的旧记忆
        cutoff = (datetime.now() - timedelta(days=1)).isoformat()
        self._conn.execute(
            "DELETE FROM memories WHERE level='L3' AND COALESCE(last_accessed_at, created_at) < ? AND access_count <= 1",
            (cutoff,)
        )

        # 阶段 2：L1 完全豁免，仅在 L2/L3 中按 effective_importance 淘汰
        total = self._conn.execute("SELECT COUNT(*) FROM memories").fetchall()[0][0]
        if total > self.MAX_MEMORIES:
            rows = self._conn.execute(
                "SELECT id, importance, access_count, level, created_at, last_accessed_at FROM memories WHERE level != 'L1'"
            ).fetchall()
            sorted_rows = sorted(rows, key=lambda r: self._effective_importance(dict(r)))
            excess = total - self.MAX_MEMORIES
            ids_to_delete = [r["id"] for r in sorted_rows[:excess]]
            if ids_to_delete:
                placeholders = ",".join(["?"] * len(ids_to_delete))
                self._conn.execute(
                    f"DELETE FROM memories WHERE id IN ({placeholders})",
                    ids_to_delete
                )
        self._conn.commit()  # 统一一次 commit

    def close(self):
        with self._lock:
            self._conn.close()


# ── Keyword retriever (original logic) ──

class KeywordRetriever(_MemoryRetriever):

    def save(self, category: str, content: str, keywords: list[str], importance: int = 3, level: str = "L2"):
        with self._lock:
            existing, similarity = self._find_similar(content, keywords)

            if existing:
                # 仅近似重复(≥0.85)才受冷却限制；中等相似视为合理更新，允许合并
                text_sim = self._deduplicator.compute_similarity(content, existing["content"])
                if text_sim >= self._DUPLICATE_THRESHOLD and self._is_in_cooldown(existing["id"], content):
                    return

                merged_content, merged_keywords, merged_importance, merged_level, _ = self._do_merge(
                    existing, content, keywords, importance, level
                )
                self._conn.execute(
                    "UPDATE memories SET content=?, keywords=?, importance=?, level=? WHERE id=?",
                    (merged_content, ",".join(merged_keywords), merged_importance, merged_level, existing["id"])
                )
                logger.info(f"[KeywordRetriever] 记忆合并 (相似度:{similarity:.2f}): {content[:20]}...")
            else:
                self._conn.execute(
                    "INSERT INTO memories (category, content, keywords, importance, level, created_at) VALUES (?,?,?,?,?,?)",
                    (category, content, ",".join(keywords), importance, level, datetime.now().isoformat())
                )

            self._conn.commit()
            self.enforce_capacity()

    def find_similar(self, content: str, keywords: list[str]) -> Tuple[Optional[dict], float]:
        return self._find_similar(content, keywords)

    def _find_similar(self, content: str, keywords: list[str]) -> Tuple[Optional[dict], float]:
        return self._keyword_find_similar(content, keywords)

    def query_by_text(self, text: str, limit: int = 3) -> list[dict]:
        return self._keyword_query(text, limit)


# ── Vector retriever (sqlite-vec) ──

class VectorRetriever(_MemoryRetriever):

    def __init__(self, conn: sqlite3.Connection, dedup_threshold: float = 0.6):
        super().__init__(conn, dedup_threshold)

        from pet.config import config
        from pet.brain.embedding_client import EmbeddingClient
        self._embedder = EmbeddingClient(
            url=config.EMBEDDING_URL,
            key=config.EMBEDDING_KEY,
            model=config.EMBEDDING_MODEL,
            dim=config.EMBEDDING_DIM,
        )
        self._dim = config.EMBEDDING_DIM

        self._create_vec_table()
        logger.info(f"[VectorRetriever] initialized, dim={self._dim}")

    def _create_vec_table(self):
        with self._lock:
            # 检测已有表的维度是否匹配，不匹配则重建
            try:
                existing = self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='memories_vec'"
                ).fetchone()
                if existing:
                    import sqlite_vec
                    test_vec = sqlite_vec.serialize_float32([0.0] * self._dim)
                    self._conn.execute(
                        "INSERT INTO memories_vec (memory_id, embedding) VALUES (-1, ?)",
                        (test_vec,)
                    )
                    self._conn.execute("DELETE FROM memories_vec WHERE memory_id=-1")
                    self._conn.commit()
            except Exception:
                logger.warning(f"[VectorRetriever] dimension mismatch or table error, recreating memories_vec with dim={self._dim}")
                self._conn.execute("DROP TABLE IF EXISTS memories_vec")
                self._conn.commit()

            self._conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS memories_vec USING vec0("
                f"memory_id INTEGER PRIMARY KEY, "
                f"embedding FLOAT[{self._dim}]"
                f")"
            )
            self._conn.commit()

    def _generate_embedding(self, content: str):
        """在锁外生成 embedding（网络 I/O），返回 vector 或 None。"""
        try:
            vectors = self._embedder.embed(content)
            return vectors[0]
        except Exception as e:
            logger.warning(f"[VectorRetriever] embedding 生成失败: {e}")
            return None

    def _upsert_vector(self, memory_id: int, vector):
        """将预计算的 vector 写入 memories_vec（纯 DB 操作，不含网络 I/O）。"""
        import sqlite_vec
        vec_bytes = sqlite_vec.serialize_float32(vector)
        self._conn.execute("DELETE FROM memories_vec WHERE memory_id=?", (memory_id,))
        self._conn.execute(
            "INSERT INTO memories_vec (memory_id, embedding) VALUES (?,?)",
            (memory_id, vec_bytes)
        )
        self._conn.execute("UPDATE memories SET has_embedding=1 WHERE id=?", (memory_id,))

    def save(self, category: str, content: str, keywords: list[str], importance: int = 3, level: str = "L2"):
        # Phase 1: 关键词优先去重
        with self._lock:
            existing, similarity = self._keyword_find_similar(content, keywords)
            if existing:
                # 仅近似重复(≥0.85)才受冷却限制；中等相似视为合理更新，允许合并
                text_sim = self._deduplicator.compute_similarity(content, existing["content"])
                if text_sim >= self._DUPLICATE_THRESHOLD and self._is_in_cooldown(existing["id"], content):
                    return
                merged_content, merged_keywords, merged_importance, merged_level, content_changed = self._do_merge(
                    existing, content, keywords, importance, level
                )
                self._conn.execute(
                    "UPDATE memories SET content=?, keywords=?, importance=?, level=? WHERE id=?",
                    (merged_content, ",".join(merged_keywords), merged_importance, merged_level, existing["id"])
                )
                self._conn.commit()
                self.enforce_capacity()
                logger.info(f"[VectorRetriever] memory merged via keyword (sim:{similarity:.2f}): {content[:20]}...")
                # 内容变更时补充更新向量（需 embedding，但不阻塞主流程）
                if content_changed:
                    vector = self._generate_embedding(content)
                    if vector is not None:
                        # 已在 self._lock 内，无需再次获取
                        self._upsert_vector(existing["id"], vector)
                        self._conn.commit()
                return

        # Phase 2: 关键词未命中 → 生成 embedding 做向量语义去重
        vector = self._generate_embedding(content)

        with self._lock:
            existing, similarity = self._find_similar_with_vector(content, keywords, vector)

            if existing:
                # 向量相似但内容差异大 → 视为新记忆，不走冷却
                text_sim = self._deduplicator.compute_similarity(content, existing["content"])
                if text_sim >= self._deduplicator.sim_threshold:
                    # 仅近似重复(≥0.85)才受冷却限制；中等相似视为合理更新，允许合并
                    if text_sim >= self._DUPLICATE_THRESHOLD and self._is_in_cooldown(existing["id"], content):
                        return
                    merged_content, merged_keywords, merged_importance, merged_level, content_changed = self._do_merge(
                        existing, content, keywords, importance, level
                    )
                    self._conn.execute(
                        "UPDATE memories SET content=?, keywords=?, importance=?, level=? WHERE id=?",
                        (merged_content, ",".join(merged_keywords), merged_importance, merged_level, existing["id"])
                    )
                    if content_changed and vector is not None:
                        self._upsert_vector(existing["id"], vector)
                    logger.info(f"[VectorRetriever] memory merged via vector (sim:{similarity:.2f}): {content[:20]}...")
                else:
                    # 内容差异大 → 保存为新记忆
                    logger.info(f"[VectorRetriever] vector similar but text diff ({text_sim:.2f}), save as new: {content[:20]}...")
                    existing = None  # 标记为需要新建

            if not existing:
                self._conn.execute(
                    "INSERT INTO memories (category, content, keywords, importance, level, created_at, has_embedding) VALUES (?,?,?,?,?,?,0)",
                    (category, content, ",".join(keywords), importance, level, datetime.now().isoformat())
                )
                new_id = self._conn.execute("SELECT last_insert_rowid()").fetchall()[0][0]
                if vector is not None:
                    self._upsert_vector(new_id, vector)
                logger.info(f"[VectorRetriever] new memory saved: {content[:20]}...")

            self._conn.commit()
            self.enforce_capacity()

    def _find_similar_with_vector(self, content: str, keywords: list[str], vector) -> Tuple[Optional[dict], float]:
        """使用预计算的 vector 做相似度检索，失败时 fallback 到关键词匹配。"""
        if vector is not None:
            try:
                import sqlite_vec
                vec_bytes = sqlite_vec.serialize_float32(vector)
                cursor = self._conn.execute(
                    "SELECT memory_id, distance FROM memories_vec WHERE embedding MATCH ? ORDER BY distance LIMIT 5",
                    (vec_bytes,)
                )
                vec_hits = cursor.fetchall()
                if vec_hits and vec_hits[0][1] < config.EMBEDDING_DEDUP_THRESHOLD:  # 语义去重阈值
                    mid = vec_hits[0][0]
                    row = self._conn.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchall()
                    if row:
                        return dict(row[0]), 1.0 - vec_hits[0][1]
            except Exception:
                pass

        return self._keyword_find_similar(content, keywords)

    def find_similar(self, content: str, keywords: list[str]) -> Tuple[Optional[dict], float]:
        """公开接口：自行生成 embedding 后检索（独立调用时使用）。"""
        vector = self._generate_embedding(content)
        return self._find_similar_with_vector(content, keywords, vector)

    def query_by_text(self, text: str, limit: int = 3) -> list[dict]:
        if not text:
            return []
        try:
            vectors = self._embedder.embed(text)
            query_vec = vectors[0]
            import sqlite_vec
            vec_bytes = sqlite_vec.serialize_float32(query_vec)

            cursor = self._conn.execute(
                "SELECT memory_id, distance FROM memories_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (vec_bytes, limit * 3)
            )
            vec_rows = cursor.fetchall()
            if not vec_rows:
                return []

            # distance → similarity (0~1)
            id_to_sim = {r[0]: 1.0 - min(r[1], 1.0) for r in vec_rows}
            memory_ids = list(id_to_sim.keys())

            with self._lock:
                rows = self._conn.execute(
                    f"SELECT * FROM memories WHERE id IN ({','.join('?' * len(memory_ids))}) AND has_embedding=1",
                    memory_ids
                ).fetchall()

            id_to_row = {r["id"]: dict(r) for r in rows}
            ordered = [id_to_row[mid] for mid in memory_ids if mid in id_to_row]
            # 严格隔离 L3：不参与向量检索
            ordered = [r for r in ordered if r.get("level", "L2") != "L3"]

            # 加权重排序：相似度为主（0.7），effective_importance 归一化加权（0.2），时效性加权（0.1）
            now = datetime.now()
            def rerank_score(r):
                sim = id_to_sim.get(r["id"], 0.0)
                eff_imp = self._effective_importance(r) / 5.0
                try:
                    age_hours = (now - datetime.fromisoformat(r.get("created_at", ""))).total_seconds() / 3600
                except Exception:
                    age_hours = 9999
                recency = 1.0 / (1.0 + age_hours / 24)
                return config.MEMORY_RERANK_WEIGHT_SIM * sim + config.MEMORY_RERANK_WEIGHT_IMP * eff_imp + config.MEMORY_RERANK_WEIGHT_RECENCY * recency

            ordered.sort(key=rerank_score, reverse=True)
            ordered = ordered[:limit]

            self.touch([r["id"] for r in ordered])
            return ordered
        except Exception as e:
            logger.warning(f"[VectorRetriever] vector query failed, fallback: {e}")
            return self._keyword_query(text, limit)


# ── MemoryStore wrapper ──

class MemoryStore:

    def __init__(self, db_path: str | None = None, dedup_threshold: float = 0.6):
        if db_path is None:
            db_path = str(Path(__file__).resolve().parent.parent.parent / "pet.db")

        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()

        self._create_table()
        self._retriever = self._build_retriever(dedup_threshold)
        logger.info(f"[MemoryStore] database: {self._db_path}, retriever: {type(self._retriever).__name__}")

    def _create_table(self):
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    keywords TEXT NOT NULL,
                    importance INTEGER DEFAULT 3,
                    created_at TEXT NOT NULL,
                    access_count INTEGER DEFAULT 0
                )
            """)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_importance ON memories(importance DESC)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_created ON memories(created_at DESC)")
            # Idempotent column migrations
            cursor = self._conn.execute("PRAGMA table_info(memories)")
            cols = [row[1] for row in cursor.fetchall()]
            if "has_embedding" not in cols:
                self._conn.execute("ALTER TABLE memories ADD COLUMN has_embedding INTEGER DEFAULT 0")
            if "level" not in cols:
                self._conn.execute("ALTER TABLE memories ADD COLUMN level TEXT DEFAULT 'L2'")
                # 存量数据按 importance 重新分级
                self._conn.execute("UPDATE memories SET level='L3' WHERE importance <= 2")
            if "last_accessed_at" not in cols:
                self._conn.execute("ALTER TABLE memories ADD COLUMN last_accessed_at TEXT")
                # 回填已有数据
                self._conn.execute("UPDATE memories SET last_accessed_at = created_at WHERE last_accessed_at IS NULL")
            # 复合索引：覆盖 query_core 的 WHERE level != 'L3' ORDER BY importance DESC
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_level_importance ON memories(level, importance DESC)")
            # 部分索引：仅索引 L3 行，加速 enforce_capacity 的过期清理
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_l3_access ON memories(level, last_accessed_at) WHERE level='L3'")
            self._conn.commit()

    def _try_load_vec_extension(self) -> bool:
        """Try to load sqlite-vec extension. Return True if available."""
        try:
            import sqlite_vec
            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            return True
        except Exception as e:
            logger.warning(f"[MemoryStore] sqlite-vec not available: {e}")
            return False

    def _build_retriever(self, dedup_threshold: float) -> _MemoryRetriever:
        from pet.config import config
        reasons = []
        vec_ok = False

        if not config.EMBEDDING_ENABLED:
            reasons.append("EMBEDDING_ENABLED=False")
        if not config.EMBEDDING_URL:
            reasons.append("EMBEDDING_URL not set")
        if not config.EMBEDDING_KEY:
            reasons.append("EMBEDDING_KEY not set")
        if not config.EMBEDDING_MODEL:
            reasons.append("EMBEDDING_MODEL not set")

        if not reasons:
            vec_ok = self._try_load_vec_extension()
            if not vec_ok:
                reasons.append("sqlite-vec not available")

        if vec_ok:
            try:
                logger.info("[MemoryStore] embedding config OK, initializing VectorRetriever")
                return VectorRetriever(self._conn, dedup_threshold=dedup_threshold)
            except Exception as e:
                logger.warning(f"[MemoryStore] VectorRetriever init failed: {e}, falling back to KeywordRetriever")

        logger.info(f"[MemoryStore] vector mode disabled ({', '.join(reasons)}), using KeywordRetriever")
        return KeywordRetriever(self._conn, dedup_threshold=dedup_threshold)

    def save(self, category, content, keywords, importance=3, level="L2"):
        return self._retriever.save(category, content, keywords, importance, level)

    def save_from_line(self, line: str):
        return self._retriever.save_from_line(line)

    def retrieve_context(self, user_message: str) -> str:
        return self._retriever.retrieve_context(user_message)

    def maintenance(self):
        """定期维护：L3 硬清理 + 容量控制。"""
        with self._retriever._lock:
            self._retriever.enforce_capacity()

    def close(self):
        self._retriever.close()
