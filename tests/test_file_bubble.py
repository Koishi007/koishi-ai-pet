"""文件气泡的动作可用性判定与目录摘要回填。

只构造 FileBubble 本身，不构造 PetWindow：动作判定只读 refs 与配置。
"""

import time

import pytest
from PySide6.QtWidgets import QApplication

from pet.file_intake import FileRef
from pet.tools.registry import TOOL_REGISTRY
from pet.ui import file_bubble as file_bubble_module
from pet.ui.file_bubble import FileBubble, _dir_note, describe_ref


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def bubble(qt_app, monkeypatch):
    monkeypatch.setattr(TOOL_REGISTRY, "file_actions", lambda: [])
    return FileBubble(pet_window=None)


def _ref(kind: str, name: str) -> FileRef:
    return FileRef(path=f"C:/tmp/{name}", name=name, suffix="", size=10, kind=kind)


def _actions(bubble, monkeypatch, kinds, *, vision=True, read_content=True):
    """返回 {动作 id: (按钮文案, 是否可用)}。"""
    monkeypatch.setattr(file_bubble_module.config, "VISION_ENABLED", vision)
    monkeypatch.setattr(file_bubble_module.config, "FILE_DROP_READ_CONTENT", read_content)
    bubble._refs = tuple(_ref(kind, f"{index}.{kind}") for index, kind in enumerate(kinds))
    return {action_id: (label, usable)
            for action_id, label, usable in bubble._available_actions()}


class TestCoreActions:
    def test_text_drop_available(self, bubble, monkeypatch):
        actions = _actions(bubble, monkeypatch, ["text"])
        assert actions["taste"] == ("尝一口", True)
        assert actions["read"] == ("看一看", True)

    def test_image_drop_needs_vision(self, bubble, monkeypatch):
        actions = _actions(bubble, monkeypatch, ["image"], vision=False)
        assert actions["taste"] == ("尝一口（视觉通道已关闭）", False)
        assert actions["read"] == ("看一看（视觉通道已关闭）", False)

    def test_image_drop_with_vision(self, bubble, monkeypatch):
        actions = _actions(bubble, monkeypatch, ["image"])
        assert actions["taste"] == ("尝一口", True)
        assert actions["read"] == ("看一看", True)

    def test_mixed_drop_keeps_text_actions(self, bubble, monkeypatch):
        actions = _actions(bubble, monkeypatch, ["text", "image"], vision=False)
        assert actions["taste"] == ("尝一口", True)
        assert actions["read"] == ("看一看", True)

    def test_binary_and_dir_get_taste_only(self, bubble, monkeypatch):
        for kind in ("binary", "dir"):
            actions = _actions(bubble, monkeypatch, [kind])
            assert actions["taste"] == ("尝一口", True)
            assert actions["read"] == ("看一看（没有可读文本）", False)

    def test_read_content_off_disables_core(self, bubble, monkeypatch):
        actions = _actions(bubble, monkeypatch, ["text"], read_content=False)
        assert actions["taste"] == ("尝一口（内容读取已关闭）", False)
        assert actions["read"] == ("看一看（内容读取已关闭）", False)


class TestToolActions:
    def test_text_action_matches_text_drop(self, bubble, monkeypatch):
        monkeypatch.setattr(TOOL_REGISTRY, "file_actions", lambda: [
            {"tool": "probe", "id": "ingest", "label": "收进知识库",
             "handler": None, "accepts": "text"}])
        actions = _actions(bubble, monkeypatch, ["text"])
        assert actions["tool:probe:ingest"] == ("收进知识库", True)

    def test_text_action_hidden_for_image_drop(self, bubble, monkeypatch):
        monkeypatch.setattr(TOOL_REGISTRY, "file_actions", lambda: [
            {"tool": "probe", "id": "ingest", "label": "收进知识库",
             "handler": None, "accepts": "text"}])
        actions = _actions(bubble, monkeypatch, ["image"])
        assert "tool:probe:ingest" not in actions

    def test_actions_hidden_when_read_content_off(self, bubble, monkeypatch):
        monkeypatch.setattr(TOOL_REGISTRY, "file_actions", lambda: [
            {"tool": "probe", "id": "ingest", "label": "收进知识库",
             "handler": None, "accepts": "any"}])
        actions = _actions(bubble, monkeypatch, ["text"], read_content=False)
        assert "tool:probe:ingest" not in actions

    def test_action_without_content_need_stays(self, bubble, monkeypatch):
        monkeypatch.setattr(TOOL_REGISTRY, "file_actions", lambda: [
            {"tool": "probe", "id": "todo", "label": "记进待办",
             "handler": None, "accepts": "any", "needs_content": False}])
        actions = _actions(bubble, monkeypatch, ["binary"], read_content=False)
        assert actions["tool:probe:todo"] == ("记进待办", True)


class TestSummaries:
    def test_dir_scan_fills_row(self, bubble, qt_app, tmp_path):
        folder = tmp_path / "folder"
        folder.mkdir()
        (folder / "a.txt").write_text("x", encoding="utf-8")
        bubble._refs = (FileRef(path=str(folder), name="folder", suffix="",
                                size=0, kind="dir"),)
        bubble._render_body()
        assert "扫描中…" in bubble._rows[0].text()
        for _ in range(50):
            qt_app.processEvents()
            if "1 项" in bubble._rows[0].text():
                break
            time.sleep(0.05)
        assert "1 项：a.txt" in bubble._rows[0].text()

    def test_dir_row_waits_for_scan(self):
        assert describe_ref(_ref("dir", "d")) == "d · 文件夹 · 扫描中…"

    def test_dir_row_with_note(self):
        assert describe_ref(_ref("dir", "d"), "3 项：a、b") == "d · 文件夹 · 3 项：a、b"

    def test_file_row_has_size(self):
        assert describe_ref(_ref("text", "a.txt")) == "a.txt · 文本 · 10 B"

    def test_empty_dir_note(self):
        assert _dir_note(0, []) == "空文件夹"
