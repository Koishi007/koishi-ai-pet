"""装配层文件动作的冒烟测试。

pet/app.py 只在进程入口被导入，此前没有测试覆盖；这里用桩 dispatcher 触发两条动作分支，
断言后台线程能启动、正文能送达，不构造 QApplication 与 PetWindow。
"""

import threading

from pet.app import _file_meta_lines, _run_tool_file_action, _start_file_action
from pet.file_intake import FileRef
from pet.file_intake.sniff import make_ref
from pet.tools.context import TOOL_CTX
from pet.tools.registry import TOOL_REGISTRY


class _SpeechSpy:
    """替代 TOOL_CTX.speech，记录核心替工具播报的提示。"""

    def __init__(self):
        self.calls = []
        self.done = threading.Event()

    def __call__(self, text, duration=5000):
        self.calls.append(text)
        self.done.set()


def _register_tool(monkeypatch, action_id, handler, accepts="any"):
    monkeypatch.setattr(TOOL_REGISTRY, "file_actions", lambda: [
        {"tool": "probe", "id": action_id, "label": "探测",
         "handler": handler, "accepts": accepts}])


class _Signal:
    """替代 _FileActionDispatcher.ready：记录 emit 参数并放行等待。"""

    def __init__(self):
        self.calls = []
        self.done = threading.Event()

    def emit(self, *args):
        self.calls.append(args)
        self.done.set()


class _Dispatcher:
    def __init__(self):
        self.ready = _Signal()


def _text_ref(tmp_path, body: str = "恋恋的测试文本"):
    path = tmp_path / "note.txt"
    path.write_text(body, encoding="utf-8")
    return make_ref(str(path)), body


def test_core_action_reads_body_in_thread(tmp_path):
    ref, body = _text_ref(tmp_path)
    dispatcher = _Dispatcher()
    _start_file_action(dispatcher, "taste", [ref])
    assert dispatcher.ready.done.wait(5), "后台读取未回调"
    action_id, refs, image, text = dispatcher.ready.calls[0]
    assert action_id == "taste"
    assert list(refs) == [ref]
    assert image is None
    assert body in text


def test_tool_action_calls_handler_in_thread(monkeypatch, tmp_path):
    ref, _ = _text_ref(tmp_path)
    seen = []
    done = threading.Event()

    def handler(files):
        seen.append(files)
        done.set()

    _register_tool(monkeypatch, "x", handler)
    _run_tool_file_action("tool:probe:x", [ref])
    assert done.wait(5), "工具 handler 未被调用"
    assert [r.name for r in seen[0]] == ["note.txt"]


def test_tool_failure_summary_is_spoken(monkeypatch, tmp_path):
    ref, _ = _text_ref(tmp_path)
    spy = _SpeechSpy()
    monkeypatch.setattr(TOOL_CTX, "speech", spy)
    _register_tool(monkeypatch, "x", lambda files: {"ok": False, "summary": "读不出文字"})
    _run_tool_file_action("tool:probe:x", [ref])
    assert spy.done.wait(5), "失败提示未播出"
    assert spy.calls == ["读不出文字"]


def test_tool_success_stays_silent(monkeypatch, tmp_path):
    ref, _ = _text_ref(tmp_path)
    spy = _SpeechSpy()
    ran = threading.Event()
    monkeypatch.setattr(TOOL_CTX, "speech", spy)
    _register_tool(monkeypatch, "x",
                   lambda files: (ran.set(), {"ok": True, "summary": "已收进知识库 1 份"})[1])
    _run_tool_file_action("tool:probe:x", [ref])
    assert ran.wait(5), "工具 handler 未被调用"
    assert spy.calls == []


def test_tool_exception_reports_generic_summary(monkeypatch, tmp_path):
    ref, _ = _text_ref(tmp_path)
    spy = _SpeechSpy()

    def handler(files):
        raise RuntimeError("boom")

    monkeypatch.setattr(TOOL_CTX, "speech", spy)
    _register_tool(monkeypatch, "x", handler)
    _run_tool_file_action("tool:probe:x", [ref])
    assert spy.done.wait(5), "异常未转成提示"
    assert "没能完成" in spy.calls[0]


def test_meta_lines_include_kind_and_size(tmp_path):
    ref, _ = _text_ref(tmp_path)
    assert _file_meta_lines([ref]) == [f"note.txt（文本，{ref.size} 字节）"]


def test_meta_lines_skip_dir_size(tmp_path):
    ref = FileRef(path=str(tmp_path), name="d", suffix="", size=0, kind="dir")
    assert _file_meta_lines([ref]) == ["d（文件夹）"]


def test_missing_tool_action_only_warns(monkeypatch, tmp_path):
    ref, _ = _text_ref(tmp_path)
    monkeypatch.setattr(TOOL_REGISTRY, "file_actions", lambda: [])
    _run_tool_file_action("tool:gone:x", [ref])
