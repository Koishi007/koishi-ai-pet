"""知识库文件动作：入库与播报 hint 的内容，以及播报请求的线程转发。

用桩 storage 与桩播报，验证只把成功入库的文件名写进 hint，读不出内容的文件不出现；
播报请求经 tool_interact_requested 信号转回主线程。
"""

import pytest
from PySide6.QtCore import QCoreApplication, QObject, Signal

from pet.file_intake import FileRef
from pet.tools import knowledge
from pet.tools.context import TOOL_CTX


@pytest.fixture(scope="module")
def qcore_app():
    return QCoreApplication.instance() or QCoreApplication([])


class _StorageStub:
    def __init__(self):
        self.titles = []

    def add_document(self, title: str, content: str, tags: str = "", source: str = "") -> dict:
        self.titles.append(title)
        return {"id": len(self.titles), "title": title, "chunks": 1, "source": source}


class _InteractSpy:
    def __init__(self):
        self.hints = []
        self.kwargs = []

    def __call__(self, hint, **kwargs):
        self.hints.append(hint)
        self.kwargs.append(kwargs)


def _ref(path, name: str) -> FileRef:
    return FileRef(path=str(path), name=name, suffix="", size=1, kind="text")


def _setup(monkeypatch) -> tuple[_StorageStub, _InteractSpy]:
    storage = _StorageStub()
    spy = _InteractSpy()
    monkeypatch.setattr(knowledge, "_instance", storage)
    monkeypatch.setattr(TOOL_CTX, "request_interact", spy)
    return storage, spy


def test_hint_lists_ingested_names(monkeypatch, tmp_path):
    storage, spy = _setup(monkeypatch)
    ok = tmp_path / "笔记.md"
    ok.write_text("有内容", encoding="utf-8")
    blank = tmp_path / "空.txt"
    blank.write_text("   ", encoding="utf-8")

    result = knowledge._ingest_files([_ref(ok, "笔记.md"), _ref(blank, "空.txt")])

    assert storage.titles == ["笔记.md"]
    assert result == {"ok": True, "summary": "已收进知识库 1 份"}
    assert "笔记.md" in spy.hints[0]
    assert "空.txt" not in spy.hints[0]
    assert spy.kwargs[0]["cooldown_ms"] == 0


def test_no_readable_content_skips_interact(monkeypatch, tmp_path):
    storage, spy = _setup(monkeypatch)
    blank = tmp_path / "空.txt"
    blank.write_text("   ", encoding="utf-8")

    result = knowledge._ingest_files([_ref(blank, "空.txt")])

    assert storage.titles == []
    assert spy.hints == []
    assert result["ok"] is False
    assert "没有能读出来的文字" in result["summary"]


def test_hint_names_are_capped(monkeypatch, tmp_path):
    storage, spy = _setup(monkeypatch)
    refs = []
    for index in range(6):
        path = tmp_path / f"f{index}.txt"
        path.write_text("内容", encoding="utf-8")
        refs.append(_ref(path, f"f{index}.txt"))

    result = knowledge._ingest_files(refs)

    assert storage.titles == [f"f{index}.txt" for index in range(6)]
    assert result["summary"] == "已收进知识库 6 份"
    assert "f4.txt" in spy.hints[0]
    assert "f5.txt" not in spy.hints[0]


class _AgentSignalStub(QObject):
    tool_interact_requested = Signal(dict)

    def __init__(self):
        super().__init__()
        self.emitted: list[dict] = []
        self.tool_interact_requested.connect(self.emitted.append)


def test_request_interact_marshals_via_signal(qcore_app, monkeypatch):
    agent = _AgentSignalStub()
    monkeypatch.setattr(TOOL_CTX, "_agent", agent)

    TOOL_CTX.request_interact("用户把「笔记.md」交给你收进知识库了", cooldown_ms=0)

    assert agent.emitted == [{
        "hint": "用户把「笔记.md」交给你收进知识库了",
        "delay_ms": 100,
        "cooldown_ms": 0,
        "thinking": None,
        "enable_tools": None,
    }]
