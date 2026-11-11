"""CLI 输出排版：键值对齐、分组分隔与说明行。"""

from __future__ import annotations

import unicodedata
from typing import Iterable


def display_width(text: str) -> int:
    """终端显示宽度：全角字符按两列计。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in text)


def key_values(rows: Iterable[tuple[str, str]], indent: str = "") -> list[str]:
    """把 (键, 值) 排成冒号对齐的两列，值起点一致。"""
    rows = list(rows)
    width = max((display_width(key) for key, _value in rows), default=0)
    return [f"{indent}{key}:{' ' * (width - display_width(key) + 1)}{value}"
            for key, value in rows]


def sections(blocks: Iterable[tuple[str, Iterable[tuple[str, str]]]],
             indent: str = "  ") -> list[str]:
    """「标题 + 键值块」依次排列，块间空一行。"""
    lines: list[str] = []
    for title, rows in blocks:
        if lines:
            lines.append("")
        lines.append(title)
        lines.extend(key_values(rows, indent))
    return lines


def note(text: str) -> str:
    """说明行：以 # 开头，与数据行区分。"""
    return f"# {text}"
