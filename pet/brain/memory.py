"""SQLite 持久化记忆存储"""

import sqlite3
import re
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from difflib import SequenceMatcher
from typing import Optional, List, Tuple
from abc import ABC, abstractmethod

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


# ── Abstract base with shared logic ──

class _MemoryRetriever(ABC):
    """Abstract base for memory retrieval strategies with shared logic."""

    MAX_MEMORIES = 200
    # 记忆被召回后多长时间内禁止被 LLM 再次保存
    RECALL_COOLDOWN_SECONDS = 300

    def __init__(self, conn: sqlite3.Connection, dedup_threshold: float = 0.6):
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

        # 召回冷却：记录每条记忆最近一次被召回的时间戳
        self._recall_times: dict[int, datetime] = {}

        self._deduplicator = LightweightDeduplicator(sim_threshold=dedup_threshold)
        logger.info(f"[{self.__class__.__name__}] 初始化完成，轻量去重阈值: {dedup_threshold}")

    # ── Abstract methods (subclass-specific) ──

    @abstractmethod
    def save(self, category: str, content: str, keywords: list[str], importance: int): ...

    @abstractmethod
    def find_similar(self, content: str, keywords: list[str]) -> Tuple[Optional[dict], float]: ...

    @abstractmethod
    def query_by_text(self, text: str, limit: int = 3) -> list[dict]: ...

    # ── Shared concrete methods ──

    def _is_in_cooldown(self, memory_id: int) -> bool:
        """检查记忆是否在召回冷却期内。"""
        last_recall = self._recall_times.get(memory_id)
        if last_recall:
            elapsed = (datetime.now() - last_recall).total_seconds()
            if elapsed < self.RECALL_COOLDOWN_SECONDS:
                logger.info(
                    f"[{self.__class__.__name__}] 记忆冷却中，跳过保存 (距召回 {elapsed:.0f}s)"
                )
                return True
        return False

    @staticmethod
    def _do_merge(existing, content: str, keywords: list[str], importance: int):
        """合并策略：保留较长内容和合并关键词，返回 (content, keywords, importance, content_changed)。"""
        merged_content = content if len(content) >= len(existing["content"]) else existing["content"]
        merged_keywords = list(set(existing["keywords"].split(",") + keywords))
        merged_importance = existing["importance"]
        content_changed = len(content) > len(existing["content"])
        if content_changed:
            merged_importance = max(existing["importance"], importance)
        return merged_content, merged_keywords, merged_importance, content_changed

    def save_from_line(self, line: str):
        """Parse and save a memory from LLM output line."""
        line = line.strip()
        cat_match = re.match(r"\[(\w+)\]\s*(.+)", line)
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
        self.touch(rows)
        return [dict(r) for r in rows]

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

    def _keyword_find_similar(self, content: str, keywords: list[str]) -> Tuple[Optional[dict], float]:
        """关键词捞取候选集 + 轻量文本相似度（两个子类的共享 fallback 逻辑）。"""
        candidate_rows = []

        if keywords:
            conditions = " OR ".join(["keywords LIKE ?" for _ in keywords])
            params = [f"%{kw}%" for kw in keywords]
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
        result_dicts = [dict(r) for r in rows]
        self.touch(result_dicts)
        return result_dicts

    def touch(self, ids_or_rows):
        if not ids_or_rows:
            return
        if isinstance(ids_or_rows[0], int):
            ids = ids_or_rows
        else:
            ids = [r["id"] for r in ids_or_rows]
        with self._lock:
            placeholders = ",".join(["?"] * len(ids))
            self._conn.execute(
                f"UPDATE memories SET access_count = access_count + 1 WHERE id IN ({placeholders})",
                ids
            )
            self._conn.commit()

    def enforce_capacity(self):
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


# ── Keyword retriever (original logic) ──

class KeywordRetriever(_MemoryRetriever):

    def save(self, category: str, content: str, keywords: list[str], importance: int = 3):
        with self._lock:
            existing, similarity = self._find_similar(content, keywords)

            if existing:
                if self._is_in_cooldown(existing["id"]):
                    return

                merged_content, merged_keywords, merged_importance, _ = self._do_merge(
                    existing, content, keywords, importance
                )
                self._conn.execute(
                    "UPDATE memories SET content=?, keywords=?, importance=? WHERE id=?",
                    (merged_content, ",".join(merged_keywords), merged_importance, existing["id"])
                )
                logger.info(f"[KeywordRetriever] 记忆合并 (相似度:{similarity:.2f}): {content[:20]}...")
            else:
                self._conn.execute(
                    "INSERT INTO memories (category, content, keywords, importance, created_at) VALUES (?,?,?,?,?)",
                    (category, content, ",".join(keywords), importance, datetime.now().isoformat())
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

        from config import config
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

    def save(self, category: str, content: str, keywords: list[str], importance: int = 3):
        # Phase 1: 关键词优先去重
        with self._lock:
            existing, similarity = self._keyword_find_similar(content, keywords)
            if existing:
                if self._is_in_cooldown(existing["id"]):
                    return
                merged_content, merged_keywords, merged_importance, content_changed = self._do_merge(
                    existing, content, keywords, importance
                )
                self._conn.execute(
                    "UPDATE memories SET content=?, keywords=?, importance=? WHERE id=?",
                    (merged_content, ",".join(merged_keywords), merged_importance, existing["id"])
                )
                self._conn.commit()
                self.enforce_capacity()
                logger.info(f"[VectorRetriever] memory merged via keyword (sim:{similarity:.2f}): {content[:20]}...")
                # 内容变更时补充更新向量（需 embedding，但不阻塞主流程）
                if content_changed:
                    vector = self._generate_embedding(content)
                    if vector is not None:
                        with self._lock:
                            self._upsert_vector(existing["id"], vector)
                            self._conn.commit()
                return

        # Phase 2: 关键词未命中 → 生成 embedding 做向量语义去重
        vector = self._generate_embedding(content)

        with self._lock:
            existing, similarity = self._find_similar_with_vector(content, keywords, vector)

            if existing:
                if self._is_in_cooldown(existing["id"]):
                    return
                merged_content, merged_keywords, merged_importance, content_changed = self._do_merge(
                    existing, content, keywords, importance
                )
                self._conn.execute(
                    "UPDATE memories SET content=?, keywords=?, importance=? WHERE id=?",
                    (merged_content, ",".join(merged_keywords), merged_importance, existing["id"])
                )
                if content_changed and vector is not None:
                    self._upsert_vector(existing["id"], vector)
                logger.info(f"[VectorRetriever] memory merged via vector (sim:{similarity:.2f}): {content[:20]}...")
            else:
                self._conn.execute(
                    "INSERT INTO memories (category, content, keywords, importance, created_at, has_embedding) VALUES (?,?,?,?,?,0)",
                    (category, content, ",".join(keywords), importance, datetime.now().isoformat())
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
                if vec_hits and vec_hits[0][1] < 0.6:  # 语义去重阈值
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

            memory_ids = [r[0] for r in vec_rows]

            with self._lock:
                rows = self._conn.execute(
                    f"SELECT * FROM memories WHERE id IN ({','.join('?' * len(memory_ids))}) AND has_embedding=1",
                    memory_ids
                ).fetchall()

            id_to_row = {r["id"]: dict(r) for r in rows}
            ordered = [id_to_row[mid] for mid in memory_ids if mid in id_to_row]

            # Re-rank by importance DESC, then created_at DESC
            ordered.sort(key=lambda r: (r["importance"], r["created_at"]), reverse=True)
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
        self._lock = threading.Lock()

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
            # Add has_embedding column idempotently
            cursor = self._conn.execute("PRAGMA table_info(memories)")
            cols = [row[1] for row in cursor.fetchall()]
            if "has_embedding" not in cols:
                self._conn.execute("ALTER TABLE memories ADD COLUMN has_embedding INTEGER DEFAULT 0")
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
        from config import config
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

    def save(self, category, content, keywords, importance=3):
        return self._retriever.save(category, content, keywords, importance)

    def save_from_line(self, line: str):
        return self._retriever.save_from_line(line)

    def retrieve_context(self, user_message: str) -> str:
        return self._retriever.retrieve_context(user_message)

    def close(self):
        self._retriever.close()
