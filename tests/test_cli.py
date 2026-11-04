"""终端本地命令：分发、帮助排版、策略试算与工具命令的通用调用。

内建命令直接测；工具命令用桩注册表挂上工具自己登记的条目，验证 cli 只做分发，
参数翻译与工具名由工具决定。
"""

from __future__ import annotations

import pytest

from pet import cli as tc
from pet.cli import catalog
from pet.config import config
from pet.terminal import policy
from pet.tools import knowledge, timer, todo
from pet.tools.executor import ToolResult
from pet.tools.registry import TOOL_REGISTRY, ToolCommand


class _StubRegistry:
    """只提供 cli 需要的两个查询：终端命令与工具方法是否存在。"""

    def __init__(self, commands=(), methods=()):
        self._commands = list(commands)
        self._methods = set(methods)

    def commands(self):
        return list(self._commands)

    def get_method(self, full_name):
        return object() if full_name in self._methods else None


class _StubRunner:
    """策略快照桩：不探测安装情况，也不碰真实目录。"""

    def list(self) -> dict:
        return {
            "headline": "当前档位 auto，免确认命令 1 条",
            "platform": "Windows",
            "sandbox_root": "/sandbox",
            "allowed": [{"command": "git status", "installed": True},
                        {"command": "mytool *", "installed": False}],
            "interpreters": list(policy.INTERPRETERS),
            "deny_categories": ["删除", "关机"],
            "shell_mode": False,
            "mode": "auto",
        }


def _collect(module) -> list[tuple[str, ToolCommand]]:
    """借工具自己的 register_commands 拿到它登记的条目。"""
    commands: list[tuple[str, ToolCommand]] = []

    class _Recorder:
        def add_command(self, tool_name, **kwargs):
            commands.append((tool_name, ToolCommand(**kwargs)))

    module.register_commands(_Recorder())
    return commands


_METHODS = ("timer__set", "timer__list", "timer__cancel", "todo__add",
            "todo__list", "knowledge__search", "recall__search")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离配置与程序解析：git 视为系统已装，沙箱根指向临时目录。"""
    monkeypatch.setattr(config, "TERMINAL_MODE", "auto")
    monkeypatch.setattr(config, "TERMINAL_ALLOW", ["git status"])
    monkeypatch.setattr(config, "TERMINAL_SANDBOX_ROOT", str(tmp_path))
    monkeypatch.setattr(config, "TERMINAL_DENY_PATTERNS", [])
    monkeypatch.setattr(policy.shutil, "which", lambda name: f"/opt/system/{name}")
    return tmp_path


@pytest.fixture
def tool_commands(monkeypatch):
    """把三个工具登记的终端命令与真实的 recall 命令挂到桩注册表上。"""
    commands = _collect(timer) + _collect(todo) + _collect(knowledge)
    commands += [item for item in TOOL_REGISTRY.commands() if item[1].name == "memory"]
    stub = _StubRegistry(commands, _METHODS)
    monkeypatch.setattr(catalog, "TOOL_REGISTRY", stub)
    return stub


@pytest.fixture
def tool_calls(monkeypatch):
    """记录经通用执行器发出的 tool__method 调用。"""
    calls = []

    def fake_execute(self, call):
        calls.append(call)
        return ToolResult(name=call.name, output={"headline": "ok", "body": "细节"})

    monkeypatch.setattr("pet.tools.executor.ToolExecutor.execute_one", fake_execute)
    return calls


class TestPrefix:

    def test_colon_prefix_is_local(self):
        assert tc.is_local(":help")
        assert tc.is_local("   :status")
        assert not tc.is_local("dir")
        assert not tc.is_local("")

    def test_help_lists_builtin_rows(self):
        result = tc.run(":help", "", None)
        assert result.exit_code == tc.EXIT_OK
        for command in tc.commands():
            for usage, _summary in command.rows:
                assert usage in result.stdout


class TestHelpLayout:

    def test_rows_align_descriptions(self, tool_commands):
        text = tc.run(":help", "", None).stdout
        columns = set()
        for command in tc.commands():
            for usage, summary in command.rows:
                line = next(item for item in text.splitlines()
                            if item.startswith(usage + " "))
                columns.add(line.index(summary))
        assert len(columns) == 1

    def test_usage_section_follows_group_order(self, tool_commands):
        text = tc.run(":help", "", None).stdout
        usage = text.split("用法\n", 1)[1].split("\n\n命令", 1)[0].splitlines()
        assert usage.index(":todo list [status]") < usage.index(":memory search <keyword>")

    def test_help_prints_groups_in_order(self, tool_commands):
        text = tc.run(":help", "", None).stdout
        present = [group for group in tc.GROUP_ORDER if group in text]
        positions = [text.index(group) for group in present]
        assert positions == sorted(positions)
        assert {"通用", "状态与策略", "计时与待办", "检索"} <= set(present)

    def test_tool_rows_appear_after_registration(self, tool_commands):
        text = tc.run(":help", "", None).stdout
        assert ":timer list" in text
        assert ":knowledge search <keyword>" in text

    def test_tool_rows_absent_without_registration(self):
        text = tc.run(":help", "", None).stdout
        assert ":timer list" not in text

    def test_examples_come_from_registered_commands(self, tool_commands):
        text = tc.run(":help", "", None).stdout
        examples = [example for command in tc.commands() for example in command.examples]
        assert examples
        for example in examples:
            assert example in text

    def test_examples_vanish_with_the_command(self):
        text = tc.run(":help", "", None).stdout
        assert ":timer 10m 喝水" not in text
        assert ":check python script.py" in text


class TestDispatch:

    def test_unknown_command(self):
        result = tc.run(":nope", "", None)
        assert result.exit_code == tc.EXIT_USAGE
        assert result.stderr

    def test_empty_command(self):
        assert tc.run(":", "", None).exit_code == tc.EXIT_USAGE

    def test_name_is_case_insensitive(self):
        assert tc.run(":HELP", "", None).exit_code == tc.EXIT_OK

    def test_clear_sets_flag(self):
        result = tc.run(":clear", "", None)
        assert result.clear is True
        assert result.stdout == ""

    def test_tool_command_needs_registration(self):
        assert tc.run(":timer 10m", "", None).exit_code == tc.EXIT_USAGE


class TestPluggable:

    def test_new_tool_command_needs_no_cli_change(self, env, monkeypatch, tool_calls):
        """任何工具登记的命令都能被分发、进帮助，不需要改 cli。"""
        demo = ToolCommand(name="demo", group="实验",
                           rows=((":demo run", "演示命令"),),
                           resolve=lambda arg: ("run", {}))
        monkeypatch.setattr(catalog, "TOOL_REGISTRY", _StubRegistry([("demo", demo)],
                                                              methods=("demo__run",)))

        result = tc.run(":demo", str(env), _StubRunner())

        assert result.exit_code == tc.EXIT_OK
        assert [call.name for call in tool_calls] == ["demo__run"]
        text = tc.run(":help", "", None).stdout
        assert ":demo run" in text
        assert text.index("通用") < text.index("实验")


class TestCheck:

    @pytest.mark.parametrize("command,decision", [
        ("git status", "allow"),
        ("git push", "ask"),
        ("python script.py", "ask"),
        ("rm -rf /", "ask"),
    ])
    def test_decision_follows_policy(self, env, command, decision):
        result = tc.run(f":check {command}", str(env), None)
        assert f"（{decision}）" in result.stdout

    def test_deny_mode(self, env, monkeypatch):
        monkeypatch.setattr(config, "TERMINAL_MODE", "deny")
        result = tc.run(":check git status", str(env), None)
        assert "（deny）" in result.stdout

    def test_unparsable_command(self, env):
        result = tc.run(':check echo "unclosed', str(env), None)
        assert result.exit_code == tc.EXIT_USAGE

    def test_missing_argument(self, env):
        assert tc.run(":check", str(env), None).exit_code == tc.EXIT_USAGE


class TestSnapshot:

    def test_status_reports_snapshot(self, env):
        result = tc.run(":status", str(env), _StubRunner())
        assert result.exit_code == tc.EXIT_OK
        assert "auto" in result.stdout
        assert str(env) in result.stdout

    def test_policy_lists_entries(self, env):
        result = tc.run(":policy", str(env), _StubRunner())
        assert "git status" in result.stdout
        assert "mytool *" in result.stdout


class TestToolRouting:

    def test_timer_set_uses_generic_call(self, env, tool_commands, tool_calls):
        result = tc.run(":timer 10m 泡面", str(env), _StubRunner())
        assert result.exit_code == tc.EXIT_OK
        assert [call.name for call in tool_calls] == ["timer__set"]
        assert tool_calls[0].args == {"duration": 600, "label": "泡面"}

    def test_timer_list_and_cancel(self, env, tool_commands, tool_calls):
        tc.run(":timer list", str(env), _StubRunner())
        tc.run(":timer cancel abc123", str(env), _StubRunner())
        assert [call.name for call in tool_calls] == ["timer__list", "timer__cancel"]
        assert tool_calls[1].args == {"timer_id": "abc123"}

    def test_timer_bad_duration(self, env, tool_commands, tool_calls):
        result = tc.run(":timer 明天", str(env), _StubRunner())
        assert result.exit_code == tc.EXIT_USAGE
        assert tool_calls == []
        assert "用法" in result.stderr

    def test_todo_add(self, env, tool_commands, tool_calls):
        tc.run(":todo add 买牛奶", str(env), _StubRunner())
        assert tool_calls[0].name == "todo__add"
        assert tool_calls[0].args == {"title": "买牛奶"}

    @pytest.mark.parametrize("text,status", [
        (":todo", "pending"), (":todo list", "pending"),
        (":todo list all", "all"), (":todo list done", "done"),
    ])
    def test_todo_list_status(self, env, tool_commands, tool_calls, text, status):
        tc.run(text, str(env), _StubRunner())
        assert tool_calls[0].name == "todo__list"
        assert tool_calls[0].args == {"status": status}

    def test_todo_unknown_subcommand(self, env, tool_commands, tool_calls):
        assert tc.run(":todo 买牛奶", str(env), _StubRunner()).exit_code == tc.EXIT_USAGE
        assert tool_calls == []

    def test_memory_search(self, env, tool_commands, tool_calls):
        tc.run(":memory search 生日 蛋糕", str(env), _StubRunner())
        assert tool_calls[0].name == "recall__search"
        assert tool_calls[0].args["query"] == "生日 蛋糕"

    def test_knowledge_search(self, env, tool_commands, tool_calls):
        tc.run(":knowledge search 部署步骤", str(env), _StubRunner())
        assert tool_calls[0].name == "knowledge__search"
        assert tool_calls[0].args["query"] == "部署步骤"

    def test_search_requires_query(self, env, tool_commands, tool_calls):
        assert tc.run(":memory", str(env), _StubRunner()).exit_code == tc.EXIT_USAGE
        assert tc.run(":knowledge search", str(env), _StubRunner()).exit_code == tc.EXIT_USAGE
        assert tool_calls == []


class TestToolFailures:

    def test_method_missing(self, env, monkeypatch):
        stub = _StubRegistry(_collect(knowledge), methods=())
        monkeypatch.setattr(catalog, "TOOL_REGISTRY", stub)
        result = tc.run(":knowledge search 部署", str(env), _StubRunner())
        assert result.exit_code == tc.EXIT_TOOL
        assert result.stderr

    def test_tool_reports_error(self, env, tool_commands, monkeypatch):
        monkeypatch.setattr(
            "pet.tools.executor.ToolExecutor.execute_one",
            lambda self, call: ToolResult(name=call.name, error_code="tool_error",
                                          error_message="boom"))
        result = tc.run(":todo add x", str(env), _StubRunner())
        assert result.exit_code == tc.EXIT_TOOL
        assert "boom" in result.stderr


class TestResultShape:

    def test_tool_body_is_appended(self, env, tool_commands, tool_calls):
        result = tc.run(":knowledge search 部署", str(env), _StubRunner())
        assert result.stdout.splitlines() == ["ok", "细节"]

    def test_registry_lookup_exception_becomes_tool_error(self, env, monkeypatch):
        def boom(full_name):
            raise RuntimeError("registry down")

        stub = _StubRegistry(_collect(todo))
        monkeypatch.setattr(catalog, "TOOL_REGISTRY", stub)
        monkeypatch.setattr(stub, "get_method", boom)
        result = tc.run(":todo list", str(env), _StubRunner())
        assert result.exit_code == tc.EXIT_TOOL
        assert result.stderr
