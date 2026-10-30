"""安全边界测试：文件路径白名单、URL 协议校验。"""

import base64
import subprocess
import sys

import pytest
from PIL import Image

from pet.tools.browser.core import BrowserTool
from pet.tools.file_ops import core as file_ops_core
from pet.tools.file_ops.core import FileOpsTool


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """把允许目录指向临时沙箱，避免触碰真实桌面/文档。"""
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(file_ops_core, "_ALLOWED_ROOTS", [str(desktop)])
    monkeypatch.setattr(file_ops_core, "_get_special_folder", lambda _name: str(desktop))
    return desktop


class TestCheckPath:
    def test_allows_path_inside_root(self, sandbox):
        target = sandbox / "笔记.txt"
        assert FileOpsTool()._check_path(str(target)) == str(target)

    def test_allows_root_itself(self, sandbox):
        assert FileOpsTool()._check_path(str(sandbox)) == str(sandbox)

    @pytest.mark.parametrize("path", [
        "C:\\Windows\\System32\\drivers\\etc\\hosts",
        "~/.ssh/id_rsa",
    ])
    def test_rejects_outside_root(self, sandbox, path):
        with pytest.raises(PermissionError):
            FileOpsTool()._check_path(path)

    def test_rejects_parent_escape(self, sandbox):
        escape = sandbox / ".." / "outside.txt"
        with pytest.raises(PermissionError):
            FileOpsTool()._check_path(str(escape))

    def test_rejects_sibling_with_shared_prefix(self, sandbox):
        # 仅前缀相同的兄弟目录不得被误判为允许目录
        sibling = sandbox.parent / (sandbox.name + "_evil") / "file.txt"
        with pytest.raises(PermissionError):
            FileOpsTool()._check_path(str(sibling))


def _make_symlink(link, target):
    """创建符号链接；平台或权限不支持时跳过该用例。"""
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"当前环境不支持创建符号链接: {e}")


def _make_junction(link, target) -> bool:
    """用 mklink /J 创建目录联接；非 Windows 或失败时返回 False。"""
    if not sys.platform.startswith("win"):
        return False
    proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True, text=True)
    return proc.returncode == 0


class TestLinkEscape:
    """允许目录内的链接（symlink / junction）不得成为逃逸通道。"""

    def test_rejects_file_symlink_to_outside(self, sandbox, tmp_path):
        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        link = sandbox / "escape.txt"
        _make_symlink(link, outside)
        with pytest.raises(PermissionError):
            FileOpsTool()._check_path(str(link))

    def test_rejects_dir_symlink_to_outside(self, sandbox, tmp_path):
        outside_dir = tmp_path / "outside_dir"
        outside_dir.mkdir()
        (outside_dir / "secret.txt").write_text("secret", encoding="utf-8")
        link_dir = sandbox / "escape_dir"
        _make_symlink(link_dir, outside_dir)
        with pytest.raises(PermissionError):
            FileOpsTool()._check_path(str(link_dir / "secret.txt"))

    def test_allows_symlink_inside_root(self, sandbox):
        # 指向允许目录内部的链接不应被误拒
        real = sandbox / "real.txt"
        real.write_text("ok", encoding="utf-8")
        link = sandbox / "inside_link.txt"
        _make_symlink(link, real)
        assert FileOpsTool()._check_path(str(link))

    def test_rejects_junction_to_outside(self, sandbox, tmp_path):
        outside_dir = tmp_path / "j_outside"
        outside_dir.mkdir()
        (outside_dir / "secret.txt").write_text("secret", encoding="utf-8")
        link_dir = sandbox / "j_escape"
        if not _make_junction(link_dir, outside_dir):
            pytest.skip("无法创建 junction（非 Windows 或权限不足）")
        with pytest.raises(PermissionError):
            FileOpsTool()._check_path(str(link_dir / "secret.txt"))


class TestReadFile:
    def test_missing_file_reports_error(self, sandbox):
        assert "error" in FileOpsTool().read_file(str(sandbox / "无此文件.txt"))

    def test_offset_beyond_limit_refused(self, sandbox):
        target = sandbox / "长文.txt"
        target.write_text("内容" * 10, encoding="utf-8")
        tool = FileOpsTool()
        result = tool.read_file(str(target), offset=tool._MAX_OFFSET)
        assert "error" in result

    def test_paged_read_reports_next_offset(self, sandbox):
        target = sandbox / "长文.txt"
        target.write_text("甲" * 50, encoding="utf-8")
        result = FileOpsTool().read_file(str(target), max_chars=20, offset=0)
        assert result["body"] == "甲" * 20
        assert result["headline"]
        assert "offset=20" in result["hint"]

    def test_last_page_has_no_next(self, sandbox):
        target = sandbox / "短文.txt"
        target.write_text("乙" * 10, encoding="utf-8")
        result = FileOpsTool().read_file(str(target), max_chars=100, offset=0)
        assert result["hint"] == ""

    def test_offset_is_char_based_for_multibyte(self, sandbox):
        target = sandbox / "中文.txt"
        target.write_text("甲乙丙丁戊" * 10, encoding="utf-8")
        tool = FileOpsTool()
        result = tool.read_file(str(target), max_chars=7, offset=7)
        assert result["body"] == ("甲乙丙丁戊" * 10)[7:14]
        assert "offset=14" in result["hint"]

    def test_offset_larger_than_file_reports_eof(self, sandbox):
        target = sandbox / "短文.txt"
        target.write_text("只有一点内容", encoding="utf-8")
        result = FileOpsTool().read_file(str(target), max_chars=100, offset=50)
        assert result["body"] == ""
        assert result["hint"] == "已到文件末尾"

    def test_full_paging_covers_file_without_loss(self, sandbox):
        target = sandbox / "长文.txt"
        text = "恋恋的日记\n" * 60
        target.write_bytes(text.encode("utf-8"))
        tool = FileOpsTool()
        chunks, offset = [], 0
        while True:
            result = tool.read_file(str(target), max_chars=37, offset=offset)
            chunks.append(result["body"])
            if "offset=" not in result["hint"]:
                break
            offset = int(result["hint"].split("offset=")[1].split(" ")[0])
        assert "".join(chunks) == text

    def test_illegal_path_raises_permission_error(self, sandbox):
        # read_file 不做兜底，由执行器统一转为错误结果
        with pytest.raises(PermissionError):
            FileOpsTool().read_file("C:\\Windows\\win.ini")


class _VisionCtx:
    """替身 TOOL_CTX：只提供读图需要的视觉能力查询。"""

    def __init__(self, enabled: bool):
        self.enabled = enabled

    def has_vision(self) -> bool:
        return self.enabled


class TestReadImage:
    def _make_image(self, path, size=(32, 16)) -> None:
        Image.new("RGB", size, (12, 34, 56)).save(path)

    def test_image_returned_as_visual_attachment(self, sandbox, monkeypatch):
        monkeypatch.setattr(file_ops_core, "TOOL_CTX", _VisionCtx(True))
        target = sandbox / "图.png"
        self._make_image(target)
        result = FileOpsTool().read_file(str(target))
        image = result["images"][0]
        assert image["mime"] == "image/jpeg"
        assert "32x16" in result["headline"]
        assert base64.b64decode(image["base64"]).startswith(b"\xff\xd8")

    def test_image_rejected_without_vision(self, sandbox, monkeypatch):
        monkeypatch.setattr(file_ops_core, "TOOL_CTX", _VisionCtx(False))
        target = sandbox / "图.png"
        self._make_image(target)
        assert "error" in FileOpsTool().read_file(str(target))

    def test_over_pixel_gate_reports_error(self, sandbox, monkeypatch):
        monkeypatch.setattr(file_ops_core, "TOOL_CTX", _VisionCtx(True))
        monkeypatch.setattr(file_ops_core, "_MAX_IMAGE_PIXELS", 100)
        target = sandbox / "大图.png"
        self._make_image(target)
        result = FileOpsTool().read_file(str(target))
        assert "像素超出上限" in result["error"]
        assert "无法解析" not in result["error"]

    def test_broken_image_reports_error(self, sandbox, monkeypatch):
        monkeypatch.setattr(file_ops_core, "TOOL_CTX", _VisionCtx(True))
        target = sandbox / "坏图.png"
        target.write_bytes(b"not an image")
        result = FileOpsTool().read_file(str(target))
        assert "无法解析" in result["error"]
        assert "像素超出上限" not in result["error"]


class TestWriteOps:
    def test_write_note_rejects_subpath_filename(self, sandbox):
        # 白名单内的路径，但文件名带子路径 → 拒绝
        result = FileOpsTool().write_note("sub/备忘.txt", "内容")
        assert result.get("error")
        assert result.get("status") != "written"
        assert not (sandbox / "sub" / "备忘.txt").exists()

    def test_write_note_rejects_parent_escape(self, sandbox):
        result = FileOpsTool().write_note("../逃逸.txt", "内容")
        assert "error" in result
        assert not (sandbox.parent / "逃逸.txt").exists()

    def test_write_note_writes_into_sandbox(self, sandbox):
        result = FileOpsTool().write_note("备忘.txt", "你好")
        assert result["headline"]
        assert (sandbox / "备忘.txt").read_text(encoding="utf-8") == "你好"

    def test_write_file_rejects_unknown_mode(self, sandbox):
        result = FileOpsTool().write_file(str(sandbox / "a.txt"), "x", mode="x")
        assert result.get("error")
        assert not (sandbox / "a.txt").exists()

    def test_write_file_outside_root_reports_error(self, sandbox):
        result = FileOpsTool().write_file("C:\\Windows\\evil.txt", "x")
        assert "error" in result


class TestValidateUrl:
    def _tool(self) -> BrowserTool:
        return object.__new__(BrowserTool)

    @pytest.mark.parametrize("url", [
        "https://example.com/a?b=1",
        "http://localhost:8000",
        "HTTPS://EXAMPLE.COM",
    ])
    def test_http_and_https_allowed(self, url):
        assert self._tool()._validate_url(url) is None

    @pytest.mark.parametrize("url", [
        "file:///C:/Windows/win.ini",
        "javascript:alert(1)",
        "ftp://example.com/f",
        "data:text/html,<h1>x</h1>",
    ])
    def test_other_schemes_rejected(self, url):
        assert self._tool()._validate_url(url) is not None

    def test_empty_url_rejected(self):
        assert self._tool()._validate_url("")

    def test_label_included_in_message(self):
        assert "页面地址" in self._tool()._validate_url("", label="页面地址")
