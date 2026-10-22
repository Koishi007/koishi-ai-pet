"""工具框架契约测试：注册表检索、schema 生成、执行器容错。"""

import threading
from types import SimpleNamespace

import pytest

from pet.tools import executor as executor_mod
from pet.tools import registry as registry_module
from pet.tools.context import ToolContext
from pet.tools.executor import ToolCall, ToolExecutor
from pet.tools.registry import ToolRegistry


def _echo(text: str = "") -> dict:
    return {"summary": f"echo:{text}", "text": text}


def _boom() -> dict:
    raise RuntimeError("工具炸了")


def _bad_signature(unknown_arg: str = "") -> dict:
    return {"summary": unknown_arg}


# 阻塞在可控事件上：超时判定后由测试主动释放，避免真实 sleep 拖慢套件与线程残留
_slow_gate = threading.Event()


def _slow() -> dict:
    _slow_gate.wait(timeout=5)
    return {"summary": "太慢了"}


def _with_image() -> dict:
    return {"summary": "截图完成", "__image__": "QUJD",
            "__image_mime__": "image/jpeg", "__context__": "已截图"}


def _business_fail() -> dict:
    return {"error": "没做成", "code": "denied"}


def _business_fail_plain() -> dict:
    return {"error": "失败了"}


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register("demo", "演示工具", group="test")
    reg.add_method("demo", "echo", "回显文本", handler=_echo,
                   args={"text": {"type": "str", "desc": "要回显的文本"}})
    reg.add_method("demo", "count", "计数", handler=lambda n=1: {"summary": f"n={n}"},
                   args={"n": {"type": "int", "desc": "次数"},
                         "mode": {"type": "str", "desc": "模式",
                                  "enum": ["fast", "slow"], "default": "fast"}})
    reg.add_method("demo", "boom", "抛异常", handler=_boom)
    reg.add_method("demo", "bad", "签名不匹配", handler=_bad_signature,
                   args={"other": {"type": "str", "required": True}})
    reg.add_method("demo", "slow", "慢方法", handler=_slow, timeout=0.05)
    reg.add_method("demo", "shot", "带图", handler=_with_image)
    reg.add_method("demo", "fail", "业务失败", handler=_business_fail)
    reg.add_method("demo", "fail_plain", "业务失败无码", handler=_business_fail_plain)
    return reg


@pytest.fixture
def executor(registry, monkeypatch) -> ToolExecutor:
    monkeypatch.setattr(executor_mod, "TOOL_REGISTRY", registry)
    return ToolExecutor()


class TestRegistryLookup:
    def test_get_method_by_double_underscore(self, registry):
        method = registry.get_method("demo__echo")
        assert method is not None
        assert method.handler is _echo

    def test_missing_separator_returns_none(self, registry):
        assert registry.get_method("demo.echo") is None
        assert registry.get_method("demo") is None

    def test_unknown_tool_returns_none(self, registry):
        assert registry.get_method("nope__echo") is None

    def test_disabled_tool_not_resolvable(self, registry):
        registry.set_enabled("demo", False)
        assert registry.get_method("demo__echo") is None
        assert "demo" in registry.disabled_set
        registry.set_enabled("demo", True)
        assert registry.get_method("demo__echo") is not None

    def test_group_helpers(self, registry):
        assert "test" in registry.get_groups()
        assert [t.name for t in registry.get_tools_by_group("test")] == ["demo"]
        assert registry.get_tool_group("demo") == "test"
        assert registry.get_tool_group("missing") == "default"


class TestToolSearch:
    @staticmethod
    def _body_lines(result) -> list[str]:
        return [line for line in result["body"].splitlines() if line.strip()]

    def test_single_keyword_matches_description(self, registry):
        result = registry._tool_search_search("演示")
        assert "demo__echo" in result["body"]
        assert result["groups"] == ["test"]
        assert result["agent_hint"]

    def test_full_method_name_is_searchable(self, registry):
        # 模型常直接拿 demo__echo 去搜，整串必须命中
        result = registry._tool_search_search("demo__echo")
        assert "demo__echo" in result["body"]

    def test_all_terms_must_hit(self, registry):
        registry.register("bili", "B 站：点赞、投币、评论", group="info")
        result = registry._tool_search_search("点赞 评论")
        assert "bili" in result["body"]
        assert result["groups"] == ["info"]

    def test_partial_terms_fall_back_to_any_term(self, registry):
        registry.register("bili", "B 站：点赞、投币、评论", group="info")
        result = registry._tool_search_search("点赞 天气")
        assert "bili" in result["body"]
        assert result["groups"] == ["info"]

    def test_separators_split_keywords(self, registry):
        registry.register("bili", "B 站：点赞、投币、评论", group="info")
        result = registry._tool_search_search("点赞、评论")
        assert "bili" in result["body"]

    def test_whitespace_difference_ignored(self, registry):
        registry.register("bili", "B 站：点赞", group="info")
        result = registry._tool_search_search("B站")
        assert "bili" in result["body"]

    def test_method_description_is_searchable(self, registry):
        result = registry._tool_search_search("回显")
        assert "demo__echo" in result["body"]

    def test_no_match_returns_group_catalog(self, registry):
        registry.register("bili", "B 站：点赞、投币、评论", group="info")
        result = registry._tool_search_search("查无此工具")
        assert result["groups"] == []
        assert "[test]" in result["body"] and "[info]" in result["body"]

    def test_results_capped_per_search(self, registry):
        for i in range(8):
            registry.register(f"t{i}", "天气 查询", group="info")
        result = registry._tool_search_search("天气")
        assert len(self._body_lines(result)) == 5
        assert result["agent_hint"]

    def test_default_group_never_returned(self, registry):
        assert "tool_search" not in registry._tool_search_search("工具")["body"]

    def test_disabled_tool_excluded(self, registry):
        registry.set_enabled("demo", False)
        assert "demo" not in registry._tool_search_search("演示")["body"]


class TestRecallOutput:
    @staticmethod
    def _store(rows):
        class Store:
            def search_memories(self, query, limit=5):
                return rows

            def list_memories(self, search="", start_date="", end_date="",
                              page=0, page_size=10):
                return rows, len(rows)

            def format_memory_time(self, value):
                return value

        return Store()

    @staticmethod
    def _patch(monkeypatch, rows):
        monkeypatch.setattr("pet.brain.memory.get_memory_store",
                            lambda: TestRecallOutput._store(rows))
        monkeypatch.setattr(registry_module.TOOL_CTX, "speech_random",
                            lambda *args, **kwargs: None)

    def test_search_returns_body_lines(self, registry, monkeypatch):
        rows = [{"content": "喜欢猫", "level": "L2", "created_at": "2026-10-01"}]
        self._patch(monkeypatch, rows)
        result = registry._recall_search("猫")
        assert result["body"] == "[2026-10-01]（L2）喜欢猫"

    def test_browse_returns_body_and_pagination(self, registry, monkeypatch):
        rows = [{"content": "喜欢猫", "level": "L2", "created_at": "2026-10-01"}]
        self._patch(monkeypatch, rows)
        result = registry._recall_browse(page=1)
        assert result["body"] == "[2026-10-01]（L2）喜欢猫"
        assert result["pagination"]["total_items"] == 1
        assert result["pagination"]["has_next"] is False


class TestOpenAiSchema:
    def test_schema_types_and_required(self, registry):
        tools = registry.to_openai_tools(groups={"test"})
        by_name = {t["function"]["name"]: t["function"] for t in tools}
        count_schema = by_name["demo__count"]["parameters"]
        assert count_schema["properties"]["n"]["type"] == "integer"
        assert count_schema["properties"]["mode"]["type"] == "string"

    def test_default_and_enum_preserved(self, registry):
        tools = registry.to_openai_tools(groups={"test"})
        by_name = {t["function"]["name"]: t["function"] for t in tools}
        mode = by_name["demo__count"]["parameters"]["properties"]["mode"]
        assert mode["enum"] == ["fast", "slow"]
        assert mode["default"] == "fast"

    def test_groups_filter_excludes_other_groups(self, registry):
        registry.register("other", "其他工具", group="other")
        registry.add_method("other", "ping", "ping", handler=lambda: {"summary": "pong"})
        names = {t["function"]["name"] for t in registry.to_openai_tools(groups={"test"})}
        assert "other__ping" not in names
        assert "demo__echo" in names


class TestExecutorRouting:
    def test_unknown_tool(self, executor):
        result = executor.execute([ToolCall("nope__go", {})])[0]
        assert result.ok is False
        assert result.error_code == "unknown_tool"
        assert "nope__go" in result.error_message

    def test_successful_call(self, executor):
        result = executor.execute([ToolCall("demo__echo", {"text": "hi"})])[0]
        assert result.ok is True
        assert result.output["text"] == "hi"

    def test_argument_validation_failure(self, executor):
        result = executor.execute([ToolCall("demo__count", {"n": "不是数字"})])[0]
        assert result.ok is False
        assert result.error_code == "bad_arguments"
        assert "'n'" in result.error_message
        assert "int" in result.error_message and "str" in result.error_message

    def test_type_error_reported_as_signature_mismatch(self, executor):
        # 参数名与 handler 签名不匹配 → handler 抛 TypeError
        result = executor.execute([ToolCall("demo__bad", {"other": "x"})])[0]
        assert result.ok is False
        assert result.error_code == "handler_error"
        assert "other" in result.error_message

    def test_runtime_error_propagated_as_message(self, executor):
        result = executor.execute([ToolCall("demo__boom", {})])[0]
        assert result.ok is False
        assert "工具炸了" in result.error_message

    def test_business_error_maps_code(self, executor):
        result = executor.execute([ToolCall("demo__fail", {})])[0]
        assert result.ok is False
        assert result.error_code == "denied"
        assert result.error_message == "没做成"

    def test_business_error_without_code_falls_back(self, executor):
        result = executor.execute([ToolCall("demo__fail_plain", {})])[0]
        assert result.ok is False
        assert result.error_code == "tool_error"

    def test_timeout_reported(self, executor):
        try:
            result = executor.execute([ToolCall("demo__slow", {})])[0]
            assert result.ok is False
            assert result.error_code == "timeout"
            assert "0.05" in result.error_message
        finally:
            _slow_gate.set()  # 释放仍在阻塞的 handler 线程
            _slow_gate.clear()

    def test_output_passed_through_untouched(self, executor):
        """执行器不认识任何魔法键：返回值原样进 output，投影由 output.schema 负责。"""
        result = executor.execute([ToolCall("demo__shot", {})])[0]
        assert result.ok is True
        assert result.meta == {}
        assert "__image__" in result.output

    def test_multiple_calls_all_reported(self, executor):
        results = executor.execute([
            ToolCall("demo__echo", {"text": "a"}),
            ToolCall("demo__boom", {}),
        ])
        assert [r.ok for r in results] == [True, False]


class TestSideEffectDeclaration:
    def test_declared_tools(self, registry):
        registry.register("writer", "写工具", side_effect=True)
        registry.register("reader", "读工具")
        assert registry.is_side_effect("writer__do") is True
        assert registry.is_side_effect("reader__read") is False

    def test_unknown_tool_treated_as_side_effect(self, registry):
        assert registry.is_side_effect("ghost__x") is True


class TestToolContextVision:
    def test_unbound_context_reports_no_vision(self):
        assert ToolContext().has_vision() is False

    @pytest.mark.parametrize("enabled", [True, False])
    def test_vision_follows_behavior(self, enabled):
        ctx = ToolContext()
        ctx.bind(SimpleNamespace(behavior=SimpleNamespace(has_vision=enabled)))
        assert ctx.has_vision() is enabled
