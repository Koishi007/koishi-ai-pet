"""数据库路径管理 — 集中管理 pet.db 路径与统一连接配置。"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def get_db_path() -> str:
    """返回 pet.db 的绝对路径（向上查找 pyproject.toml 定位项目根）。"""
    cur = Path(__file__).resolve().parent
    for _ in range(10):
        if (cur / "pyproject.toml").exists():
            return str(cur / "pet.db")
        cur = cur.parent
    # 回退：pet/ → 1 层 parent to project root
    return str(Path(__file__).resolve().parent.parent / "pet.db")


def get_conn(db_path: Optional[str] = None, timeout: float = 3.0) -> sqlite3.Connection:
    """创建统一配置的 SQLite 连接，供所有模块共用 pet.db"""
    conn = sqlite3.connect(db_path or get_db_path(), timeout=timeout,
                           check_same_thread=False)
    # 每条 PRAGMA 独立 try：单条失败（如 WAL 切换需独占锁）不连累其余设置
    for pragma in (
        f"PRAGMA journal_mode=WAL",  # 持久化属性，仅首个连接真正切换
        "PRAGMA synchronous=NORMAL",
        f"PRAGMA busy_timeout={int(timeout * 1000)}",
    ):
        try:
            conn.execute(pragma)
        except sqlite3.Error as e:
            logger.debug(f"[db] PRAGMA failed ({pragma}): {e}")
    return conn
