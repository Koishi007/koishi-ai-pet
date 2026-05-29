import sqlite3
import re
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

STOP_WORDS = {
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "吗", "吧", "啊",
    "呢", "什么", "那", "可以", "这个", "那个", "还", "能", "对", "让",
    "但", "而", "或", "如果", "因为", "所以", "把", "被", "从", "比",
}


class MemoryStore:
    """SQLite 持久化记忆存储。"""

    MAX_MEMORIES = 200

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = str(Path(__file__).resolve().parent.parent.parent / "memories.db")
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_table()

    def _create_table(self):
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
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_importance ON memories(importance DESC)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_created ON memories(created_at DESC)
        """)
        self._conn.commit()

    # ─── 写入 ───

    def save(self, category: str, content: str, keywords: list[str], importance: int = 3):
        """写入一条记忆。若已存在高度相似内容，则更新而非新增。"""
        existing = self._find_similar(content, keywords)
        if existing:
            new_imp = max(existing["importance"], importance)
            self._conn.execute(
                "UPDATE memories SET content=?, keywords=?, importance=?, created_at=? WHERE id=?",
                (content, ",".join(keywords), new_imp, datetime.now().isoformat(), existing["id"])
            )
        else:
            self._conn.execute(
                "INSERT INTO memories (category, content, keywords, importance, created_at) VALUES (?,?,?,?,?)",
                (category, content, ",".join(keywords), importance, datetime.now().isoformat())
            )
        self._conn.commit()
        self._enforce_capacity()
        logger.info(f"[MemoryStore] saved: [{category}] {content[:30]}...")

    def save_from_line(self, line: str):
        """从 LLM 输出的 Memory 行解析并存储。
        格式: [category] content | keywords:k1,k2,k3 | importance:N
        """
        line = line.strip()
        # 支持两种格式：[category] content 或 category content
        cat_match = re.match(r"\[(\w+)\]\s*(.+)", line)
        if not cat_match:
            # 尝试无方括号格式：category content
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

    # ─── 检索 ───

    def query_core(self, limit: int = 5) -> list[dict]:
        """获取核心记忆：importance >= 4。"""
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE importance >= 4 ORDER BY importance DESC, created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        self._touch(rows)
        return [dict(r) for r in rows]

    def query_recent(self, hours: int = 24, limit: int = 3) -> list[dict]:
        """获取近期记忆。"""
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
            (since, limit)
        ).fetchall()
        self._touch(rows)
        return [dict(r) for r in rows]

    def query_by_keywords(self, keywords: list[str], limit: int = 3) -> list[dict]:
        """关键词匹配检索。"""
        if not keywords:
            return []
        conditions = " OR ".join(["keywords LIKE ?" for _ in keywords])
        params = [f"%{kw}%" for kw in keywords]
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
        """三层检索，去重合并，返回注入 prompt 的文本。"""
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

        lines = []
        for m in results:
            tag = "（重要）" if m["importance"] >= 4 else ""
            lines.append(f"- {m['content']}{tag}")
        return "\n".join(lines)

    # ─── 关键词提取 ───

    def _extract_keywords(self, text: str) -> list[str]:
        """使用 jieba 分词提取关键词。"""
        import jieba
        import jieba.analyse
        keywords = jieba.analyse.extract_tags(text, topK=5)
        if not keywords:
            tokens = re.split(r"[\s,，。！？、；：\n]+", text)
            keywords = [
                t for t in tokens
                if len(t) >= 2 and t not in STOP_WORDS and not t.isdigit()
            ][:5]
        return keywords

    # ─── 内部工具 ───

    def _find_similar(self, content: str, keywords: list[str]) -> dict | None:
        """查找相似记忆（关键词重叠 >= 60%）。"""
        if not keywords:
            return None
        kw_set = set(keywords)
        conditions = " OR ".join(["keywords LIKE ?" for _ in keywords])
        params = [f"%{kw}%" for kw in keywords]
        rows = self._conn.execute(
            f"SELECT * FROM memories WHERE {conditions}", params
        ).fetchall()
        for row in rows:
            row_kws = set(row["keywords"].split(","))
            overlap = len(kw_set & row_kws) / max(len(kw_set), 1)
            if overlap >= 0.6:
                return dict(row)
        return None

    def _touch(self, rows):
        """增加 access_count。"""
        ids = [r["id"] for r in rows] if rows else []
        if ids:
            placeholders = ",".join(["?"] * len(ids))
            self._conn.execute(
                f"UPDATE memories SET access_count = access_count + 1 WHERE id IN ({placeholders})",
                ids
            )
            self._conn.commit()

    def _enforce_capacity(self):
        """容量管理：超出 MAX_MEMORIES 时清理低优先级旧记忆。"""
        count = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        if count <= self.MAX_MEMORIES:
            return
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        self._conn.execute(
            "DELETE FROM memories WHERE importance <= 2 AND created_at < ? AND access_count <= 1",
            (cutoff,)
        )
        self._conn.commit()
        count = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        if count > self.MAX_MEMORIES:
            excess = count - self.MAX_MEMORIES
            self._conn.execute(
                "DELETE FROM memories WHERE id IN (SELECT id FROM memories ORDER BY importance ASC, created_at ASC LIMIT ?)",
                (excess,)
            )
            self._conn.commit()

    def close(self):
        self._conn.close()
