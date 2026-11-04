"""TerminalRunner：注册条件、超时推导、闸门、确认、共享目录与策略快照。"""

import os
import sys

import pytest

from pet.config import config
from pet.terminal import core, policy
from pet.terminal import runner as runner_module
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
                      on_output=None):
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
                      on_output=None):
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
                      on_output=None):
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
                      on_output=None):
            captured["argv"] = list(argv)
            return core.RunResult(exit_code=0, stdout="hi\n")

        monkeypatch.setattr(runner_module.core, "run", _fake_run)
        out = runner.run(command="echo hi", shell=_USER_SHELL, cwd=str(sandbox))
        assert "error" not in out
        assert captured["argv"][-1] == "echo hi"
        assert captured["argv"][0] in ("cmd", "sh", "powershell", "bash")
