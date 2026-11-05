"""终端窗口：来源标记、缓冲上限、命令历史、多行粘贴、执行入口与审计回填。"""

import os
import time
from collections import deque

import pytest
from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pet.cli import catalog
from pet.tools import knowledge, timer, todo
from pet.tools.registry import TOOL_REGISTRY, ToolCommand
from pet.ui import terminal_window as tw
from pet.ui.styles import (TERMINAL_ACCENT, TERMINAL_DANGER, TERMINAL_IDLE,
                           TERMINAL_SOURCE_LABEL_MODEL, TERMINAL_SOURCE_LABEL_USER,
                           TERMINAL_USER, terminal_source_color, terminal_source_label)


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


class _StubRunner:
    """记录调用并回显一条结果，不真的执行命令。"""

    def __init__(self):
        self.calls: list[dict] = []
        self.sink = None
        self._cwd_source = None

    def set_terminal_sink(self, sink):
        self.sink = sink

    def set_cwd_source(self, provider):
        self._cwd_source = provider

    def shared_cwd(self):
        return str(self._cwd_source() or "") if self._cwd_source else ""

    def run(self, command, cwd="", timeout=0, shell="", **_ignored):
        return self._run(command, cwd, timeout, shell, "model")

    def run_user(self, command, cwd="", timeout=0, shell=""):
        return self._run(command, cwd, timeout, shell, "user")

    def _run(self, command, cwd, timeout, shell, source):
        self.calls.append({"command": command, "cwd": cwd, "timeout": timeout,
                           "shell": shell, "source": source})
        if self.sink is not None:
            self.sink({"source": source, "command": command, "argv": [command],
                       "cwd": cwd, "rejected": False, "exit_code": 0,
                       "stdout": "ok\n", "stderr": "", "duration_ms": 5,
                       "error": ""})
        return {"headline": "ok", "body": "ok", "exit_code": 0}


@pytest.fixture
def runner():
    return _StubRunner()


@pytest.fixture
def window(qt_app, tmp_path, runner, monkeypatch):
    monkeypatch.setattr(tw.policy, "sandbox_root", lambda: str(tmp_path))
    win = tw.TerminalWindow(runner=runner)
    yield win
    win.close()


class TestSourceMarkers:

    def test_labels_and_colors(self):
        assert terminal_source_label("user") == TERMINAL_SOURCE_LABEL_USER
        assert terminal_source_label("model") == TERMINAL_SOURCE_LABEL_MODEL
        assert terminal_source_label("user") != terminal_source_label("model")
        assert terminal_source_color("user") != terminal_source_color("model")

    def test_block_shows_source_marker(self, window):
        window._append_run({"source": "model", "command": "git status",
                            "argv": ["git", "status"], "cwd": "", "rejected": False,
                            "exit_code": 0, "stdout": "clean\n", "stderr": "",
                            "duration_ms": 12, "error": ""})
        text = window._view.toPlainText()
        assert f"{TERMINAL_SOURCE_LABEL_MODEL} git status" in text
        assert "exit 0" in text
        assert "clean" in text

    def test_cwd_line_only_for_model_outside_sandbox(self, window, tmp_path, monkeypatch):
        monkeypatch.setattr(tw.policy, "sandbox_root", lambda: str(tmp_path))
        window._append_run({"source": "model", "command": "pwd", "cwd": str(tmp_path),
                            "rejected": False, "exit_code": 0, "stdout": "",
                            "stderr": "", "duration_ms": 1, "error": ""})
        assert "cwd:" not in window._view.toPlainText()
        window._append_run({"source": "model", "command": "pwd",
                            "cwd": str(tmp_path / "sub"), "rejected": False,
                            "exit_code": 0, "stdout": "", "stderr": "",
                            "duration_ms": 1, "error": ""})
        assert "cwd:" in window._view.toPlainText()

    def test_user_command_has_no_cwd_line(self, window, tmp_path):
        window._append_run({"source": "user", "command": "pwd", "cwd": str(tmp_path),
                            "rejected": False, "exit_code": 0, "stdout": "",
                            "stderr": "", "duration_ms": 1, "error": ""})
        assert "cwd:" not in window._view.toPlainText()

    def test_rejected_block_is_marked(self, window):
        window._append_run({"source": "model", "command": "rm -rf /",
                            "argv": ["rm", "-rf", "/"], "cwd": "", "rejected": True,
                            "exit_code": None, "stdout": "", "stderr": "",
                            "duration_ms": 0, "error": "denied_by_user"})
        assert tw.REJECTED_TEXT in window._view.toPlainText()

    def test_streaming_appends_one_block(self, window):
        window._append_run({"kind": "start", "source": "model",
                            "command": "ping", "argv": ["ping"], "cwd": ""})
        window._append_run({"kind": "chunk", "stream": "stdout", "text": "line1\nline"})
        window._append_run({"kind": "chunk", "stream": "stdout", "text": "2\n"})
        window._append_run({"kind": "chunk", "stream": "stderr", "text": "warn\n"})
        window._append_run({"kind": "end", "source": "model", "command": "ping",
                            "argv": ["ping"], "cwd": "", "rejected": False,
                            "exit_code": 0, "stdout": "", "stderr": "",
                            "duration_ms": 8, "error": ""})
        text = window._view.toPlainText()
        assert text.count(TERMINAL_SOURCE_LABEL_MODEL) == 1
        assert "line1" in text and "line2" in text
        assert "warn" in text
        assert "exit 0" in text

    def test_phase_updates_status_dot(self, window):
        window._append_run({"kind": "phase", "phase": "waiting"})
        assert TERMINAL_USER in window._dot.styleSheet()
        window._append_run({"kind": "phase", "phase": "running"})
        assert TERMINAL_ACCENT in window._dot.styleSheet()
        window._append_run({"kind": "phase", "phase": "error"})
        assert TERMINAL_DANGER in window._dot.styleSheet()
        window._append_run({"kind": "phase", "phase": "idle"})
        assert TERMINAL_IDLE in window._dot.styleSheet()


class TestBuffer:

    def test_block_limit(self, window):
        cursor = QTextCursor(window._view.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for index in range(5050):
            cursor.insertText(f"line {index}\n")
        window._trim_blocks()
        assert window._view.document().blockCount() <= tw._MAX_BLOCKS


class TestCompletionKeys:

    def test_tab_completes_without_moving_focus(self, window, tmp_path):
        (tmp_path / "zzq_only.txt").write_text("", encoding="utf-8")
        assert window._change_dir(str(tmp_path)) is True
        window._input.setText("zzq")
        QTest.keyClick(window._input, Qt.Key.Key_Tab)
        assert "zzq_only.txt" in window._input.text()

    def test_tab_cycles_candidates(self, window, tmp_path):
        (tmp_path / "zzq_a.txt").write_text("", encoding="utf-8")
        (tmp_path / "zzq_b.txt").write_text("", encoding="utf-8")
        assert window._change_dir(str(tmp_path)) is True
        window._input.setText("zzq")
        window._on_tab(False)
        assert window._input.text() == "zzq_a.txt"
        window._on_tab(False)
        assert window._input.text() == "zzq_b.txt"
        window._on_tab(True)
        assert window._input.text() == "zzq_a.txt"

    def test_directory_keeps_separator_without_space(self, window, tmp_path):
        (tmp_path / "zzq_dir").mkdir()
        assert window._change_dir(str(tmp_path)) is True
        window._input.setText("zzq")
        window._on_tab()
        assert window._input.text() == "zzq_dir" + os.sep

    def test_file_completes_with_space(self, window, tmp_path):
        (tmp_path / "zzq_file.txt").write_text("", encoding="utf-8")
        assert window._change_dir(str(tmp_path)) is True
        window._input.setText("zzq")
        window._on_tab()
        assert window._input.text() == "zzq_file.txt "


class TestReopen:

    def test_hide_and_show_keeps_output(self, window):
        window._append_run({"source": "user", "command": "echo kept",
                            "cwd": "", "rejected": False, "exit_code": 0,
                            "stdout": "kept\n", "stderr": "", "duration_ms": 1,
                            "error": ""})
        window.hide()
        window.show()
        assert "kept" in window._view.toPlainText()


class TestHistory:

    def test_consecutive_duplicates_are_dropped(self):
        history = deque(maxlen=5)
        tw.push_history(history, "a")
        tw.push_history(history, "a")
        tw.push_history(history, "b")
        assert list(history) == ["a", "b"]

    def test_arrow_keys_walk_history(self, window):
        window._history.extend(["a", "b"])
        window._history_prev()
        assert window._input.text() == "b"
        window._history_prev()
        assert window._input.text() == "a"
        window._history_next()
        assert window._input.text() == "b"
        window._history_next()
        assert window._input.text() == ""

    def test_capacity(self):
        history = deque(maxlen=2)
        for item in ("a", "b", "c"):
            tw.push_history(history, item)
        assert list(history) == ["b", "c"]


class TestPaste:

    def test_multiline_paste_keeps_first_line(self, window):
        mime = QMimeData()
        mime.setText("echo one\necho two")
        window._input.insertFromMimeData(mime)
        assert window._input.text() == "echo one"
        assert tw.PASTE_TRUNCATED_NOTICE in window._status.text()


class TestSubmit:

    def _wait(self, qt_app, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            qt_app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def test_user_command_skips_confirm_and_uses_shell(self, qt_app, window, runner):
        window._input.setText("echo hi")
        window._submit()
        assert self._wait(qt_app, lambda: bool(runner.calls))
        call = runner.calls[0]
        assert call["source"] == "user"
        assert call["timeout"] == 0
        assert call["shell"]
        assert self._wait(qt_app, lambda: not window._busy)

    def test_cd_is_intercepted(self, qt_app, window, runner, tmp_path):
        target = tmp_path / "sub"
        target.mkdir()
        window._input.setText(f"cd {target}")
        window._submit()
        assert runner.calls == []
        assert window._cwd == str(target)
        assert window._view.toPlainText() == ""

    def test_cd_with_many_args_is_not_intercepted(self, window):
        assert tw.cd_target("cd a b") is None
        assert tw.cd_target("cd") is None
        assert tw.cd_target("dir") is None
        assert tw.cd_target("cd docs") == "docs"

    def test_drive_target_only_matches_bare_drive(self):
        if os.name != "nt":
            assert tw.drive_target("D:") is None
            return
        assert tw.drive_target("D:") == "D:" + os.sep
        assert tw.drive_target("c:") == "C:" + os.sep
        assert tw.drive_target("cd D:") is None
        assert tw.drive_target("D:/x") is None

    @pytest.mark.skipif(os.name != "nt", reason="盘符只在 Windows 生效")
    def test_submit_bare_drive_switches_to_root(self, window, tmp_path):
        drive = os.path.splitdrive(str(tmp_path))[0]
        assert window._change_dir(str(tmp_path)) is True
        window._input.setText(drive)
        window._submit()
        assert os.path.normcase(window._cwd) == os.path.normcase(drive + os.sep)

    @pytest.mark.skipif(os.name != "nt", reason="盘符只在 Windows 生效")
    def test_cd_bare_drive_switches_to_root(self, window, tmp_path):
        drive = os.path.splitdrive(str(tmp_path))[0]
        assert window._change_dir(str(tmp_path)) is True
        window._input.setText("cd " + drive)
        window._submit()
        assert os.path.normcase(window._cwd) == os.path.normcase(drive + os.sep)


class TestCwd:

    def test_initial_cwd_is_work_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tw.policy, "sandbox_root", lambda: str(tmp_path))
        assert tw.resolve_initial_cwd() == str(tmp_path)

    def test_model_reads_terminal_cwd(self, window, tmp_path):
        target = tmp_path / "sub"
        target.mkdir()
        assert window._change_dir(str(target)) is True
        assert window._runner.shared_cwd() == str(target)

    def test_cwd_prompt_is_inside_input(self, window):
        assert window._input.prompt_text() == tw.cwd_prompt(window._cwd)
        assert window._input.prompt_text().endswith(">")
        assert window._input.textMargins().left() > 0

    def test_prompt_has_no_gap_before_text(self, window):
        metrics = window._input.fontMetrics()
        prompt_end = (window._input.contentsRect().left() + tw._PROMPT_INSET
                      + metrics.horizontalAdvance(window._input.prompt_text()))
        assert window._input.cursorRect().left() - prompt_end <= 2

    def test_cd_updates_prompt(self, window, tmp_path):
        target = tmp_path / "sub"
        target.mkdir()
        assert window._change_dir(str(target)) is True
        assert window._input.prompt_text() == tw.cwd_prompt(str(target))

    def test_elide_keeps_head_and_tail(self):
        text = "C:" + os.sep + ("a" * 60)
        elided = tw.elide_cwd(text)
        assert len(elided) <= tw._CWD_LABEL_MAX
        assert "..." in elided


class TestClear:

    def test_clear_empties_view(self, window):
        window._append_run({"source": "user", "command": "echo hi", "cwd": "",
                            "rejected": False, "exit_code": 0, "stdout": "hi\n",
                            "stderr": "", "duration_ms": 1, "error": ""})
        window.clear()
        assert window._view.toPlainText() == ""


def _wait_idle(window, timeout_ms: int = 3000) -> None:
    """等本地命令线程回主线程后结束忙碌态。"""
    waited = 0
    while window._busy and waited < timeout_ms:
        QTest.qWait(20)
        waited += 20


class _StubRegistry:
    """补全只读注册表的 commands()；工具命令由各工具自己登记。"""

    def __init__(self, commands):
        self._commands = commands

    def commands(self):
        return list(self._commands)


@pytest.fixture
def registered_tools(monkeypatch):
    """挂上工具登记的终端命令，供补全用例使用。"""
    commands = []

    class _Recorder:
        def add_command(self, tool_name, **kwargs):
            commands.append((tool_name, ToolCommand(**kwargs)))

    for module in (timer, todo, knowledge):
        module.register_commands(_Recorder())
    commands += [item for item in TOOL_REGISTRY.commands() if item[1].name == "memory"]
    monkeypatch.setattr(catalog, "TOOL_REGISTRY", _StubRegistry(commands))
    return commands


class TestLocalCommand:

    def test_local_command_skips_runner(self, window, runner):
        window._input.setText(":help")
        window._submit()
        _wait_idle(window)
        assert runner.calls == []
        text = window._view.toPlainText()
        assert ":help" in text
        assert "exit 0" in text
        assert "本地 ·" not in text
        assert window._busy is False

    def test_unknown_command_skips_runner(self, window, runner):
        window._input.setText(":nope")
        window._submit()
        _wait_idle(window)
        assert runner.calls == []
        assert "exit 2" in window._view.toPlainText()

    def test_clear_command_empties_view(self, window):
        window._append_run({"source": "user", "command": "echo hi", "cwd": "",
                            "rejected": False, "exit_code": 0, "stdout": "hi\n",
                            "stderr": "", "duration_ms": 1, "error": ""})
        window._input.setText(":clear")
        window._submit()
        _wait_idle(window)
        assert window._view.toPlainText() == ""

    def test_shell_command_still_reaches_runner(self, window, runner):
        window._input.setText("dir")
        window._submit()
        _wait_idle(window)
        assert [call["command"] for call in runner.calls] == ["dir"]

    def test_tab_completes_command_name(self, window):
        window._input.setText(":he")
        window._input.setCursorPosition(3)
        window._on_tab()
        assert window._input.text() == ":help "

    def test_tab_cycles_between_commands(self, window, registered_tools):
        window._input.setText(":t")
        window._input.setCursorPosition(2)
        window._on_tab()
        first = window._input.text()
        window._on_tab()
        assert first != window._input.text()
        assert window._input.text().startswith(":t")

    def test_tab_completes_subcommand(self, window, registered_tools):
        window._input.setText(":memory sea")
        window._input.setCursorPosition(len(window._input.text()))
        window._on_tab()
        assert window._input.text() == ":memory search "

    def test_tab_leaves_unknown_argument_untouched(self, window):
        window._input.setText(":help zzz")
        window._input.setCursorPosition(len(window._input.text()))
        window._on_tab()
        assert window._input.text() == ":help zzz"
