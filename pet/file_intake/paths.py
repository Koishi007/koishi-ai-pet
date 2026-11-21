"""桌面与文档目录解析、写入路径白名单校验"""

import os
import sys


def special_folder(name: str) -> str:
    """跨平台获取特殊文件夹路径（兼容中文 Windows）。"""
    if sys.platform == "win32":
        import ctypes
        csidl = {"DESKTOP": 0, "DOCUMENTS": 5, "DOWNLOADS": 40}.get(name)
        if csidl is not None:
            buf = ctypes.create_unicode_buffer(1024)
            if ctypes.windll.shell32.SHGetFolderPathW(0, csidl, 0, 0, buf) == 0:
                return buf.value
    return os.path.expanduser(f"~/{name.capitalize()}")


_ROOTS: list[str] | None = None


def allowed_roots() -> list[str]:
    """允许读写的根目录：桌面与文档；首次调用解析后缓存。"""
    global _ROOTS
    if _ROOTS is None:
        _ROOTS = [special_folder("DESKTOP"), special_folder("DOCUMENTS")]
    return list(_ROOTS)


def desktop_dir() -> str:
    return special_folder("DESKTOP")


def check_write_path(path: str) -> str:
    """解析并校验写入路径，只允许桌面与文档目录内。

    解析失败或越界抛 PermissionError：调用方据此返回可读的拒绝原因。
    """
    expanded = os.path.expanduser(path)
    try:
        abs_path = os.path.realpath(expanded)
    except OSError as e:
        raise PermissionError(f"不允许访问: {expanded}（路径无法解析: {e}）") from e
    for root in allowed_roots():
        real_root = os.path.realpath(root)
        try:
            if os.path.commonpath([abs_path, real_root]) == real_root:
                return abs_path
        except ValueError:
            continue
    raise PermissionError(f"不允许访问: {abs_path}")
