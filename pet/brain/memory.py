"""SQLite 持久化记忆存储"""

import sqlite3
import re
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from difflib import SequenceMatcher
from typing import Optional, List, Tuple

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
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "吗", "吧", "啊",
    "呢", "什么", "那", "可以", "这个", "那个", "还", "能", "对", "让",
    "但", "而", "或", "如果", "因为", "所以", "把", "被", "从", "比",
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


class MemoryStore:

    MAX_MEMORIES = 200
    # 记忆被召回后多长时间内禁止被 LLM 再次保存（冷却期，秒）
    RECALL_COOLDOWN_SECONDS = 300  # 5 分钟

    def __init__(self, db_path: str | None = None, dedup_threshold: float = 0.6):
        if db_path is None:
            db_path = str(Path(__file__).resolve().parent.parent.parent / "pet.db")

        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

        # 召回冷却：记录每条记忆最近一次被召回的时间戳
        self._recall_times: dict[int, datetime] = {}

        self._create_table()

        self._deduplicator = LightweightDeduplicator(sim_threshold=dedup_threshold)
        logger.info(f"[MemoryStore] 初始化完成，轻量去重阈值: {dedup_threshold}")

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
            self._conn.commit()

    def save(self, category: str, content: str, keywords: list[str], importance: int = 3):
        with self._lock:
            existing, similarity = self._find_similar(content, keywords)

            if existing:
                # 冷却期检查：如果这条记忆刚被召回过，LLM 可能只是把看到的记忆复述回来
                # 跳过保存以打断正反馈循环
                last_recall = self._recall_times.get(existing["id"])
                if last_recall:
                    elapsed = (datetime.now() - last_recall).total_seconds()
                    if elapsed < self.RECALL_COOLDOWN_SECONDS:
                        logger.info(
                            f"[MemoryStore] 记忆冷却中，跳过保存 (距召回 {elapsed:.0f}s): {content[:20]}..."
                        )
                        return

                # 合并策略：保留较长内容和合并关键词
                # 不刷新 created_at，也不轻易提升 importance ——
                # 否则每次被召回后 LLM 重复输出 Memory: 行会形成正反馈循环
                merged_content = content if len(content) >= len(existing["content"]) else existing["content"]
                merged_keywords = list(set(existing["keywords"].split(",") + keywords))
                # importance 只在真正新增信息时才提升：新内容比旧内容更长才算
                merged_importance = existing["importance"]
                if len(content) > len(existing["content"]):
                    merged_importance = max(existing["importance"], importance)

                self._conn.execute(
                    "UPDATE memories SET content=?, keywords=?, importance=? WHERE id=?",
                    (merged_content, ",".join(merged_keywords), merged_importance, existing["id"])
                )
                logger.info(f"[MemoryStore] 记忆合并 (相似度:{similarity:.2f}): {content[:20]}...")
            else:
                self._conn.execute(
                    "INSERT INTO memories (category, content, keywords, importance, created_at) VALUES (?,?,?,?,?)",
                    (category, content, ",".join(keywords), importance, datetime.now().isoformat())
                )

            self._conn.commit()
            self._enforce_capacity()

    def save_from_line(self, line: str):
        line = line.strip()
        cat_match = re.match(r"\[(\w+)\]\s*(.+)", line)
        if not cat_match:
            cat_match = re.match(r"(\w+)\s+(.+)", line)
        if not cat_match:
            logger.warning(f"[MemoryStore] 无法解析 memory 行: {line}")
            return

        category = cat_match.group(1)
        rest = cat_match.group(2)

        parts = [p.strip() for p in rest.split("|")]
        content = parts[0] if parts else rest
        keywords = []
        importance = 3

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

        if not keywords:
            keywords = self._extract_keywords(content)

        importance = max(1, min(5, importance))
        self.save(category, content, keywords, importance)

    def query_core(self, limit: int = 5) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE importance >= 4 ORDER BY importance DESC, created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        self._touch(rows)
        return [dict(r) for r in rows]

    def query_recent(self, hours: int = 24, limit: int = 3) -> list[dict]:
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
                (since, limit)
            ).fetchall()
        self._touch(rows)
        return [dict(r) for r in rows]

    def query_by_keywords(self, keywords: list[str], limit: int = 3) -> list[dict]:
        if not keywords:
            return []
        conditions = " OR ".join(["keywords LIKE ?" for _ in keywords])
        params = [f"%{kw}%" for kw in keywords]
        
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM memories WHERE {conditions} ORDER BY importance DESC, created_at DESC LIMIT ?",
                params + [limit * 3]
            ).fetchall()

        def match_score(row):
            row_kws = set(row["keywords"].split(","))
            return len(row_kws & set(keywords))

        rows = sorted(rows, key=match_score, reverse=True)[:limit]
        self._touch(rows)
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

        keywords = self._extract_keywords(user_message)
        if keywords:
            for m in self.query_by_keywords(keywords, 3):
                if m["id"] not in seen_ids:
                    seen_ids.add(m["id"])
                    results.append(m)

        if not results:
            return ""

        # 记录被召回的记忆 ID 和时间，用于冷却期去重
        now = datetime.now()
        for m in results:
            self._recall_times[m["id"]] = now

        lines = []
        for m in results:
            tag = "（重要）" if m["importance"] >= 4 else ""
            lines.append(f"- {m['content']}{tag}")
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

    def _find_similar(self, content: str, keywords: list[str]) -> Tuple[Optional[dict], float]:
        """两步走漏斗：先用关键词捞取候选集（快），再用文本相似度精准打分（准）"""
        candidate_rows = []
        
        # 1. 关键词捞取候选集
        if keywords:
            conditions = " OR ".join(["keywords LIKE ?" for _ in keywords])
            params = [f"%{kw}%" for kw in keywords]
            candidate_rows = self._conn.execute(
                f"SELECT * FROM memories WHERE {conditions} LIMIT 20", params
            ).fetchall()
        
        # 2. 如果关键词匹配不到任何候选，补充最近的重要记忆作为候选集
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

        # 3. 轻量级文本相似度计算
        existing_texts = [row["content"] for row in candidate_rows]
        duplicates = self._deduplicator.find_duplicates(content, existing_texts)
        
        if duplicates:
            best_idx, best_score = duplicates[0]
            return dict(candidate_rows[best_idx]), best_score
            
        return None, 0.0

    def _touch(self, rows):
        if not rows:
            return
        ids = [r["id"] for r in rows]
        with self._lock:
            placeholders = ",".join(["?"] * len(ids))
            self._conn.execute(
                f"UPDATE memories SET access_count = access_count + 1 WHERE id IN ({placeholders})",
                ids
            )
            self._conn.commit()

    def _enforce_capacity(self):
        """容量控制（需在已有 lock 内调用）"""
        count = self._conn.execute("SELECT COUNT(*) FROM memories").fetchall()[0][0]
        if count <= self.MAX_MEMORIES:
            return
            
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        self._conn.execute(
            "DELETE FROM memories WHERE importance <= 2 AND created_at < ? AND access_count <= 1",
            (cutoff,)
        )
        self._conn.commit()
        
        count = self._conn.execute("SELECT COUNT(*) FROM memories").fetchall()[0][0]
        if count > self.MAX_MEMORIES:
            excess = count - self.MAX_MEMORIES
            self._conn.execute(
                "DELETE FROM memories WHERE id IN (SELECT id FROM memories ORDER BY importance ASC, created_at ASC LIMIT ?)",
                (excess,)
            )
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()
