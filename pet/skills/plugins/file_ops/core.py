import os
import sys
import logging

logger = logging.getLogger(__name__)


def _get_special_folder(name: str) -> str:
    """跨平台获取特殊文件夹路径（兼容中文 Windows）。"""
    if sys.platform == "win32":
        import ctypes
        csidl = {"DESKTOP": 0, "DOCUMENTS": 5, "DOWNLOADS": 40}.get(name)
        if csidl is not None:
            buf = ctypes.create_unicode_buffer(1024)
            if ctypes.windll.shell32.SHGetFolderPathW(0, csidl, 0, 0, buf) == 0:
                return buf.value
    # fallback / macOS / Linux
    return os.path.expanduser(f"~/{name.capitalize()}")


_ALLOWED_ROOTS = [
    _get_special_folder("DESKTOP"),
    _get_special_folder("DOCUMENTS"),
]


class FileOpsTool:
    def _check_path(self, path: str) -> str:
        abs_path = os.path.abspath(os.path.expanduser(path))
        for root in _ALLOWED_ROOTS:
            try:
                if os.path.commonpath([abs_path, root]) == root:
                    return abs_path
            except ValueError:
                continue
        raise PermissionError(f"不允许访问: {abs_path}")

    def list_dir(self, path: str = "~/Desktop") -> dict:
        try:
            abs_path = self._check_path(path)
        except PermissionError as e:
            return {"error": str(e)}
        if not os.path.isdir(abs_path):
            return {"error": "目录不存在"}
        try:
            items = os.listdir(abs_path)[:30]
        except PermissionError:
            return {"error": f"无权限读取目录: {abs_path}"}
        return {"path": abs_path, "items": items, "count": len(items)}

    def read_file(self, path: str, max_chars: int = 500) -> dict:
        abs_path = self._check_path(path)
        if not os.path.isfile(abs_path):
            return {"error": "文件不存在"}
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read(max_chars)
            return {"path": abs_path, "content": content, "truncated": len(content) >= max_chars}
        except Exception as e:
            return {"error": str(e)}

    def write_note(self, filename: str, content: str) -> dict:
        desktop = _get_special_folder("DESKTOP")
        path = os.path.join(desktop, filename)
        try:
            abs_path = self._check_path(path)
        except PermissionError as e:
            return {"error": str(e)}
        # 防止路径遍历
        if os.path.basename(abs_path) != filename:
            return {"error": "文件名不合法"}
        try:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            return {"error": f"写入失败: {e}"}
        return {"status": "written", "path": abs_path}