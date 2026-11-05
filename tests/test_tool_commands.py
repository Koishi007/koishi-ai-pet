"""工具注册的终端命令：登记内容、参数翻译与注册表聚合。

命令只把参数文本翻成 (方法名, 参数)，真正的调用走通用执行器，所以这里只断言
登记出来的用法、补全候选与翻译结果。
"""

import pytest

from pet.tools import knowledge, timer, todo
from pet.tools.registry import ToolCommand, ToolRegistry


class _Recorder:
    """收集 register_commands() 的登记结果。"""

    def __init__(self):
        self.commands: list[tuple[str, ToolCommand]] = []

    def add_command(self, tool_name: str, **kwargs) -> None:
        self.commands.append((tool_name, ToolCommand(**kwargs)))


def _command(module) -> tuple[str, ToolCommand]:
    recorder = _Recorder()
    module.register_commands(recorder)
    assert len(recorder.commands) == 1
    return recorder.commands[0]


class TestTimerCommand:

    @pytest.fixture
    def command(self):
        return _command(timer)[1]

    def test_rows_and_words(self, command):
        assert command.name == "timer"
        assert command.group
        assert len(command.rows) == 3
        assert command.words == ("list", "cancel")
        assert command.paths is False
        assert command.examples and command.examples[0].startswith(":timer ")

    @pytest.mark.parametrize("text,expected", [
        ("list", ("list", {})),
        ("cancel ab12", ("cancel", {"timer_id": "ab12"})),
        ("10m 泡面", ("set", {"duration": 600, "label": "泡面"})),
        ("90", ("set", {"duration": 90, "label": "时间到"})),
        ("1h30m", ("set", {"duration": 5400, "label": "时间到"})),
    ])
    def test_resolve(self, command, text, expected):
        assert command.resolve(text) == expected

    @pytest.mark.parametrize("text", ["", "cancel", "明天", "0", "10x"])
    def test_resolve_rejects(self, command, text):
        with pytest.raises(ValueError):
            command.resolve(text)

    @pytest.mark.parametrize("text,seconds", [
        ("90", 90), ("90s", 90), ("10m", 600), ("1h", 3600), ("1h30m", 5400),
        ("2H", 7200),
    ])
    def test_parse_duration(self, text, seconds):
        assert timer.parse_duration(text) == seconds

    @pytest.mark.parametrize("text", ["", "0", "abc", "1h 30m", "-5", "m"])
    def test_parse_duration_rejects(self, text):
        assert timer.parse_duration(text) is None


class TestTodoCommand:

    @pytest.fixture
    def command(self):
        return _command(todo)[1]

    def test_rows_and_words(self, command):
        assert command.name == "todo"
        assert command.words == ("add", "list")
        assert command.values == {"list": ("pending", "done", "all")}
        assert command.examples and command.examples[0].startswith(":todo ")

    @pytest.mark.parametrize("text,expected", [
        ("", ("list", {"status": "pending"})),
        ("list", ("list", {"status": "pending"})),
        ("list done", ("list", {"status": "done"})),
        ("list all", ("list", {"status": "all"})),
        ("list 乱七八糟", ("list", {"status": "pending"})),
        ("add 买牛奶", ("add", {"title": "买牛奶"})),
    ])
    def test_resolve(self, command, text, expected):
        assert command.resolve(text) == expected

    @pytest.mark.parametrize("text", ["add", "remove 1"])
    def test_resolve_rejects(self, command, text):
        with pytest.raises(ValueError):
            command.resolve(text)


class TestKnowledgeCommand:

    @pytest.fixture
    def command(self):
        return _command(knowledge)[1]

    def test_rows_and_words(self, command):
        assert command.name == "knowledge"
        assert command.words == ("search",)
        assert command.examples and command.examples[0].startswith(":knowledge ")

    def test_resolve(self, command):
        assert command.resolve("search 部署步骤") == (
            "search", {"query": "部署步骤", "limit": 3})

    @pytest.mark.parametrize("text", ["", "search", "find 部署"])
    def test_resolve_rejects(self, command, text):
        with pytest.raises(ValueError):
            command.resolve(text)


class TestRegistryAggregation:

    def test_memory_command_is_registered(self):
        names = [command.name for _tool, command in ToolRegistry().commands()]
        assert "memory" in names

    def test_disabled_tool_hides_its_commands(self):
        registry = ToolRegistry()
        assert "memory" in [c.name for _tool, c in registry.commands()]
        registry.set_enabled("recall", False)
        assert "memory" not in [c.name for _tool, c in registry.commands()]

    def test_duplicate_command_name_is_ignored(self):
        registry = ToolRegistry()
        registry.add_command("recall", name="memory", group="检索",
                             rows=((":memory", "重复登记"),),
                             resolve=lambda arg: ("search", {"query": arg}))
        names = [command.name for _tool, command in registry.commands()]
        assert names.count("memory") == 1

    def test_unknown_tool_is_rejected(self):
        with pytest.raises(KeyError):
            ToolRegistry().add_command("nope", name="nope", group="通用",
                                       rows=((":nope", "没有这个工具"),),
                                       resolve=lambda arg: ("x", {}))
