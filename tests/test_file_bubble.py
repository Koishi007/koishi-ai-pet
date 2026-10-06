"""文件气泡的动作可用性判定、按钮状态与目录摘要回填。

只构造 FileBubble 本身，不构造 PetWindow：动作判定只读 refs 与配置，
需要重绘按钮的用例用几何桩替代窗口。
"""

import time

import pytest
from PySide6.QtCore import QRect
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


class _WindowStub:
    """只提供 FileBubble 定位所需的几何信息。"""

    def __init__(self, rect: QRect):
        self._rect = rect

    def geometry(self) -> QRect:
        return self._rect

    def screen(self):
        return None


@pytest.fixture
def wired_bubble(qt_app, monkeypatch):
    """带定位窗口的实例：重建按钮会顺带更新位置。"""
    monkeypatch.setattr(TOOL_REGISTRY, "file_actions", lambda: [])
    return FileBubble(pet_window=_WindowStub(QRect(100, 100, 64, 64)))


def _wait_until(qt_app, condition, timeout_s: float = 3.0) -> bool:
    """驱动事件循环直到条件成立，用于等动画结束。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return condition()


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


class TestButtonStates:
    """按钮可用性：不可用动作保留按钮但置灰，取消始终可用且不产生请求。"""

    def _button(self, bubble, text: str):
        for index in range(bubble._buttons.count()):
            widget = bubble._buttons.itemAt(index).widget()
            if widget is not None and widget.text() == text:
                return widget
        raise AssertionError(f"按钮不存在: {text}")

    def test_unreadable_drop_greys_out_read(self, wired_bubble, monkeypatch):
        actions = _actions(wired_bubble, monkeypatch, ["binary"])
        assert actions["read"] == ("看一看（没有可读文本）", False)
        wired_bubble._render_buttons()
        button = self._button(wired_bubble, "看一看（没有可读文本）")
        assert not button.isEnabled()
        assert ":disabled" in button.styleSheet()

    def test_readable_drop_keeps_read_enabled(self, wired_bubble, monkeypatch):
        _actions(wired_bubble, monkeypatch, ["text"])
        wired_bubble._render_buttons()
        assert self._button(wired_bubble, "看一看").isEnabled()

    def test_cancel_enabled_when_all_actions_disabled(self, wired_bubble, monkeypatch):
        _actions(wired_bubble, monkeypatch, ["binary"], read_content=False)
        wired_bubble._render_buttons()
        assert not self._button(wired_bubble, "看一看（内容读取已关闭）").isEnabled()
        assert wired_bubble._cancel_button.isEnabled()

    def test_cancel_closes_without_request(self, wired_bubble, qt_app):
        chosen: list[str] = []
        wired_bubble.action_chosen.connect(lambda action_id, refs: chosen.append(action_id))
        wired_bubble.show_files((_ref("text", "a.txt"),))
        assert wired_bubble.isVisible()

        wired_bubble._cancel_button.click()
        assert _wait_until(qt_app, lambda: not wired_bubble.isVisible())
        assert chosen == []
        assert not wired_bubble._idle_timer.isActive()
