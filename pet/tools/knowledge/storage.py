"""知识库存储层 — SQLite + sqlite-vec 向量检索。"""

import json
import sqlite3
import threading
import logging
from datetime import datetime
from pathlib import Path

from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)

_TOOL_DIR = Path(__file__).parent
_CONFIG_FILE = _TOOL_DIR / "config.json"

_DEFAULTS = {
    "chunk_size": 500,
    "chunk_overlap": 50,
    "embedding_enabled": False,
    "embedding_url": "",
    "embedding_key": "",
    "embedding_model": "",
    "embedding_dim": 256,
}


def _load_config() -> dict:
    """读取工具私有配置文件，合并默认值。"""
    cfg = dict(_DEFAULTS)
    if _CONFIG_FILE.is_file():
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[Knowledge] Failed to read config.json: {e}")
    return cfg


class KnowledgeStorage:
    """知识库存储，支持向量语义检索和关键词降级。"""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or TOOL_CTX.db_path()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        self._embedder = None

        self._create_tables()
        self._vec_available = self._init_vector_support()

        mode = "vector" if self._vec_available else "keyword"
        logger.info(f"[KnowledgeStorage] initialized, mode={mode}, db={self._db_path}")

    def _create_tables(self):
        """创建文档表和分块表（不依赖 sqlite-vec）。"""
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_docs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    title       TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    source      TEXT DEFAULT 'manual',
                    tags        TEXT DEFAULT '',
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id      INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content     TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    -- 删除由应用层手动处理 chunk → vec 清理顺序
                    FOREIGN KEY (doc_id) REFERENCES knowledge_docs(id)
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_doc "
                "ON knowledge_chunks(doc_id)"
            )
            self._conn.commit()

    def _init_vector_support(self) -> bool:
        """初始化向量支持：检查 embedding 配置 + 加载 sqlite-vec + 创建 vec 表。

        全部成功返回 True，任一环节失败返回 False（降级为关键词模式）。
        """
        cfg = _load_config()

        # 1. 检查 embedding_enabled 开关
        if not cfg.get("embedding_enabled"):
            logger.info("[Knowledge] embedding_enabled=False, vector mode disabled")
            return False

        url = cfg.get("embedding_url", "")
        key = cfg.get("embedding_key", "")
        model = cfg.get("embedding_model", "")
        dim = cfg.get("embedding_dim", 256)

        # 2. 检查 embedding API 配置
        if not (url and key and model):
            logger.info("[Knowledge] embedding API config not set, vector mode disabled")
            return False

        # 3. 加载 sqlite-vec 扩展
        try:
            import sqlite_vec
            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
        except Exception as e:
            logger.warning(f"[Knowledge] sqlite-vec not available: {e}")
            return False

        # 4. 维度兼容性检查（已有表时检测维度是否匹配）
        try:
            existing = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_vec'"
            ).fetchone()
            if existing:
                test_vec = sqlite_vec.serialize_float32([0.0] * dim)
                self._conn.execute(
                    "INSERT INTO knowledge_vec (chunk_id, embedding) VALUES (-1, ?)",
                    (test_vec,)
                )
                self._conn.execute("DELETE FROM knowledge_vec WHERE chunk_id=-1")
                self._conn.commit()
        except Exception:
            logger.warning("[Knowledge] dimension mismatch, recreating knowledge_vec")
            self._conn.execute("DROP TABLE IF EXISTS knowledge_vec")
            self._conn.commit()

        # 5. 创建 vec0 虚拟表
        try:
            self._conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_vec "
                f"USING vec0(chunk_id INTEGER PRIMARY KEY, "
                f"embedding FLOAT[{dim}])"
            )
            self._conn.commit()
        except Exception as e:
            logger.warning(f"[Knowledge] failed to create vec table: {e}")
            return False

        # 6. 初始化 EmbeddingClient
        try:
            from pet.brain.embedding_client import EmbeddingClient
            self._embedder = EmbeddingClient(
                url=url,
                key=key,
                model=model,
                dim=dim,
            )
        except Exception as e:
            logger.warning(f"[Knowledge] embedder init failed: {e}")
            return False

        logger.info("[Knowledge] vector mode enabled")
        return True

    # ── 写入 ──

    def add_document(self, title: str, content: str, tags: str = "",
                     source: str = "manual") -> dict:
        """添加文档 → 分块 → 生成向量（如可用）→ 存储。"""
        from pet.tools.knowledge.chunker import chunk_text

        now = datetime.now().isoformat()
        cfg = _load_config()
        chunks = chunk_text(
            content,
            max_chars=cfg["chunk_size"],
            overlap=cfg["chunk_overlap"],
        )
        if not chunks:
            chunks = [content[:cfg["chunk_size"]]]

        # Phase 1: 写入文档和分块（锁内，纯 DB 操作）
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO knowledge_docs (title, content, tags, source, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (title, content, tags, source, now, now)
            )
            doc_id = cur.lastrowid

            chunk_ids = []
            for i, chunk in enumerate(chunks):
                cur2 = self._conn.execute(
                    "INSERT INTO knowledge_chunks (doc_id, chunk_index, content, created_at) "
                    "VALUES (?,?,?,?)",
                    (doc_id, i, chunk, now)
                )
                chunk_ids.append(cur2.lastrowid)
            self._conn.commit()

        # Phase 2: 生成向量并写入（锁外执行网络 I/O，再锁内写入）
        if self._vec_available and self._embedder and chunk_ids:
            try:
                vectors = self._embedder.embed(chunks)
                with self._lock:
                    import sqlite_vec
                    for cid, vec in zip(chunk_ids, vectors):
                        vec_bytes = sqlite_vec.serialize_float32(vec)
                        self._conn.execute(
                            "INSERT INTO knowledge_vec (chunk_id, embedding) VALUES (?,?)",
                            (cid, vec_bytes)
                        )
                    self._conn.commit()
            except Exception as e:
                logger.warning(f"[Knowledge] embedding failed for doc {doc_id}: {e}")
                try:
                    self._conn.rollback()
                except Exception:
                    pass

        logger.info(f"[Knowledge] document added: id={doc_id}, title='{title}', chunks={len(chunks)}")
        return {"id": doc_id, "title": title, "chunks": len(chunks), "source": source}

    def delete_document(self, doc_id: int) -> bool:
        """删除文档及其所有分块和向量。"""
        with self._lock:
            # 先查 chunk_ids 以清理向量表
            chunk_ids = [r[0] for r in self._conn.execute(
                "SELECT id FROM knowledge_chunks WHERE doc_id=?", (doc_id,)
            ).fetchall()]

            if chunk_ids and self._vec_available:
                placeholders = ",".join("?" * len(chunk_ids))
                self._conn.execute(
                    f"DELETE FROM knowledge_vec WHERE chunk_id IN ({placeholders})",
                    chunk_ids
                )
            self._conn.execute("DELETE FROM knowledge_chunks WHERE doc_id=?", (doc_id,))
            cur = self._conn.execute("DELETE FROM knowledge_docs WHERE id=?", (doc_id,))
            self._conn.commit()
        deleted = cur.rowcount > 0
        if deleted:
            logger.info(f"[Knowledge] document deleted: id={doc_id}")
        return deleted

    # ── 检索 ──

    def search(self, query: str, limit: int = 3) -> list[dict]:
        """语义检索：query -> embedding -> 向量匹配 -> 返回相关 chunk + 文档元信息。

        向量不可用时降级为关键词 LIKE 匹配。
        """
        limit = max(1, min(limit, 10))

        if self._vec_available and self._embedder:
            try:
                vectors = self._embedder.embed(query)
                query_vec = vectors[0]
                import sqlite_vec
                vec_bytes = sqlite_vec.serialize_float32(query_vec)

                with self._lock:
                    hits = self._conn.execute(
                        "SELECT chunk_id, distance FROM knowledge_vec "
                        "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                        (vec_bytes, limit * 2)
                    ).fetchall()

                    if not hits:
                        return self._keyword_search(query, limit)

                    chunk_ids = [h[0] for h in hits]
                    placeholders = ",".join("?" * len(chunk_ids))
                    rows = self._conn.execute(
                        f"SELECT c.id, c.content, c.doc_id, d.title, d.tags "
                        f"FROM knowledge_chunks c "
                        f"JOIN knowledge_docs d ON c.doc_id = d.id "
                        f"WHERE c.id IN ({placeholders})",
                        chunk_ids
                    ).fetchall()

                # 按 distance 升序排列（距离越小越相关）
                id_to_row = {r[0]: dict(r) for r in rows}
                results = []
                for h in hits:
                    cid, dist = h[0], h[1]
                    if cid in id_to_row:
                        row = id_to_row[cid]
                        row["score"] = round(1.0 - min(dist, 1.0), 4)
                        results.append(row)

                return results[:limit]
            except Exception as e:
                logger.warning(f"[Knowledge] vector search failed, fallback: {e}")

        return self._keyword_search(query, limit)

    @staticmethod
    def _escape_like(s: str) -> str:
        """转义 LIKE 模式中的 SQL 通配符。"""
        return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def _keyword_search(self, query: str, limit: int) -> list[dict]:
        """关键词降级：LIKE 匹配。"""
        keywords = [w.strip() for w in query.split() if len(w.strip()) >= 2]
        if not keywords:
            keywords = [query.strip()]
        conditions = " OR ".join(["c.content LIKE ? ESCAPE '\\'" for _ in keywords])
        params = [f"%{self._escape_like(kw)}%" for kw in keywords]
        with self._lock:
            rows = self._conn.execute(
                f"SELECT c.id, c.content, c.doc_id, d.title, d.tags "
                f"FROM knowledge_chunks c "
                f"JOIN knowledge_docs d ON c.doc_id = d.id "
                f"WHERE {conditions} LIMIT ?",
                params + [limit]
            ).fetchall()
        return [dict(r) for r in rows]

    # ── 列表 ──

    def list_documents(self, page: int = 1, page_size: int = 20) -> dict:
        """分页列出知识文档（含分块数）。"""
        page = max(1, page)
        offset = (page - 1) * page_size
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM knowledge_docs"
            ).fetchone()[0]
            rows = self._conn.execute(
                "SELECT d.id, d.title, substr(d.content, 1, 200) as content, "
                "d.source, d.tags, d.created_at, d.updated_at, "
                "(SELECT COUNT(*) FROM knowledge_chunks c WHERE c.doc_id = d.id) as chunk_count "
                "FROM knowledge_docs d "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (page_size, offset)
            ).fetchall()
        return {
            "documents": [dict(r) for r in rows],
            "page": page,
            "total_pages": max(1, (total + page_size - 1) // page_size),
            "has_next": offset + page_size < total,
            "has_prev": page > 1,
        }

    def close(self):
        with self._lock:
            self._conn.close()
