"""TerminalRunner：注册条件、超时推导、闸门、确认、共享目录与策略快照。"""

import os
import sys
import threading

import pytest

from pet.config import config
from pet.terminal import core, policy
from pet.terminal import runner as runner_module
from pet.terminal.events import EventKind, Phase
from pet.terminal.runner import TerminalRunner
from pet.tools.registry import ToolRegistry

_USER_SHELL = core.shell_choices()[1]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setattr(config, "TERMINAL_ENABLED", True)
    monkeypatch.setattr(config, "TERMINAL_MODE", "ask")
    monkeypatch.setattr(config, "TERMINAL_ALLOW", [])
    monkeypatch.setattr(config, "TERMINAL_SANDBOX_ROOT", str(root))
    monkeypatch.setattr(config, "TERMINAL_DENY_PATTERNS", [])
    monkeypatch.setattr(config, "TERMINAL_QUEUE_WAIT_S", 10)
    monkeypatch.setattr(config, "TERMINAL_CONFIRM_TIMEOUT_S", 60)
    monkeypatch.setattr(config, "TERMINAL_COMMAND_TIMEOUT_S", 30)
    monkeypatch.setattr(config, "TERMINAL_MAX_COMMAND_TIMEOUT_S", 120)
    monkeypatch.setattr(config, "TERMINAL_ALLOW_SHELL", False)
    return root


class TestRegistration:

    def test_switch_controls_registration(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_ENABLED", False)
        assert ToolRegistry().get_tool("terminal") is None
        monkeypatch.setattr(config, "TERMINAL_ENABLED", True)
        tool = ToolRegistry().get_tool("terminal")
        assert tool is not None
        assert tool.meta is True
        assert tool.side_effect is True
        assert tool.group == "default"


class TestToolContract:

    def test_executor_roundtrip(self, sandbox, monkeypatch):
        from pet.tools.output import validate

        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["echo *"])
        registry = ToolRegistry()
        method = registry.get_method("terminal__run")
        assert method is not None
        assert method.timeout == 200
        out = method.handler(command="echo hi", cwd=str(sandbox))
        assert validate(method.output, out) == []
        assert out["exit_code"] == 0
        assert "hi" in out["body"]

    def test_list_output_matches_schema(self, sandbox):
        from pet.tools.output import validate

        registry = ToolRegistry()
        method = registry.get_method("terminal__list")
        assert method is not None
        assert validate(method.output, method.handler()) == []


class TestTimeout:

    def test_method_timeout_adds_every_stage(self, sandbox):
        runner = TerminalRunner()
        expected = 60 + 120 + 10 + core.KILL_WAIT_S + core.OUTPUT_JOIN_S + 3
        assert runner.method_timeout == expected
        assert runner.method_timeout == 200


class TestGate:

    def test_busy_when_lock_is_held(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_QUEUE_WAIT_S", 0)
        runner = TerminalRunner()
        runner.lock.acquire()
        try:
            out = runner.run_user(command="echo hi", shell=_USER_SHELL)
        finally:
            runner.lock.release()
        assert out["code"] == "busy"


class TestSourceIsolation:

    def test_model_entry_ignores_source_argument(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "ask")
        runner = TerminalRunner()
        out = runner.run(command="echo hi", cwd=str(sandbox), source="user")
        assert out["code"] == "denied_by_user"

    def test_registered_handler_ignores_extra_arguments(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "ask")
        method = ToolRegistry().get_method("terminal__run")
        out = method.handler(command="echo hi", cwd=str(sandbox), source="user")
        assert out["code"] == "denied_by_user"

    def test_user_entry_skips_policy(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "deny")
        runner = TerminalRunner()
        monkeypatch.setattr(runner_module.core, "run",
                            lambda *args, **kwargs: core.RunResult(exit_code=0))
        out = runner.run_user(command="echo hi", cwd=str(sandbox))
        assert "error" not in out


class TestConfirm:

    def test_rejection_skips_execution(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "ask")
        runner = TerminalRunner()
        runner.set_confirm(lambda request: False)
        monkeypatch.setattr(runner_module.core, "run",
                            lambda *args, **kwargs: pytest.fail("命令不应被执行"))
        out = runner.run(command="echo hi", cwd=str(sandbox))
        assert out["code"] == "denied_by_user"

    def test_missing_confirm_function_denies(self, sandbox):
        runner = TerminalRunner()
        out = runner.run(command="echo hi", cwd=str(sandbox))
        assert out["code"] == "denied_by_user"


class TestSharedCwd:

    def test_model_defaults_to_shared_cwd(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "ask")
        runner = TerminalRunner()
        runner.set_confirm(lambda request: True)
        runner.set_cwd_source(lambda: str(sandbox))
        captured = {}

        def _fake_run(argv, cwd="", timeout_s=30, env=None, max_capture_bytes=131072,
                      on_output=None, on_start=None):
            captured["cwd"] = cwd
            return core.RunResult(exit_code=0, stdout="ok\n")

        monkeypatch.setattr(runner_module.core, "run", _fake_run)
        out = runner.run(command="echo hi", cwd="")
        assert out["exit_code"] == 0
        assert os.path.normcase(captured["cwd"]) == os.path.normcase(str(sandbox))

    def test_model_cannot_escape_shared_cwd(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "ask")
        runner = TerminalRunner()
        runner.set_confirm(lambda request: True)
        runner.set_cwd_source(lambda: str(sandbox))
        out = runner.run(command="echo hi", cwd="..")
        assert out["code"] == "cwd_denied"


class TestResult:

    def test_nonzero_exit_is_a_normal_result(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "ask")
        runner = TerminalRunner()
        runner.set_confirm(lambda request: True)
        command = f'"{sys.executable}" -c "import sys; sys.exit(3)"'
        out = runner.run(command=command, cwd=str(sandbox))
        assert "error" not in out
        assert out["exit_code"] == 3
        assert "3" in out["headline"]

    def test_resolved_program_path_is_executed(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["git status"])
        monkeypatch.setattr(runner_module.shutil, "which",
                            lambda name: "/opt/system/git")
        runner = TerminalRunner()
        captured = {}

        def _fake_run(argv, cwd="", timeout_s=30, env=None, max_capture_bytes=131072,
                      on_output=None, on_start=None):
            captured["argv"] = list(argv)
            return core.RunResult(exit_code=0, stdout="")

        monkeypatch.setattr(runner_module.core, "run", _fake_run)
        out = runner.run(command="git status", cwd=str(sandbox))
        assert "error" not in out
        resolved, err = policy.resolve_program("git", str(sandbox))
        assert err == ""
        assert captured["argv"] == [resolved, "status"]

    def test_byte_truncation_reaches_headline(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "ask")
        runner = TerminalRunner()
        runner.set_confirm(lambda request: True)
        monkeypatch.setattr(runner_module.core, "run",
                            lambda *args, **kwargs: core.RunResult(
                                exit_code=0, stdout="x\n", truncated=True))
        out = runner.run(command="echo hi", cwd=str(sandbox))
        assert out["headline"].endswith(runner_module.TRUNCATED_SUFFIX)


class TestList:

    def test_snapshot_shape(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
        monkeypatch.setattr(config, "TERMINAL_ALLOW", ["customtool *", "git status"])
        monkeypatch.setattr(runner_module.shutil, "which",
                            lambda name: "/opt/system/git" if name == "git" else None)
        data = TerminalRunner().list()
        for field in ("headline", "body", "platform", "sandbox_root", "allowed",
                      "interpreters", "deny_categories", "shell_mode", "mode"):
            assert field in data
        assert data["headline"]
        assert data["body"]
        assert data["mode"] == "auto"
        by_name = {item["command"]: item for item in data["allowed"]}
        assert by_name["customtool *"]["installed"] is False
        assert by_name["git status"]["installed"] is True
        assert len(data["allowed"]) == len(policy.allowed_entries())
        assert "删除" in data["deny_categories"]
        assert "invoke-expression" not in data["body"]


class TestShellMode:

    def test_switch_off_rejects_model_shell(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_ALLOW_SHELL", False)
        out = TerminalRunner().run(command="dir", shell=_USER_SHELL)
        assert out["code"] == "shell_mode_disabled"

    def test_user_source_ignores_switch(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_ALLOW_SHELL", False)
        runner = TerminalRunner()
        captured = {}

        def _fake_run(argv, cwd="", timeout_s=30, env=None, max_capture_bytes=131072,
                      on_output=None, on_start=None):
            captured["argv"] = list(argv)
            return core.RunResult(exit_code=0, stdout="hi\n")

        monkeypatch.setattr(runner_module.core, "run", _fake_run)
        out = runner.run_user(command="echo hi", shell=_USER_SHELL)
        assert "error" not in out
        assert captured["argv"][0] in ("cmd", "sh", "powershell", "bash")
        assert captured["argv"][-1] == "echo hi"


    def test_switch_on_routes_model_command(self, sandbox, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_ALLOW_SHELL", True)
        monkeypatch.setattr(config, "TERMINAL_MODE", "ask")
        runner = TerminalRunner()
        runner.set_confirm(lambda request: True)
        captured = {}

        def _fake_run(argv, cwd="", timeout_s=30, env=None, max_capture_bytes=131072,
                      on_output=None, on_start=None):
            captured["argv"] = list(argv)
            return core.RunResult(exit_code=0, stdout="hi\n")

        monkeypatch.setattr(runner_module.core, "run", _fake_run)
        out = runner.run(command="echo hi", shell=_USER_SHELL, cwd=str(sandbox))
        assert "error" not in out
        assert captured["argv"][-1] == "echo hi"
        assert captured["argv"][0] in ("cmd", "sh", "powershell", "bash")


class _EventLog:
    """线程安全的事件记录器，可等待某个序列条件成立。"""

    def __init__(self):
        self._items: list = []
        self._cv = threading.Condition()

    def __call__(self, event) -> None:
        with self._cv:
            self._items.append(event)
            self._cv.notify_all()

    def wait_for(self, predicate, timeout: float = 3.0) -> bool:
        with self._cv:
            return self._cv.wait_for(lambda: predicate(list(self._items)), timeout)

    def snapshot(self) -> list:
        with self._cv:
            return list(self._items)


class TestEventProtocol:
    """P0：每条命令的终态事件在闸门内闭合，且整条链路带同一个 run_id。"""

    @staticmethod
    def _runner_with_log():
        runner = TerminalRunner()
        log = _EventLog()
        runner.set_terminal_sink(log)
        return runner, log

    def test_previous_end_precedes_next_start(self, sandbox, monkeypatch):
        """A 尚未 end 时 B 绝不能 start；B 的 start 必须排在 A 的 end 之后。"""
        runner, log = self._runner_with_log()
        entered = threading.Event()
        release = threading.Event()

        def blocked_run(*args, **kwargs):
            entered.set()
            assert release.wait(3), "A 未被放行"
            return core.RunResult(exit_code=0, stdout="ok\n")

        monkeypatch.setattr(runner_module.core, "run", blocked_run)
        results: list[dict] = []
        first = threading.Thread(
            target=lambda: results.append(runner.run_user(command="echo a")),
            daemon=True)
        first.start()
        assert entered.wait(3)
        assert log.wait_for(
            lambda items: any(p.kind is EventKind.START for p in items))

        second = threading.Thread(
            target=lambda: results.append(runner.run_user(command="echo b")),
            daemon=True)
        second.start()
        assert log.wait_for(
            lambda items: sum(1 for p in items if p.phase is Phase.WAITING) >= 2)
        assert sum(1 for p in log.snapshot() if p.kind is EventKind.START) == 1

        release.set()
        first.join(3)
        second.join(3)
        assert not first.is_alive() and not second.is_alive()
        assert len(results) == 2 and all("error" not in r for r in results)

        items = log.snapshot()
        assert all(p.run_id for p in items)
        a_id = next(p.run_id for p in items if p.kind is EventKind.START)
        b_id = next(p.run_id for p in items
                    if p.kind is EventKind.START and p.run_id != a_id)
        a_end = next(i for i, p in enumerate(items)
                     if p.kind is EventKind.END and p.run_id == a_id)
        b_start = next(i for i, p in enumerate(items)
                       if p.kind is EventKind.START and p.run_id == b_id)
        assert a_end < b_start
        for run_id in (a_id, b_id):
            assert sum(1 for p in items
                       if p.kind is EventKind.END and p.run_id == run_id) == 1
            assert sum(1 for p in items if p.kind is EventKind.PHASE
                       and p.phase is Phase.IDLE and p.run_id == run_id) == 1

    def test_busy_releases_phase(self, sandbox, monkeypatch):
        """busy 路径必须补 idle；模型命令没有窗口侧 _on_run_finished 兜底。"""
        monkeypatch.setattr(config, "TERMINAL_QUEUE_WAIT_S", 0)
        runner, log = self._runner_with_log()
        runner.lock.acquire()
        try:
            out = runner.run_user(command="echo hi")
        finally:
            runner.lock.release()
        assert out["code"] == "busy"
        items = log.snapshot()
        assert [p.phase for p in items if p.kind is EventKind.PHASE] == [
            Phase.WAITING, Phase.IDLE]
        assert all(p.run_id for p in items)
        assert not any(p.kind is EventKind.START for p in items)
        ends = [p for p in items if p.kind is EventKind.END]
        assert len(ends) == 1
        assert ends[0].rejected is True
        assert ends[0].run_id == items[0].run_id

    def test_core_exception_still_closes_block(self, sandbox, monkeypatch):
        """core.run 抛异常时仍要发 end（闭合块）与 idle，并释放闸门。"""
        runner, log = self._runner_with_log()

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(runner_module.core, "run", boom)
        out = runner.run_user(command="echo hi")
        assert out["code"] == "internal_error"
        items = log.snapshot()
        run_id = next(p.run_id for p in items if p.kind is EventKind.START)
        ends = [p for p in items if p.kind is EventKind.END]
        assert len(ends) == 1
        assert ends[0].run_id == run_id
        assert ends[0].rejected is True
        assert "boom" in ends[0].error
        assert items[-1].kind is EventKind.PHASE and items[-1].phase is Phase.IDLE
        assert items[-1].run_id == run_id
        assert runner.lock.acquire(blocking=False)
        runner.lock.release()


class TestRefactorContracts:
    """P1：快照纯数据、根范围收窄、shutdown 注销回调。"""

    def test_snapshot_has_no_presentation_fields(self, sandbox):
        data = TerminalRunner().snapshot()
        assert "headline" not in data and "body" not in data
        assert set(data) == {"platform", "sandbox_root", "allowed",
                             "interpreters", "deny_categories", "shell_mode", "mode"}
        assert "headline" in TerminalRunner().list()

    def test_shared_cwd_outside_root_does_not_expand_root(self, sandbox, tmp_path):
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        runner = TerminalRunner()
        runner.set_cwd_source(lambda: str(outside))
        assert os.path.normcase(runner.snapshot()["sandbox_root"]) == \
               os.path.normcase(str(sandbox))

    def test_shutdown_terminates_active_pid_and_unregisters(self, sandbox, monkeypatch):
        runner = TerminalRunner()
        sink = lambda event: None
        runner.set_terminal_sink(sink)
        runner.set_cwd_source(lambda: str(sandbox))
        runner.set_confirm(lambda request: True)
        runner._track_pid(4242)
        calls = []
        monkeypatch.setattr(runner_module.core, "terminate_tree",
                            lambda pid: (calls.append(pid) or (True, "")))
        assert runner.shutdown() == 1
        assert calls == [4242]
        assert runner._terminal_sink is None
        assert runner._cwd_source is None
        assert runner._confirm is None
        assert runner._active_pid is None
