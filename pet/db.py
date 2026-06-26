"""数据库路径管理 — 集中管理 pet.db 路径。"""

from pathlib import Path


def get_db_path() -> str:
    """返回 pet.db 的绝对路径（向上查找 pyproject.toml 定位项目根）。"""
    cur = Path(__file__).resolve().parent
    for _ in range(10):
        if (cur / "pyproject.toml").exists():
            return str(cur / "pet.db")
        cur = cur.parent
    # 回退：pet/ → 1 层 parent to project root
    return str(Path(__file__).resolve().parent.parent / "pet.db")
