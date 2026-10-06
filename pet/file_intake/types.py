"""拖入文件的数据结构。"""

from dataclasses import dataclass

# kind 到展示名的映射，界面与提示词共用
KIND_LABELS = {"text": "文本", "image": "图片", "binary": "二进制", "dir": "文件夹"}


@dataclass(frozen=True)
class FileRef:
    """一个拖入项的元信息。目录的 size 记 0，suffix 为空串。"""

    path: str
    name: str
    suffix: str
    size: int
    kind: str


@dataclass(frozen=True)
class DropVerdict:
    """放下阶段的判定结果。status 非 ok 时 refs 为空。"""

    status: str
    refs: tuple[FileRef, ...] = ()
    detail: str = ""
