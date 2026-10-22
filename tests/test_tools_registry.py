"""工具框架契约测试：注册表检索、schema 生成、执行器容错。"""

import threading
from types import SimpleNamespace

import pytest

from pet.tools import executor as executor_mod
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
    def test_single_keyword_matches_description(self, registry):
        result = registry._tool_search_search("演示")
        assert [m["name"] for m in result["matches"]] == ["demo"]
        assert "直接调用" in result["hint"]
        assert result["__context__"].startswith("搜到 demo")

    def test_full_method_name_is_searchable(self, registry):
        # 模型常直接拿 demo__echo 去搜，整串必须命中
        result = registry._tool_search_search("demo__echo")
        assert [m["name"] for m in result["matches"]] == ["demo"]

    def test_all_terms_must_hit(self, registry):
        registry.register("bili", "B 站：点赞、投币、评论", group="info")
        result = registry._tool_search_search("点赞 评论")
        assert [m["name"] for m in result["matches"]] == ["bili"]

    def test_partial_terms_fall_back_to_any_term(self, registry):
        registry.register("bili", "B 站：点赞、投币、评论", group="info")
        result = registry._tool_search_search("点赞 天气")
        assert [m["name"] for m in result["matches"]] == ["bili"]

    def test_separators_split_keywords(self, registry):
        registry.register("bili", "B 站：点赞、投币、评论", group="info")
        result = registry._tool_search_search("点赞、评论")
        assert [m["name"] for m in result["matches"]] == ["bili"]

    def test_whitespace_difference_ignored(self, registry):
        registry.register("bili", "B 站：点赞", group="info")
        result = registry._tool_search_search("B站")
        assert [m["name"] for m in result["matches"]] == ["bili"]

    def test_method_description_is_searchable(self, registry):
        result = registry._tool_search_search("回显")
        assert [m["name"] for m in result["matches"]] == ["demo"]

    def test_no_match_returns_all_tools_with_hint(self, registry):
        result = registry._tool_search_search("查无此工具")
        assert [m["name"] for m in result["matches"]] == ["demo"]
        assert "不要重复搜索" in result["hint"]
        assert result["__context__"].startswith("未匹配")

    def test_default_group_never_returned(self, registry):
        names = [m["name"] for m in registry._tool_search_search("工具")["matches"]]
        assert "tool_search" not in names

    def test_disabled_tool_excluded(self, registry):
        registry.set_enabled("demo", False)
        assert registry._tool_search_search("演示")["matches"] == []


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
        assert result.success is False
        assert "nope__go" in result.error

    def test_successful_call(self, executor):
        result = executor.execute([ToolCall("demo__echo", {"text": "hi"})])[0]
        assert result.success is True
        assert result.data["text"] == "hi"

    def test_argument_validation_failure(self, executor):
        result = executor.execute([ToolCall("demo__count", {"n": "不是数字"})])[0]
        assert result.success is False
        assert "'n'" in result.error
        assert "int" in result.error and "str" in result.error

    def test_type_error_reported_as_signature_mismatch(self, executor):
        # 参数名与 handler 签名不匹配 → handler 抛 TypeError
        result = executor.execute([ToolCall("demo__bad", {"other": "x"})])[0]
        assert result.success is False
        assert "other" in result.error

    def test_runtime_error_propagated_as_message(self, executor):
        result = executor.execute([ToolCall("demo__boom", {})])[0]
        assert result.success is False
        assert "工具炸了" in result.error

    def test_timeout_reported(self, executor):
        try:
            result = executor.execute([ToolCall("demo__slow", {})])[0]
            assert result.success is False
            assert "0.05" in result.error
        finally:
            _slow_gate.set()  # 释放仍在阻塞的 handler 线程
            _slow_gate.clear()

    def test_image_and_context_extracted_from_data(self, executor):
        result = executor.execute([ToolCall("demo__shot", {})])[0]
        assert result.image_b64 == "QUJD"
        assert result.image_mime == "image/jpeg"
        assert result.context_brief == "已截图"
        assert "__image__" not in result.data
        assert "__context__" not in result.data

    def test_multiple_calls_all_reported(self, executor):
        results = executor.execute([
            ToolCall("demo__echo", {"text": "a"}),
            ToolCall("demo__boom", {}),
        ])
        assert [r.success for r in results] == [True, False]


class TestNormalizeAndFormat:
    def test_dict_with_summary(self):
        assert ToolExecutor._normalize({"summary": "完成", "n": 1}) == '完成\n{"n": 1}'

    def test_dict_without_summary(self):
        assert ToolExecutor._normalize({"n": 1}) == '{"n": 1}'

    def test_str_passthrough(self):
        assert ToolExecutor._normalize("原文") == "原文"

    def test_format_results_success_and_failure_lines(self, executor):
        results = executor.execute([
            ToolCall("demo__echo", {"text": "x"}),
            ToolCall("demo__boom", {}),
        ])
        text, images = ToolExecutor.format_results(results)
        assert "[OK demo__echo]" in text
        assert "[FAIL demo__boom]" in text
        assert images == []

    def test_format_results_emits_image_uri(self, executor):
        results = executor.execute([ToolCall("demo__shot", {})])
        text, images = ToolExecutor.format_results(results)
        assert images == ["data:image/jpeg;base64,QUJD"]
        assert len(text.split("\n\n")) == 2


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
