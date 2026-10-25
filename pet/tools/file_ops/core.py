"""文件操作工具：列目录、读写文本、读取图片，限桌面与文档目录。"""

import base64
import io
import logging
import os
import sys

from pet.file_intake import REASON_OVERSIZE, load_image_with_reason, sniff
from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)

# 解码前的像素闸门，拦解压炸弹；工具读图不复用拖入文件的配置
_MAX_IMAGE_PIXELS = 40_000_000
_JPEG_QUALITY = 95


def _get_special_folder(name: str) -> str:
    """跨平台获取特殊文件夹路径（兼容中文 Windows）。"""
    if sys.platform == "win32":
        import ctypes
        csidl = {"DESKTOP": 0, "DOCUMENTS": 5, "DOWNLOADS": 40}.get(name)
        if csidl is not None:
            buf = ctypes.create_unicode_buffer(1024)
            if ctypes.windll.shell32.SHGetFolderPathW(0, csidl, 0, 0, buf) == 0:
                return buf.value
    # 回退 / macOS / Linux
    return os.path.expanduser(f"~/{name.capitalize()}")


_ALLOWED_ROOTS: list[str] | None = None


def _allowed_roots() -> list[str]:
    """允许访问的根目录，首次校验时才查询系统特殊目录。"""
    global _ALLOWED_ROOTS
    if _ALLOWED_ROOTS is None:
        _ALLOWED_ROOTS = [
            _get_special_folder("DESKTOP"),
            _get_special_folder("DOCUMENTS"),
        ]
    return _ALLOWED_ROOTS


def _encode_jpeg(image) -> str:
    """把 RGB 图片编码成 base64 JPEG，控制多模态请求体积。"""
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=_JPEG_QUALITY)
    return base64.b64encode(buf.getvalue()).decode("ascii")


class FileOpsTool:
    def _check_path(self, path: str) -> str:
        # realpath 解析 symlink / junction：否则允许目录内的链接可指向外部造成逃逸
        expanded = os.path.expanduser(path)
        try:
            abs_path = os.path.realpath(expanded)
        except OSError as e:
            # 解析失败（如路径含不受信任的装入点）时无法确认是否在允许目录内，按拒绝处理
            raise PermissionError(f"不允许访问: {expanded}（路径无法解析: {e}）") from e
        for root in _allowed_roots():
            real_root = os.path.realpath(root)
            try:
                if os.path.commonpath([abs_path, real_root]) == real_root:
                    return abs_path
            except ValueError:
                continue
        raise PermissionError(f"不允许访问: {abs_path}")

    _PAGE_SIZE = 50

    def list_dir(self, path: str = "~/Desktop", page: int = 1) -> dict:
        try:
            abs_path = self._check_path(path)
        except PermissionError as e:
            return {"error": str(e)}
        if not os.path.isdir(abs_path):
            return {"error": "目录不存在"}
        try:
            all_items = sorted(os.listdir(abs_path))
        except PermissionError:
            return {"error": f"无权限读取目录: {abs_path}"}
        total = len(all_items)
        page_size = self._PAGE_SIZE
        total_pages = (total + page_size - 1) // page_size if total else 1
        page = max(1, min(page, total_pages))
        start = (page - 1) * page_size
        items = all_items[start:start + page_size]
        return {
            "headline": f"列出目录 {abs_path}（第 {page}/{total_pages} 页，{len(items)}/{total} 项）",
            "body": "\n".join(items),
            "pagination": {"page": page, "page_size": page_size,
                           "total_items": total, "has_next": page < total_pages},
        }

    _MAX_OFFSET = 5000  # offset 上限，防止翻页耗尽轮次

    def read_file(self, path: str, max_chars: int = 1000, offset: int = 0) -> dict:
        abs_path = self._check_path(path)
        if not os.path.isfile(abs_path):
            return {"error": "文件不存在"}
        if sniff(abs_path) == "image":
            # 图片整张读取，offset / max_chars 只用于文本分页
            return self._read_image(abs_path)
        if offset >= self._MAX_OFFSET:
            return {"error": f"已读取至 offset={offset}，达到上限 {self._MAX_OFFSET}，不再翻页"}
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                f.seek(offset)
                content = f.read(max_chars)
            actual_offset = offset
            has_next = len(content) >= max_chars
            next_offset = actual_offset + len(content)
            # 到达上限则标记无下一页
            if next_offset >= self._MAX_OFFSET:
                has_next = False
            hint = ""
            if has_next:
                hint = f"还有更多内容，用 offset={next_offset} 继续读取"
            elif next_offset >= self._MAX_OFFSET:
                hint = f"已达读取上限（{self._MAX_OFFSET} 字符），不再翻页"
            return {
                "headline": (f"读取文件 {abs_path}（offset={actual_offset}，"
                             f"{len(content)} 字符{'，还有更多' if has_next else ''}）"),
                "body": content,
                "hint": hint,
            }
        except Exception as e:
            return {"error": str(e)}

    def _read_image(self, abs_path: str) -> dict:
        """图片走视觉通道：解码、缩放后以 base64 附给模型，不参与文本分页。"""
        if not TOOL_CTX.has_vision():
            return {"error": "未开启视觉理解，无法查看图片（设置 → 外观 → 视觉）"}
        image, reason = load_image_with_reason(abs_path, _MAX_IMAGE_PIXELS)
        if image is None:
            if reason == REASON_OVERSIZE:
                return {"error": f"图片像素超出上限（上限 {_MAX_IMAGE_PIXELS}）"}
            return {"error": "图片无法解析（格式不支持或文件损坏）"}
        width, height = image.size
        try:
            payload = _encode_jpeg(image)
        finally:
            image.close()
        logger.info(f"[FileOps] read image: {abs_path} {width}x{height}")
        return {
            "headline": f"查看图片 {abs_path}（{width}x{height}）",
            "images": [{"base64": payload, "mime": "image/jpeg",
                        "caption": f"查看图片 {abs_path}（{width}x{height}）"}],
        }

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
        return {"headline": f"写入文件 {abs_path}（{len(content)} 字节）"}

    def write_file(self, path: str, content: str, mode: str = "w") -> dict:
        if mode not in ("w", "a"):
            return {"error": f"不支持的写入模式: {mode!r}，仅支持 w(覆盖) 和 a(追加)"}
        try:
            abs_path = self._check_path(path)
        except PermissionError as e:
            return {"error": str(e)}
        try:
            with open(abs_path, mode, encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            return {"error": f"写入失败: {e}"}
        return {"headline": f"{'追加' if mode == 'a' else '写入'}文件 {abs_path}（{len(content)} 字节）"}