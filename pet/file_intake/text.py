"""文本读取与截断。"""

from __future__ import annotations

import logging

from pet.file_intake.sniff import decode_bytes, sniff

logger = logging.getLogger(__name__)

_OMIT = "……（已省略 {n} 字符）……"

REASON_BINARY = "内容不是可识别的文字，读不出来"
REASON_DIR = "这是一个文件夹，没有正文"
REASON_IMAGE = "这是一张图片，正文内容走视觉通道"
REASON_UNREADABLE = "读不到这个文件（权限或占用）"


def truncate(text: str, limit: int) -> str:
    """超限时取首尾两段：首 2/3 额度、末 1/3 额度，省略标记不计入额度。

    默认额度 1500 对应首 1000 与末 500。
    """
    if limit <= 0 or len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    omitted = len(text) - limit
    if tail <= 0:
        return f"{text[:head]}{_OMIT.format(n=omitted)}"
    return f"{text[:head]}{_OMIT.format(n=omitted)}{text[-tail:]}"


def _read_all(path: str) -> bytes | None:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as e:
        logger.info(f"[FileIntake] read failed: {path} {e}")
        return None


def load_text(path: str, limit: int) -> tuple[str, str]:
    """返回（片段, 降级说明）。说明为空表示读到了内容。"""
    kind = sniff(path)
    if kind == "dir":
        return "", REASON_DIR
    if kind == "image":
        return "", REASON_IMAGE
    if kind == "binary":
        return "", REASON_BINARY

    raw = _read_all(path)
    if raw is None:
        return "", REASON_UNREADABLE
    text = decode_bytes(raw)
    if text is None:
        return "", REASON_BINARY
    return truncate(text, limit), ""


def load_text_full(path: str) -> str:
    """整篇读取，供工具动作入库使用；不可读返回空串。"""
    if sniff(path) != "text":
        return ""
    raw = _read_all(path)
    if raw is None:
        return ""
    return decode_bytes(raw) or ""
