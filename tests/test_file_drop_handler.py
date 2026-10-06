"""拖放判定层的单元测试。

只构造 QMimeData 与临时文件，不构造 PetWindow；三个出口用记录列表断言调用序列。
"""

from pathlib import Path

from PySide6.QtCore import QMimeData, QUrl

from pet.tools.registry import ToolRegistry
from pet.ui.file_drop_handler import FileDropHandler, busy_event_text, paths_from_mime


def _mime(paths=None, text=None) -> QMimeData:
    mime = QMimeData()
    if paths:
        mime.setUrls([QUrl.fromLocalFile(path) for path in paths])
    if text is not None:
        mime.setText(text)
    return mime


def _file(tmp_path: Path, name: str, data: bytes = b"hi") -> str:
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def make_handler(*, enabled: bool = True, penetration: bool = False, busy: bool = False):
    """返回（handler, calls）；calls 记录各出口的调用参数。"""
    calls = {"bubble": [], "reject": [], "busy": [], "busy_show": []}
    handler = FileDropHandler(
        hover_state=lambda: (enabled, penetration),
        is_busy=lambda: busy,
        on_show_bubble=calls["bubble"].append,
        on_reject=lambda status, names: calls["reject"].append((status, names)),
        on_busy_event=calls["busy"].append,
        on_show_busy=calls["busy_show"].append,
    )
    return handler, calls


class TestPathsFromMime:
    def test_local_files(self, tmp_path):
        first = _file(tmp_path, "a.txt")
        second = _file(tmp_path, "b.md")
        assert paths_from_mime(_mime([first, second])) == [first, second]

    def test_empty_mime(self):
        assert paths_from_mime(_mime()) == []

    def test_text_only(self):
        assert paths_from_mime(_mime(text="hello")) == []

    def test_remote_url_ignored(self):
        mime = QMimeData()
        mime.setUrls([QUrl("https://example.com/a.txt")])
        assert paths_from_mime(mime) == []

    def test_none(self):
        assert paths_from_mime(None) == []


class TestHoverLayer:
    def test_accepts_plain_local_paths(self):
        handler, _ = make_handler()
        assert handler.accept_hover(True) is True

    def test_rejects_without_local_paths(self):
        handler, _ = make_handler()
        assert handler.accept_hover(False) is False

    def test_rejects_when_disabled(self):
        handler, _ = make_handler(enabled=False)
        assert handler.accept_hover(True) is False

    def test_rejects_when_penetration_on(self):
        handler, _ = make_handler(penetration=True)
        assert handler.accept_hover(True) is False


class TestDropLayer:
    def test_too_many(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pet.config.config.FILE_DROP_MAX_FILES", 2)
        handler, calls = make_handler()
        handler.handle_drop([_file(tmp_path, f"f{i}.txt") for i in range(3)])
        assert calls["reject"] == [("too_many", ("f0.txt", "f1.txt", "f2.txt"))]
        assert calls["bubble"] == []
        assert calls["busy"] == []

    def test_too_large(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pet.config.config.FILE_DROP_MAX_FILE_MB", 0)
        handler, calls = make_handler()
        handler.handle_drop([_file(tmp_path, "big.txt", b"x" * 2048)])
        assert calls["reject"] == [("too_large", ("big.txt",))]
        assert calls["bubble"] == []

    def test_forbidden(self, tmp_path):
        handler, calls = make_handler()
        handler.handle_drop([_file(tmp_path, ".env", b"A=1")])
        assert calls["reject"] == [("forbidden", (".env",))]
        assert calls["bubble"] == []

    def test_ok_shows_bubble_once(self, tmp_path):
        handler, calls = make_handler()
        handler.handle_drop([_file(tmp_path, "note.md")])
        assert calls["reject"] == []
        assert len(calls["bubble"]) == 1
        refs = calls["bubble"][0]
        assert [ref.name for ref in refs] == ["note.md"]

    def test_busy_writes_event_and_shows_hint(self, tmp_path):
        path = _file(tmp_path, "note.md")
        handler, calls = make_handler(busy=True)
        handler.handle_drop([path])
        assert calls["reject"] == []
        assert calls["bubble"] == []
        assert len(calls["busy"]) == 1
        assert "note.md" in calls["busy"][0]
        assert calls["busy_show"] == [[path]]

    def test_empty_paths_are_ignored(self):
        handler, calls = make_handler(busy=True)
        handler.handle_drop([])
        assert calls["busy"] == []


class TestFileActions:
    """工具文件动作的注册与查询：只测注册表，不加载具体工具。"""

    def test_empty_by_default(self):
        registry = ToolRegistry()
        assert registry.file_actions() == []

    def test_registered_action_is_listed(self):
        registry = ToolRegistry()
        registry.register("demo", "示例工具")
        handler = lambda files: {"ok": True, "summary": ""}  # noqa: E731
        registry.add_file_action("demo", "ingest", "收进去", handler, accepts="text")

        actions = registry.file_actions()
        assert len(actions) == 1
        assert actions[0]["tool"] == "demo"
        assert actions[0]["id"] == "ingest"
        assert actions[0]["label"] == "收进去"
        assert actions[0]["accepts"] == "text"
        assert actions[0]["handler"] is handler

    def test_returns_plain_dicts(self):
        registry = ToolRegistry()
        registry.register("demo", "示例工具")
        registry.add_file_action("demo", "ingest", "收进去", lambda files: {})
        assert set(registry.file_actions()[0]) == {"tool", "id", "label", "handler",
                                                   "accepts", "needs_content"}

    def test_disabled_tool_hides_action(self):
        registry = ToolRegistry()
        registry.register("demo", "示例工具")
        registry.add_file_action("demo", "ingest", "收进去", lambda files: {})
        registry.set_enabled("demo", False)
        assert registry.file_actions() == []

    def test_default_accepts_is_any(self):
        registry = ToolRegistry()
        registry.register("demo", "示例工具")
        registry.add_file_action("demo", "ingest", "收进去", lambda files: {})
        assert registry.file_actions()[0]["accepts"] == "any"

    def test_needs_content_defaults_true_and_passes_through(self):
        registry = ToolRegistry()
        registry.register("demo", "示例工具")
        registry.add_file_action("demo", "ingest", "收进去", lambda files: {})
        registry.add_file_action("demo", "todo", "记进待办", lambda files: {},
                                 needs_content=False)
        by_id = {action["id"]: action for action in registry.file_actions()}
        assert by_id["ingest"]["needs_content"] is True
        assert by_id["todo"]["needs_content"] is False


class TestBusyText:
    def test_lists_names(self):
        text = busy_event_text(["C:/x/报告.md", "D:/y/图.png"])
        assert "报告.md" in text
        assert "图.png" in text

    def test_names_are_capped(self):
        text = busy_event_text([f"C:/x/f{i}.txt" for i in range(6)])
        assert "f0.txt" in text
        assert "f3.txt" not in text
