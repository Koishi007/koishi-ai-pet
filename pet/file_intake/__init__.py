"""拖入文件的纯逻辑：类型嗅探、解码链、截断与额度、图片闸门、拒绝名单、目录摘要。

不依赖 Qt，可脱离界面单测；界面与工具侧都从这里取实现。
"""

from pet.file_intake.image import load_image
from pet.file_intake.sniff import check_drop, dir_summary, match_deny, sniff
from pet.file_intake.text import load_text, load_text_full, truncate
from pet.file_intake.types import KIND_LABELS, DropVerdict, FileRef

__all__ = [
    "KIND_LABELS",
    "DropVerdict",
    "FileRef",
    "check_drop",
    "dir_summary",
    "load_image",
    "load_text",
    "load_text_full",
    "match_deny",
    "sniff",
    "truncate",
]
