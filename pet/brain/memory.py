"""SQLite 持久化记忆存储"""

import sqlite3
import re
import math
import logging
import threading
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Optional, List, Tuple
from abc import ABC, abstractmethod

from pet.config import config
from pet.db import get_conn, get_db_path

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
    "的", "地", "得", "了", "着", "过", "吗", "呢", "吧", "啊", "呀", "哦", "哇", "嘛", "呗", "么",
    "我", "你", "他", "她", "它", "我们", "你们", "他们", "她们", "它们",
    "这", "那", "这个", "那个", "这些", "那些", "这里", "那里", "这样", "那样",
    "自己", "别人", "大家", "俺", "咱", "谁", "什么", "怎么", "怎样", "为什么", "哪", "哪里",
    "在", "和", "与", "及", "或", "把", "被", "让", "给", "对", "从", "向", "往", "于",
    "以", "为", "由", "跟", "同", "至于", "关于", "除了",
    "因为", "所以", "如果", "虽然", "但是", "而且", "并且", "还是", "或者", "然后", "接着", "由于", "即使", "只要", "只有",
    "很", "非常", "太", "更", "最", "也", "还", "就", "都", "已经", "正在", "将要", "马上", "立刻",
    "不", "没", "没有", "不是", "不要", "不能", "别", "勿", "未", "莫",
    "会", "能", "可以", "应该", "可能", "必须", "需要", "或许", "也许",
    "又", "再", "只", "只是", "只有", "仅仅", "甚至", "其实", "确实", "真的", "当然",
    "一定", "肯定", "大概", "也许", "经常", "偶尔", "一直", "总是", "从不", "永远",
    "比如", "例如", "其实", "不过", "此外", "另外",
    "是", "有", "说", "做", "看", "想", "觉得", "知道", "感觉", "认为", "需要", "要", "去", "来", "到", "上", "下", "进", "出",
    "个", "些", "种", "类", "一", "二", "三", "几", "多", "少",
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


# 并入查询词的原始片段最大长度：超过该长度基本是被标点切开的长句片段，
# 而非用户/模型手写的检索词
_MAX_QUERY_TERM_LEN = 8


def _drop_subsumed(terms: list[str]) -> list[str]:
    """丢弃被其它更长词包含的碎片（不区分大小写），保持原顺序并按小写去重"""
    unique: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(term)
    return [
        t for i, t in enumerate(unique)
        if not any(len(other) > len(t) and t.lower() in other.lower()
                   for j, other in enumerate(unique) if j != i)
    ]


def _day_floor(date_str: str) -> str:
    """YYYY-MM-DD → 当天 00:00 的下界字符串（含当天）。

    created_at 以 datetime.isoformat() 存储，字典序与时间序一致，
    故可与 'YYYY-MM-DD' 直接比较，且能命中 idx_created 索引。
    """
    return datetime.strptime(date_str.strip(), "%Y-%m-%d").strftime("%Y-%m-%d")


def _day_ceil(date_str: str) -> str:
    """YYYY-MM-DD → 次日 00:00 的开区间上界字符串（不含次日）。

    与 _day_floor 配合得到 [当天 00:00, 次日 00:00) 的完整日期范围。
    """
    day = datetime.strptime(date_str.strip(), "%Y-%m-%d")
    return (day + timedelta(days=1)).strftime("%Y-%m-%d")


class _MemoryRetriever(ABC):
    """记忆检索策略的抽象基类，包含共享逻辑。"""

    # 记忆被召回后多长时间内禁止被 LLM 再次保存
    # 以下值从 config 动态读取，通过 property 暴露

    _BLOCKED_TTL = 120  # 被拦截的记忆内容保留时间（秒）
    _DUPLICATE_THRESHOLD = 0.85  # 近似重复阈值，高于此值才触发冷却拦截

    # L2 降级：effective_importance 低于此阈值的 L2 → L3
    _L2_DEMOTE_THRESHOLD = 2.2
    # L3 升级：在 _L3_PROMOTE_WINDOW 内 access_count 达到 _L3_PROMOTE_HITS → 升 L2
    _L3_PROMOTE_WINDOW = timedelta(hours=6)
    _L3_PROMOTE_HITS = 6
    # 加权冷却：冷却期内再次命中，importance 奖励上限（避免无限增长）
    _COOLDOWN_BOOST_CAP = 5

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


    @abstractmethod
    def save(self, category: str, content: str, keywords: list[str], importance: int, level: str = "L2"): ...

    @abstractmethod
    def find_similar(self, content: str, keywords: list[str]) -> Tuple[Optional[dict], float]: ...

    @abstractmethod
    def query_by_text(self, text: str, limit: int = 3) -> list[dict]: ...


    _LEVEL_ORDER = {"L1": 0, "L2": 1, "L3": 2}
    _HALF_LIFE = {
        # 仅 L1 + importance=5 永不衰减；其余 L1 走短半衰期，快速老化触发降级
        "L1": {5: float("inf"), 4: 30, 3: 21, 2: 14, 1: 7},
        "L2": {5: 60, 4: 30, 3: 14, 2: 7, 1: 3},
        "L3": {5: 3, 4: 3, 3: 2, 2: 1, 1: 1},
    }

    def _half_life(self, row: dict) -> float:
        """返回半衰期天数，永久记忆（L1+i5）返回 inf。"""
        level = row.get("level", "L2")
        importance = row.get("importance", 3)
        hl_map = self._HALF_LIFE.get(level, self._HALF_LIFE["L2"])
        return hl_map.get(importance, 45)

    # 回忆强化的时间常数（天）：最近被召回的记忆获得加成，随时间衰减回基线
    # _RECALL_BONUS_TAU 为半衰期（0.5 天 = 12 小时），_RECALL_BONUS_MAX 为最高加成比例
    _RECALL_BONUS_TAU = 0.5
    _RECALL_BONUS_MAX = 0.5

    def _recency_factor(self, row: dict) -> float:
        """回忆强化因子：基于 last_accessed_at，刚被召回时 >1，随时间衰减回 1.0"""
        last = row.get("last_accessed_at")
        if not last:
            return 1.0  # 从未被访问，中性（不加分不扣分）
        try:
            last_time = datetime.fromisoformat(last)
        except Exception:
            return 1.0
        age_days = (datetime.now() - last_time).total_seconds() / 86400
        # 指数衰减：刚访问时 bonus = _RECALL_BONUS_MAX，每 _RECALL_BONUS_TAU 天减半
        bonus = self._RECALL_BONUS_MAX * (0.5 ** (age_days / self._RECALL_BONUS_TAU))
        return 1.0 + bonus

    def _effective_importance(self, row: dict) -> float:
        """计算有效重要性：base * decay(基于创建时间) * recency_factor(基于最近访问时间)"""
        base = row.get("importance", 3)

        half_life = self._half_life(row)
        if half_life == float("inf"):
            # L1 不衰减
            decay = 1.0
        else:
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

        # 回忆强化：基于最近访问时间的衰减因子，不累积访问次数
        recency_factor = self._recency_factor(row)

        return min(5.0, base * decay * recency_factor)

    @staticmethod
    def _merge_level(existing_level: str, new_level: str) -> str:
        """合并时取较高 level（L1 > L2 > L3）。"""
        return min(existing_level, new_level, key=lambda l: _MemoryRetriever._LEVEL_ORDER.get(l, 1))


    def _is_in_cooldown(self, memory_id: int, content: str = "") -> bool:
        """检查记忆是否在召回冷却期内。content 用于记录被拦截的内容。

        加权冷却：冷却期内再次命中（用户复读/强调），虽然不重复输出给 LLM，
        但触发 importance 奖励——复读 = 情绪强调，应提升权重。
        """
        last_recall = self._recall_times.get(memory_id)
        if last_recall:
            elapsed = (datetime.now() - last_recall).total_seconds()
            if elapsed < self.RECALL_COOLDOWN_SECONDS:
                logger.info(
                    f"[{self.__class__.__name__}] 记忆冷却中，跳过保存 (距召回 {elapsed:.0f}s)"
                )
                if content:
                    self._record_blocked(content)
                # 加权冷却：复读=强调，importance 奖励（有上限）
                self._boost_importance(memory_id)
                return True
        return False

    def _boost_importance(self, memory_id: int):
        """冷却期内再次命中，提升 importance（模拟"复读=重要"的情绪权重）。"""
        row = self._conn.execute(
            "SELECT importance FROM memories WHERE id=?", (memory_id,)
        ).fetchone()
        if not row:
            return
        cur = row["importance"]
        if cur < self._COOLDOWN_BOOST_CAP:
            self._conn.execute(
                "UPDATE memories SET importance = importance + 1 WHERE id=?",
                (memory_id,)
            )
            logger.info(
                f"[{self.__class__.__name__}] 加权冷却: memory#{memory_id} importance {cur}→{cur + 1}"
            )

    def _record_blocked(self, content: str):
        """记录被冷却拦截的记忆内容，供上下文反馈使用。"""
        self._recently_blocked.append((content, datetime.now()))
        cutoff = datetime.now() - timedelta(seconds=self._BLOCKED_TTL)
        self._recently_blocked = [(c, t) for c, t in self._recently_blocked if t > cutoff]

    def get_recently_blocked(self) -> list[str]:
        """返回最近被拦截的记忆内容列表（未过期的）。"""
        cutoff = datetime.now() - timedelta(seconds=self._BLOCKED_TTL)
        return [c for c, t in self._recently_blocked if t > cutoff]

    def _cleanup_recall_times(self):
        """清理过期的召回冷却记录，防止 _recall_times 字典无限增长。"""
        if not self._recall_times:
            return
        cutoff = datetime.now() - timedelta(seconds=self.RECALL_COOLDOWN_SECONDS * 2)
        self._recall_times = {
            k: v for k, v in self._recall_times.items() if v > cutoff
        }

    def mark_recalled(self, ids: list[int]):
        """记录记忆被召回（进入冷却期），供 save 去重。"""
        now = datetime.now()
        with self._lock:
            self._cleanup_recall_times()
            for mid in ids:
                self._recall_times[mid] = now

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
        """解析 LLM 输出行并保存记忆。"""
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
                "SELECT * FROM memories ORDER BY importance DESC, created_at DESC LIMIT ?",
                (limit * 5,)
            ).fetchall()
        result_dicts = [dict(r) for r in rows]
        # 按 effective_importance 过滤和排序
        # L3 不再被排除，靠 effective_importance 自然降权——
        # 新鲜 L3（recency_factor 高）有机会进入，老 L3（衰减大）自然落选
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
        """构建上下文：核心槽 + 新鲜槽 + MMR 多样性槽。

        分层结构避免视角坍缩（同一话题占满上下文）：
        - core_slot:    按 effective_importance 选核心记忆，固定保留（人格背景）
        - recent_slot:  按时间选最新记忆，固定保留（对话连续性）
        - mmr_slot:     从文本匹配候选中按 MMR 挑选，既相关又与已选不同
        """
        from pet.config import config
        total = max(3, config.MEMORY_RECALL_COUNT)
        # 按 3:2:5 比例分配，每槽至少 1 条
        core_n = max(1, round(total * 0.3))
        recent_n = max(1, round(total * 0.2))
        mmr_n = max(1, total - core_n - recent_n)
        mmr_candidates = max(mmr_n + 3, 8)  # 候选池留余量供 MMR 挑选

        seen_ids = set()
        results = []

        # 1. 核心槽：effective_importance 最高的 N 条
        for m in self.query_core(core_n):
            if m["id"] not in seen_ids:
                seen_ids.add(m["id"])
                results.append(m)

        # 2. 新鲜槽：最近 24h 的 N 条
        for m in self.query_recent(24, recent_n):
            if m["id"] not in seen_ids:
                seen_ids.add(m["id"])
                results.append(m)

        # 3. MMR 多样性槽：从文本匹配候选中选 N 条（λ=0.7 偏相关，0.3 给多样性）
        candidates = self.query_by_text(user_message, mmr_candidates)
        candidates = [c for c in candidates if c["id"] not in seen_ids]
        mmr_picked = self._mmr_select(candidates, results, n=mmr_n, lam=0.7)
        for m in mmr_picked:
            if m["id"] not in seen_ids:
                seen_ids.add(m["id"])
                results.append(m)

        if not results:
            return ""

        # 记录被召回的记忆 ID 和时间，用于冷却期去重（加锁保护防止与 save() 竞争）
        self.mark_recalled([m["id"] for m in results])

        lines = []
        for m in results:
            tag = "（重要）" if self._effective_importance(m) >= 3.5 else ""
            time_str = self._format_memory_time(m.get("created_at", ""))
            time_suffix = f"（{time_str}）" if time_str else ""
            lines.append(f"- {m['content']}{time_suffix}{tag}")

        # 附加最近被拦截的记忆，提示 LLM 不要重复输出
        blocked = self.get_recently_blocked()
        if blocked:
            lines.append("")
            lines.append("（以下信息已记录或正在保存，请勿重复输出 Memory 行）")
            for b in blocked:
                lines.append(f"- {b}")

        return "\n".join(lines)

    def _mmr_select(self, candidates: list[dict], selected: list[dict],
                    n: int, lam: float) -> list[dict]:
        """Maximal Marginal Relevance 选择：在相关性和多样性间平衡。

        对每个候选 d:
            mmr(d) = λ * relevance(d) - (1-λ) * max_{s in selected} sim(d, s)
        每轮选 mmr 最高的加入 selected，直到填满 n 个槽位。

        - relevance: effective_importance 归一化到 [0,1]
        - sim: 复用 LightweightDeduplicator.compute_similarity（文本重合度）
        - λ=0.7 偏相关性，0.3 权重给多样性惩罚
        """
        if not candidates or n <= 0:
            return []

        pool = list(candidates)
        picked: list[dict] = []
        # 已选集合 = 传入的 selected + 本轮 picked（都参与相似度惩罚）
        selected_contents = [m.get("content", "") for m in selected]

        while pool and len(picked) < n:
            best = None
            best_score = -1.0
            for m in pool:
                rel = self._effective_importance(m) / 5.0
                if selected_contents or picked:
                    ref_contents = selected_contents + [p.get("content", "") for p in picked]
                    max_sim = max(
                        self._deduplicator.compute_similarity(m.get("content", ""), s)
                        for s in ref_contents
                    )
                else:
                    max_sim = 0.0
                score = lam * rel - (1 - lam) * max_sim
                if score > best_score:
                    best_score = score
                    best = m
            if best is None:
                break
            pool.remove(best)
            picked.append(best)

        return picked

    @staticmethod
    def _format_memory_time(created_at: str) -> str:
        """将 ISO 时间戳转为简短的中文时长描述，例如 '3小时前'、'昨天'、'5天前'。"""
        if not created_at:
            return ""
        try:
            ref = datetime.fromisoformat(created_at)
            seconds = (datetime.now() - ref).total_seconds()
            if seconds < 60:
                return "刚刚"
            elif seconds < 3600:
                return f"{int(seconds // 60)}分钟前"
            elif seconds < 86400:
                return f"{int(seconds // 3600)}小时前"
            elif seconds < 86400 * 2:
                return "昨天"
            elif seconds < 86400 * 30:
                return f"{int(seconds // 86400)}天前"
            else:
                return ref.strftime("%m-%d")
        except Exception:
            return ""

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

    def _extract_query_terms(self, query: str) -> list[str]:
        """检索用的查询词：分词结果 + 空格/标点切分出的原始短语"""
        terms = list(self._extract_keywords(query))
        tokens = [
            t.strip() for t in re.split(r"[\s,，。！？、；：\n]+", (query or "").strip())
            if t.strip()
        ]
        if len(tokens) >= 2:
            for token in tokens:
                if (2 <= len(token) <= _MAX_QUERY_TERM_LEN
                        and token not in STOP_WORDS and not token.isdigit()):
                    terms.append(token)
        return _drop_subsumed(terms)[:8]

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
        keywords = self._extract_query_terms(text)
        if not keywords:
            return []
        # 每个查询词都同时匹配 keywords 与 content，词与词之间取 OR：
        conditions = " OR ".join(
            ["(keywords LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')"
             for _ in keywords]
        )
        params = []
        for kw in keywords:
            like_val = f"%{_escape_like(kw)}%"
            params.extend([like_val, like_val])

        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM memories WHERE {conditions} ORDER BY importance DESC, created_at DESC LIMIT ?",
                params + [limit * 5]
            ).fetchall()

        def match_score(row):
            """模糊命中加权分：keywords 命中权重高于 content，命中词越多分越高"""
            row_kws = (row["keywords"] or "").lower()
            row_content = (row["content"] or "").lower()
            score = 0.0
            for kw in keywords:
                needle = kw.lower()
                if needle in row_kws:
                    score += 2.0
                elif needle in row_content:
                    score += 1.0
            return score

        result_dicts = [dict(r) for r in rows]
        # 先按命中加权分筛选，再按 effective_importance 排序
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
        now = datetime.now()
        now_iso = now.isoformat()
        with self._lock:
            placeholders = ",".join(["?"] * len(ids))
            self._conn.execute(
                f"UPDATE memories SET access_count = access_count + 1, last_accessed_at = ? WHERE id IN ({placeholders})",
                [now_iso] + ids
            )
            self._conn.commit()
            # L3 高频访问升级：短时间窗口内被多次召回 → 升 L2
            self._maybe_promote_l3(ids, now)

    def _maybe_promote_l3(self, ids: list[int], now: datetime):
        """检测 L3 记忆是否在短时间内高频被访问，若是则升级为 L2。

        升级条件：level='L3' 且 access_count >= _L3_PROMOTE_HITS
                  且 created_at 在 _L3_PROMOTE_WINDOW 内。
        """
        if not ids:
            return
        placeholders = ",".join(["?"] * len(ids))
        cutoff = (now - self._L3_PROMOTE_WINDOW).isoformat()
        rows = self._conn.execute(
            f"SELECT id, access_count FROM memories WHERE id IN ({placeholders}) "
            f"AND level='L3' AND created_at >= ? AND access_count >= ?",
            ids + [cutoff, self._L3_PROMOTE_HITS]
        ).fetchall()
        for r in rows:
            self._conn.execute(
                "UPDATE memories SET level='L2' WHERE id=?", (r["id"],)
            )
            logger.info(
                f"[{self.__class__.__name__}] L3→L2 升级: memory#{r['id']} "
                f"(access_count={r['access_count']}, 短时高频召回)"
            )
        if rows:
            self._conn.commit()

    def _demote_l2_to_l3(self):
        """降级维护：effective_importance 衰减到阈值以下的非永久记忆降一级。
        L1(非i5) → L2；L2(i<5) → L3。L1+i5 永久豁免。"""
        rows = self._conn.execute(
            "SELECT id, importance, access_count, level, created_at, last_accessed_at "
            "FROM memories WHERE level IN ('L1','L2') AND NOT (level='L1' AND importance=5)"
        ).fetchall()
        demote_l2_ids = []
        demote_l1_ids = []
        for r in rows:
            r_dict = dict(r)
            eff = self._effective_importance(r_dict)
            if eff >= self._L2_DEMOTE_THRESHOLD:
                continue
            if r["level"] == "L1":
                demote_l1_ids.append(r["id"])
            else:
                demote_l2_ids.append(r["id"])
        if demote_l2_ids:
            placeholders = ",".join(["?"] * len(demote_l2_ids))
            self._conn.execute(
                f"UPDATE memories SET level='L3' WHERE id IN ({placeholders})",
                demote_l2_ids
            )
        if demote_l1_ids:
            placeholders = ",".join(["?"] * len(demote_l1_ids))
            self._conn.execute(
                f"UPDATE memories SET level='L2' WHERE id IN ({placeholders})",
                demote_l1_ids
            )
        if demote_l2_ids or demote_l1_ids:
            self._conn.commit()
            logger.info(
                f"[{self.__class__.__name__}] 降级: "
                f"L1→L2 {len(demote_l1_ids)} 条, L2→L3 {len(demote_l2_ids)} 条 "
                f"(effective < {self._L2_DEMOTE_THRESHOLD})"
            )

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

        # 阶段 2：淘汰可降级记忆（L1+i5 与 L3 豁免）
        # 候选：L2 importance<5 + L1 importance<5，按 effective_importance 升序淘汰
        total = self._conn.execute("SELECT COUNT(*) FROM memories").fetchall()[0][0]
        if total > self.MAX_MEMORIES:
            excess = total - self.MAX_MEMORIES
            # 淘汰量为超额量的 2 倍（留少量缓冲），但不超过 10 条（避免单次大规模削减）
            target_clear = min(excess * 2, 10)
            ids_to_delete: list[int] = []

            # 2a: 低权重 L2 + 低权重 L1（按 effective_importance 升序）
            demote_rows = self._conn.execute(
                "SELECT id, importance, access_count, level, created_at, last_accessed_at "
                "FROM memories WHERE level IN ('L1','L2') AND importance < 5"
            ).fetchall()
            demote_rows.sort(key=lambda r: self._effective_importance(dict(r)))
            for r in demote_rows:
                if len(ids_to_delete) >= target_clear:
                    break
                ids_to_delete.append(r["id"])

            # 2b: 兜底——若低权重不足，淘汰 importance=5 的 L2（按 effective 升序，最老先淘汰）
            # 仅 L1+i5 永不淘汰；若仍超限，调大 MAX_MEMORIES 解决
            if len(ids_to_delete) < excess:
                remaining = excess - len(ids_to_delete)
                l2_hi_rows = self._conn.execute(
                    "SELECT id, importance, access_count, level, created_at, last_accessed_at "
                    "FROM memories WHERE level='L2' AND importance = 5"
                ).fetchall()
                l2_hi_rows.sort(key=lambda r: self._effective_importance(dict(r)))
                for r in l2_hi_rows[:remaining]:
                    ids_to_delete.append(r["id"])
                if l2_hi_rows:
                    logger.warning(
                        f"[{self.__class__.__name__}] 低权重不足，兜底淘汰 "
                        f"{min(remaining, len(l2_hi_rows))} 条 importance=5 的 L2"
                    )

            if ids_to_delete:
                placeholders = ",".join(["?"] * len(ids_to_delete))
                self._conn.execute(
                    f"DELETE FROM memories WHERE id IN ({placeholders})",
                    ids_to_delete
                )
                logger.info(
                    f"[{self.__class__.__name__}] 容量淘汰 {len(ids_to_delete)} 条 "
                    f"(目标:{target_clear}, 总数:{total}→{total - len(ids_to_delete)})"
                )
        self._conn.commit()  # 统一一次 commit

    def close(self):
        with self._lock:
            self._conn.close()



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
            need_rebuild = False
            # 检查已有表：1) 距离度量是否为 cosine  2) 维度是否匹配
            existing = self._conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='memories_vec'"
            ).fetchone()
            if existing:
                create_sql = existing[0] or ""
                # sqlite-vec 默认 L2，必须显式指定 cosine 才能正确用于语义相似度
                if "cosine" not in create_sql.lower():
                    logger.warning("[VectorRetriever] existing vec table uses non-cosine metric, rebuilding")
                    need_rebuild = True
                # 维度匹配检测（试探性插入）
                if not need_rebuild:
                    try:
                        import sqlite_vec
                        test_vec = sqlite_vec.serialize_float32([0.0] * self._dim)
                        self._conn.execute(
                            "INSERT INTO memories_vec (memory_id, embedding) VALUES (-1, ?)",
                            (test_vec,)
                        )
                        self._conn.execute("DELETE FROM memories_vec WHERE memory_id=-1")
                        self._conn.commit()
                    except Exception:
                        logger.warning(f"[VectorRetriever] dimension mismatch or table error, rebuilding with dim={self._dim}")
                        need_rebuild = True

            if need_rebuild:
                self._conn.execute("DROP TABLE IF EXISTS memories_vec")
                # 重置 has_embedding 标志，避免「标记有向量但实际已删除」的数据不一致
                self._conn.execute("UPDATE memories SET has_embedding = 0")
                self._conn.commit()

            self._conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS memories_vec USING vec0("
                f"memory_id INTEGER PRIMARY KEY, "
                f"embedding FLOAT[{self._dim}] distance_metric=cosine"
                f")"
            )
            self._conn.commit()

    def _generate_embedding(self, content: str):
        """在锁外生成 embedding（网络 I/O），返回 vector 或 None。"""
        try:
            import time
            t0 = time.monotonic()
            vectors = self._embedder.embed(content)
            elapsed = time.monotonic() - t0
            logger.debug(
                f"[VectorRetriever] embedding API 调用成功: "
                f"内容长度={len(content)} 维数={len(vectors[0])} "
                f"耗时={elapsed:.2f}s"
            )
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
        # 阶段 1：关键词优先去重
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
                    else:
                        # 向量生成失败：content 已更新但向量指向旧内容 → 重置标志，
                        # 检索时该记忆降级为关键词匹配，避免语义不一致
                        self._conn.execute(
                            "UPDATE memories SET has_embedding=0 WHERE id=?", (existing["id"],)
                        )
                        logger.warning(
                            f"[VectorRetriever] embedding 生成失败，memory#{existing['id']} "
                            f"has_embedding 已重置，降级为关键词检索"
                        )
                return

        # 阶段 2：关键词未命中 → 生成 embedding 做向量语义去重
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
                    elif content_changed and vector is None:
                        # 向量生成失败：content 已更新但向量指向旧内容 → 重置标志
                        self._conn.execute(
                            "UPDATE memories SET has_embedding=0 WHERE id=?", (existing["id"],)
                        )
                        logger.warning(
                            f"[VectorRetriever] embedding 生成失败，memory#{existing['id']} "
                            f"has_embedding 已重置，降级为关键词检索"
                        )
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

            # distance → similarity（0~1）
            id_to_sim = {r[0]: 1.0 - min(r[1], 1.0) for r in vec_rows}
            memory_ids = list(id_to_sim.keys())

            with self._lock:
                rows = self._conn.execute(
                    f"SELECT * FROM memories WHERE id IN ({','.join('?' * len(memory_ids))}) AND has_embedding=1",
                    memory_ids
                ).fetchall()

            id_to_row = {r["id"]: dict(r) for r in rows}
            ordered = [id_to_row[mid] for mid in memory_ids if mid in id_to_row]
            # L3 不再被排除：靠 rerank_score 中的 effective_importance 自然降权，
            # 但若语义高度相似（sim 高），L3 仍可逆袭进入结果

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



class MemoryStore:

    _HEAVY_INTERVAL = 6  # 每 6 次 slow_tick 执行一次重量维护（≈30min）

    def __init__(self, db_path: str | None = None, dedup_threshold: float = 0.6):
        if db_path is None:
            db_path = get_db_path()

        self._db_path = db_path
        self._conn = get_conn(db_path)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        
        self._maintenance_skip = 0

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
            # 复合索引：覆盖 query_core 的 ORDER BY importance DESC
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_level_importance ON memories(importance DESC)")
            # 部分索引：仅索引 L3 行，加速 enforce_capacity 的过期清理
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_l3_access ON memories(level, last_accessed_at) WHERE level='L3'")
            self._conn.commit()

    def _try_load_vec_extension(self) -> bool:
        """尝试加载 sqlite-vec 扩展。可用时返回 True。"""
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

    def search_memories(self, query: str, limit: int = 5) -> list[dict]:
        """语义/关键词检索记忆并计入召回冷却（recall 工具入口）。"""
        results = self._retriever.query_by_text(query, limit=limit)
        if results:
            self._retriever.mark_recalled([r["id"] for r in results])
        return results

    def maintenance(self):
        """定期维护：轻量操作每次执行，重量操作每 6 次 slow_tick 执行一次。"""
        with self._retriever._lock:
            # L3 过期硬清理
            cutoff_l3 = (datetime.now() - timedelta(days=config.MEMORY_L3_EXPIRE_DAYS)).isoformat()
            stale_l3 = self._retriever._conn.execute(
                "SELECT 1 FROM memories WHERE level='L3' "
                "AND COALESCE(last_accessed_at, created_at) < ? LIMIT 1",
                (cutoff_l3,)
            ).fetchone()
            if stale_l3:
                self._retriever._conn.execute(
                    "DELETE FROM memories WHERE level='L3' "
                    "AND COALESCE(last_accessed_at, created_at) < ?",
                    (cutoff_l3,)
                )
                self._retriever._conn.commit()

            # 每 6 次 slow_tick 执行一次
            self._maintenance_skip += 1
            if self._maintenance_skip >= self._HEAVY_INTERVAL:
                self._maintenance_skip = 0
                self._retriever._demote_l2_to_l3()
                count = self._retriever._conn.execute("SELECT COUNT(*) FROM memories").fetchall()[0][0]
                if count > self._retriever.MAX_MEMORIES:
                    self._retriever.enforce_capacity()


    def list_memories(
        self, level: str = "", importance: int = 0, search: str = "",
        start_date: str = "", end_date: str = "",
        page: int = 0, page_size: int = 50,
    ) -> tuple[list[dict], int]:
        """分页查询记忆，返回 (rows, total)。

        start_date / end_date 均为 YYYY-MM-DD，按 created_at 的日期闭区间筛选，
        格式非法时记录告警并忽略该条件（而非静默返回空）。
        """
        with self._retriever._lock:
            conn = self._retriever._conn
            where = "WHERE 1=1"
            params = []

            if level:
                where += " AND level = ?"
                params.append(level)
            if importance > 0:
                where += " AND importance = ?"
                params.append(importance)
            if search:
                where += " AND (content LIKE ? ESCAPE '\\' OR keywords LIKE ? ESCAPE '\\')"
                like_val = f"%{_escape_like(search)}%"
                params.extend([like_val, like_val])
            # 用字符串边界比较代替 date(created_at)：后者对每行调用函数，
            # 会导致 idx_created 索引失效并退化为全表扫描。
            if start_date:
                try:
                    params.append(_day_floor(start_date))
                    where += " AND created_at >= ?"
                except ValueError:
                    logger.warning(f"[MemoryStore] start_date 格式无效: {start_date!r}")
            if end_date:
                try:
                    params.append(_day_ceil(end_date))
                    where += " AND created_at < ?"
                except ValueError:
                    logger.warning(f"[MemoryStore] end_date 格式无效: {end_date!r}")

            total = conn.execute(
                f"SELECT COUNT(*) FROM memories {where}", params
            ).fetchall()[0][0]

            offset = page * page_size
            rows = conn.execute(
                "SELECT id, category, content, keywords, importance, level, "
                "created_at, last_accessed_at, access_count "
                f"FROM memories {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [page_size, offset],
            ).fetchall()

            return [dict(r) for r in rows], total

    def update_memory(
        self, memory_id: int, *, level: str | None = None, importance: int | None = None,
        keywords: str | None = None, content: str | None = None,
    ):
        """更新记忆的级别/重要性/关键词/内容。

        None 表示不更新该字段；空字符串表示清空该字段。
        """
        parts = []
        params = []
        if level is not None:
            parts.append("level = ?")
            params.append(level)
        if importance is not None:
            parts.append("importance = ?")
            params.append(importance)
        if keywords is not None:
            parts.append("keywords = ?")
            params.append(keywords)
        if content is not None:
            parts.append("content = ?")
            params.append(content)
        if not parts:
            return
        parts.append("last_accessed_at = ?")
        params.append(datetime.now().isoformat())
        params.append(memory_id)

        # 先判断正文是否真的变了，避免 metadata-only 编辑触发 embedding
        content_changed = False
        vector = None
        if content is not None and isinstance(self._retriever, VectorRetriever):
            with self._retriever._lock:
                old_row = self._retriever._conn.execute(
                    "SELECT content FROM memories WHERE id=?", (memory_id,)
                ).fetchone()
            if old_row and old_row["content"] != content:
                content_changed = True
                # embedding 网络 I/O 在锁外执行，不阻塞后台检索
                vector = self._retriever._generate_embedding(content)

        with self._retriever._lock:
            self._retriever._conn.execute(
                f"UPDATE memories SET {', '.join(parts)} WHERE id = ?",
                params,
            )
            if content_changed and isinstance(self._retriever, VectorRetriever):
                if vector is not None:
                    self._retriever._upsert_vector(memory_id, vector)
                else:
                    # embedding 生成失败，降级为关键词检索
                    self._retriever._conn.execute(
                        "DELETE FROM memories_vec WHERE memory_id=?", (memory_id,)
                    )
                    self._retriever._conn.execute(
                        "UPDATE memories SET has_embedding=0 WHERE id=?", (memory_id,)
                    )
            self._retriever._conn.commit()
            # commit 成功后才记日志，确保没有虚报
            if content_changed and isinstance(self._retriever, VectorRetriever):
                if vector is not None:
                    logger.info(f"[MemoryStore] 向量索引已重建: memory#{memory_id}")
                else:
                    logger.warning(
                        f"[MemoryStore] embedding 生成失败，memory#{memory_id} "
                        f"has_embedding 已重置，降级为关键词检索"
                    )

    def delete_memories(self, ids: list[int]):
        """批量删除记忆，同步清理向量索引。"""
        if not ids:
            return
        placeholders = ",".join(["?"] * len(ids))
        with self._retriever._lock:
            self._retriever._conn.execute(
                f"DELETE FROM memories WHERE id IN ({placeholders})", ids
            )
            if isinstance(self._retriever, VectorRetriever):
                self._retriever._conn.execute(
                    f"DELETE FROM memories_vec WHERE memory_id IN ({placeholders})", ids
                )
            self._retriever._conn.commit()

    def get_effective_importance(self, row: dict) -> float:
        """查询一条记忆的有效重要性。"""
        return self._retriever._effective_importance(row)

    def close(self):
        global _MEMORY_STORE
        self._retriever.close()
        # 持锁置空，且仅当被关闭的正是全局单例（自建实例的 close 不影响全局单例）；
        # 置空后极端时序下再次 get 会重建而非使用已关闭连接
        with _MEMORY_STORE_LOCK:
            if _MEMORY_STORE is self:
                _MEMORY_STORE = None


_MEMORY_STORE: "MemoryStore | None" = None
_MEMORY_STORE_LOCK = threading.Lock()


def get_memory_store() -> "MemoryStore":
    """全局共享单例（PetAgent、recall 工具、UI 面板共用）。"""
    global _MEMORY_STORE
    if _MEMORY_STORE is None:
        with _MEMORY_STORE_LOCK:
            if _MEMORY_STORE is None:
                _MEMORY_STORE = MemoryStore()
    return _MEMORY_STORE
