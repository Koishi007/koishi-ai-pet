"""类型嗅探、解码链、拒绝名单与目录摘要。"""

from __future__ import annotations

import fnmatch
import logging
import os
from typing import Sequence

from pet.file_intake.types import DropVerdict, FileRef

logger = logging.getLogger(__name__)

HEAD_BYTES = 8192
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".ico"})
COUNT_CAP = 200
NAME_LIMIT = 20

# 只在真的带 BOM 时才用这两个编解码器：Python 的 utf-16 对无 BOM 数据会按本机
# 字节序硬解，任何二进制都能被它解成一串汉字与韩文，进而被误判为文本
_BOM_ENCODINGS = ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16"))
_PLAIN_CHAIN = ("utf-8", "gb18030")
_SKIP_CONTROL = frozenset({0x09, 0x0A, 0x0C, 0x0D})
_TEXT_CONTROL_MAX = 0.10


def read_head(path: str, size: int = HEAD_BYTES) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(size)


def _is_common_char(char: str) -> bool:
    """判断字符是否属于文本常见范围：可打印 ASCII、空白、中日韩与全角标点。"""
    if char in "\t\n\r\f":
        return True
    if char.isascii():
        return char.isprintable()
    code = ord(char)
    return (0x3000 <= code <= 0x303F or 0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF or 0xFF00 <= code <= 0xFFEF)


def _text_score(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for char in text if _is_common_char(char)) / len(text)


def decode_bytes(raw: bytes) -> str | None:
    """按解码链解出文本，全部失败返回 None。

    带 BOM 时信任 BOM。无 BOM 时 utf-8 与 gb18030 都尝试一遍，按文本合理性打分取高者：
    GBK 的两字节序列可能恰好落在 UTF-8 的两字节区间（如「文」的 cec4），
    先到先得会把中文静默解成希腊字母一类的乱码。同分取 utf-8。
    """
    for bom, encoding in _BOM_ENCODINGS:
        if raw.startswith(bom):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                return None

    best_score = -1.0
    best_text: str | None = None
    for encoding in _PLAIN_CHAIN:
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        score = _text_score(text)
        if score > best_score:
            best_score, best_text = score, text
    return best_text


def _control_ratio(raw: bytes) -> float:
    if not raw:
        return 0.0
    control = sum(1 for byte in raw if byte < 0x20 and byte not in _SKIP_CONTROL)
    return control / len(raw)


def sniff(path: str) -> str:
    """返回 text / image / binary / dir 之一。

    图片后缀先判：PNG、JPEG 的文件头含 \\x00 与高位字节，走文本判定会被误判成二进制。
    """
    try:
        if os.path.isdir(path):
            return "dir"
        head = read_head(path)
    except OSError:
        return "binary"

    if os.path.splitext(path)[1].lower() in IMAGE_SUFFIXES:
        return "image"
    if head.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")) and decode_bytes(head) is not None:
        return "text"
    if b"\x00" in head or _control_ratio(head) > _TEXT_CONTROL_MAX:
        return "binary"
    return "text" if decode_bytes(head) is not None else "binary"


def match_deny(name: str, patterns: Sequence[str]) -> bool:
    """按文件名 glob 匹配拒绝名单。

    用 casefold 而非 os.path.normcase 做大小写归一：后者在 POSIX 上是恒等操作，
    那样写出来的不敏感只在 Windows 成立。
    """
    base = os.path.basename(name).casefold()
    return any(fnmatch.fnmatchcase(base, str(pattern).casefold()) for pattern in patterns)


def dir_summary(path: str, count_cap: int = COUNT_CAP,
                name_limit: int = NAME_LIMIT) -> tuple[int, list[str]]:
    """只读一层：返回条目数与前若干个名字，不递归、不计体积。"""
    count = 0
    names: list[str] = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                count += 1
                if len(names) < name_limit:
                    names.append(entry.name)
                if count >= count_cap:
                    break
    except OSError:
        return 0, []
    return count, names


def make_ref(path: str) -> FileRef:
    name = os.path.basename(path.rstrip("\\/")) or path
    kind = sniff(path)
    if kind == "dir":
        return FileRef(path=path, name=name, suffix="", size=0, kind=kind)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    return FileRef(path=path, name=name, suffix=os.path.splitext(name)[1].lower(),
                   size=size, kind=kind)


def check_drop(paths: Sequence[str], *, max_files: int, max_bytes: int,
               deny_patterns: Sequence[str]) -> DropVerdict:
    """放下阶段的硬条件判定：数量、体积、拒绝名单。目录跳过体积闸门。"""
    candidates = [path for path in paths if path]
    if len(candidates) > max_files:
        return DropVerdict("too_many", (), f"一次拖入了 {len(candidates)} 项，上限是 {max_files} 项")

    refs: list[FileRef] = []
    for path in candidates:
        ref = make_ref(path)
        if ref.kind != "dir" and ref.size > max_bytes:
            return DropVerdict("too_large", (), f"「{ref.name}」有 {ref.size / 1024 / 1024:.1f} MB，太重了")
        if match_deny(ref.name, deny_patterns):
            return DropVerdict("forbidden", (), f"「{ref.name}」在拒收名单里")
        refs.append(ref)
    return DropVerdict("ok", tuple(refs), "")
