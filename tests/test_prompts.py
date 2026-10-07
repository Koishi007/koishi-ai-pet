"""静态提示词组装测试：感受锚点位置、动作表缓存失效、需求做法只写一处。"""

import pytest

from pet.brain import prompts
from pet.brain.context_builder import ContextBuilder
from pet.config import config


@pytest.fixture(autouse=True)
def _clean_action_section_cache():
    """用例间共享 `_action_section` 缓存，进出都清一次，避免配置串味。"""
    prompts.invalidate_action_section()
    yield
    prompts.invalidate_action_section()


class TestFeelingMarkerPosition:
    """`<<FEELING>>` 锚点必须排在所有静态块之后，静态前缀才能命中 prompt 缓存。"""

    def test_marker_is_last_static_block(self):
        text = prompts.build_system_prompt("chat_non_vision", "chat")
        assert text.endswith(prompts.FEELING_MARKER)
        marker = text.index(prompts.FEELING_MARKER)
        assert text.index("[可用动作]") < marker   # 感知段（动作表）
        assert text.index("[核心规则]") < marker   # 任务段
        assert text.index("[记忆]") < marker       # 记忆格式块

    def test_marker_omitted_when_requested(self):
        text = prompts.build_system_prompt("interact", "interact",
                                           include_feeling_marker=False)
        assert prompts.FEELING_MARKER not in text

    def test_runtime_blocks_land_after_static_prefix(self):
        builder = ContextBuilder(
            vitals=type("V", (), {"numeric_summary": staticmethod(lambda: {"satiety": 10, "energy": 10})})(),
            mood=type("M", (), {"numeric_summary": staticmethod(lambda: {"joy": 10, "affection": 10, "sanity": 100})})(),
        )
        system = builder._build_system("chat_non_vision", "chat")
        assert system.index("[核心规则]") < system.index("[你现在的状态]")
        assert system.index("[可用动作]") < system.index("[你惦记着的事]")

    def test_no_runtime_blocks_leaves_no_marker(self):
        # 没有感受/需求/事件时也要摘掉锚点，别把 <<FEELING>> 原文漏给模型
        system = ContextBuilder()._build_system("interact", "interact")
        assert prompts.FEELING_MARKER not in system


class TestActionSectionCache:
    """动作表随调度配置变化，但靠显式失效更新，不每轮重算。"""

    def test_lazy_caches_until_invalidated(self):
        calls = []

        def factory():
            calls.append(1)
            return "内容"

        lazy = prompts._Lazy(factory)
        assert str(lazy) == "内容"
        assert str(lazy) == "内容"
        assert len(calls) == 1
        lazy.invalidate()
        assert str(lazy) == "内容"
        assert len(calls) == 2

    def test_cached_text_survives_config_change(self, monkeypatch):
        monkeypatch.setattr(config, "SCHEDULER_MID_MS", 300000)
        prompts.invalidate_action_section()
        first = str(prompts._action_section)
        monkeypatch.setattr(config, "SCHEDULER_MID_MS", 60000)
        assert str(prompts._action_section) == first

    def test_invalidate_recomputes_from_new_config(self, monkeypatch):
        monkeypatch.setattr(config, "SCHEDULER_MID_MS", 300000)
        prompts.invalidate_action_section()
        long_text = str(prompts._action_section)
        monkeypatch.setattr(config, "SCHEDULER_MID_MS", 60000)
        prompts.invalidate_action_section()
        assert str(prompts._action_section) != long_text


class TestNeedHintsSingleHome:
    """需求的具体做法只写在 `_NEED_HINTS`，别处只做抽象引导。"""

    def test_self_life_guide_has_no_concrete_need_actions(self):
        assert "饿了去找吃的" not in prompts._SELF_LIFE_GUIDE
        assert "困了累了找地方睡" not in prompts._SELF_LIFE_GUIDE

    def test_chat_task_points_to_needs_section(self):
        text = "\n".join(prompts._chat_task())
        assert "饿了就说想吃东西" not in text
        assert "你惦记着的事" in text

    def test_autonomous_user_prompts_have_no_need_actions(self):
        for text in (prompts.autonomous_vision_user_prompt("ctx"),
                     prompts.autonomous_non_vision_user_prompt("ctx")):
            assert "觅食或者向用户讨要食物" not in text
            assert "睡一会儿（sleep）" not in text

    def test_hints_remain_the_action_source(self):
        assert "自己生成食物" in ContextBuilder._NEED_HINTS["hungry"]


class TestToolGuide:
    """_tool_guide：aside 指南 + 非元工具清单；元工具始终可见，不进清单。"""

    @pytest.fixture()
    def _fake_tool(self):
        from pet.tools.registry import TOOL_REGISTRY
        TOOL_REGISTRY.register("fake_tool", "测试：假工具", group="info")
        yield
        TOOL_REGISTRY._tools.pop("fake_tool", None)

    def test_includes_aside_and_non_meta_tools(self, monkeypatch, _fake_tool):
        from pet.tools.registry import TOOL_REGISTRY
        monkeypatch.setattr(config, "LLM_TOOLS_ENABLED", True)
        guide = prompts._tool_guide()
        assert "aside" in guide
        assert "[可用工具]" in guide
        assert "- fake_tool [info] 测试：假工具" in guide
        for tool in TOOL_REGISTRY.enabled_tools:
            if tool.meta:
                assert f"- {tool.name} " not in guide

    def test_disabled_tools_falls_back_to_aside(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_TOOLS_ENABLED", False)
        guide = prompts._tool_guide()
        assert guide == prompts._TOOL_ASIDE_GUIDE
        assert "[可用工具]" not in guide

    def test_meta_only_registry_falls_back_to_aside(self, monkeypatch):
        from pet.tools.registry import TOOL_REGISTRY
        if any(not t.meta for t in TOOL_REGISTRY.enabled_tools):
            pytest.skip("注册表已加载插件工具")
        monkeypatch.setattr(config, "LLM_TOOLS_ENABLED", True)
        assert prompts._tool_guide() == prompts._TOOL_ASIDE_GUIDE

    def test_list_reflects_enabled_state(self, monkeypatch, _fake_tool):
        # 停用的工具从清单消失，恢复后回来
        from pet.tools.registry import TOOL_REGISTRY
        monkeypatch.setattr(config, "LLM_TOOLS_ENABLED", True)
        TOOL_REGISTRY.set_enabled("fake_tool", False)
        try:
            assert "- fake_tool " not in prompts._tool_guide()
        finally:
            TOOL_REGISTRY.set_enabled("fake_tool", True)
        assert "- fake_tool " in prompts._tool_guide()

    def test_system_prompt_carries_tool_list(self, monkeypatch, _fake_tool):
        monkeypatch.setattr(config, "LLM_TOOLS_ENABLED", True)
        for mode, task in (("chat_non_vision", "chat"),
                           ("autonomous_non_vision", "autonomous"),
                           ("analyze", "analyze")):
            text = prompts.build_system_prompt(mode, task)
            assert "[可用工具]" in text
            assert "tool_search__list_groups" in text

    def test_interact_task_has_no_tool_list(self, monkeypatch):
        # 交互是即时反射，不带工具段
        monkeypatch.setattr(config, "LLM_TOOLS_ENABLED", True)
        text = "\n".join(prompts._interact_task())
        assert "[可用工具]" not in text
