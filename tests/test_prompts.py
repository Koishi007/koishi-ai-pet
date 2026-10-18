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


class TestDockedPrompts:
    """贴边时的动作表、任务段与 user 段贴边状态。"""

    def test_system_action_table_filtered(self):
        text = prompts.build_system_prompt("chat_non_vision", "chat", docked=True)
        table = text.split("[可用动作]", 1)[1].split("[核心规则]", 1)[0]
        assert "共 8 个" in table
        for name in ("sleep", "thinking", "shy", "confuse",
                     "embarrassed", "dejected", "finger_heart", "shake_arms"):
            assert name in table
        for name in ("walk", "drive", "sit", "look_around"):
            assert name not in table
        assert "参考「窗口探测」数据" not in text
        assert "涉及方向/距离的指令" not in text

    def test_docked_system_has_no_wall_or_nonwall_terms(self):
        combos = (
            ("autonomous_vision", "autonomous"),
            ("autonomous_non_vision", "autonomous"),
            ("chat_vision", "chat"),
            ("chat_non_vision", "chat"),
            ("interact", "interact"),
            ("analyze", "analyze"),
        )
        for mode, task in combos:
            text = prompts.build_system_prompt(mode, task, docked=True)
            for bad in ("靠墙", "靠边", "钓鱼", "巡视", "换地方", "可玩窗口", "跳跃"):
                assert bad not in text, f"{mode}/{task} 出现了 {bad}"

    def test_undocked_table_keeps_movement(self):
        text = prompts.build_system_prompt("chat_non_vision", "chat")
        table = text.split("[可用动作]", 1)[1].split("[核心规则]", 1)[0]
        assert "walk" in table
        assert "drive" in table
        assert "[移动]" in table
        assert "[显隐]" in table

    def test_docked_task_action_floor(self):
        for fn in (prompts._autonomous_task, prompts._chat_task,
                   prompts._interact_task, prompts._analyze_task):
            text = "\n".join(fn(docked=True))
            assert "≥1个" in text
            assert "可玩窗口" not in text
            for bad in ("walk", "drive", "sit", "look_around", "靠墙", "靠边", "钓鱼"):
                assert bad not in text
        assert "总时长约" not in "\n".join(prompts._autonomous_task(docked=True))
        assert "凑满时长" not in "\n".join(prompts._autonomous_task(docked=True))
        assert "Action(≥3个)" not in "\n".join(prompts._chat_task(docked=True))

    def test_user_prompt_dock_note(self):
        note = "[贴边状态]\n测试"
        assert prompts.chat_non_vision_user_prompt("hi", "ctx", dock_note=note).startswith(note)
        assert prompts.chat_vision_user_prompt("hi", "ctx", dock_note=note).startswith(note)
        assert prompts.analyze_non_vision_user_prompt("hi", "ctx", dock_note=note).startswith(note)
        assert prompts.analyze_vision_user_prompt("hi", "ctx", dock_note=note).startswith(note)
        assert prompts.autonomous_non_vision_user_prompt("ctx", dock_note=note).startswith(note)
        assert prompts.autonomous_vision_user_prompt("ctx", dock_note=note).startswith(note)

    def test_system_prompt_from_context_builder_is_docked(self):
        builder = ContextBuilder(dock_side_fn=lambda: "left")
        system = builder._build_system("chat_non_vision", "chat")
        table = system.split("[可用动作]", 1)[1].split("[核心规则]", 1)[0]
        assert "共 8 个" in table
        assert "walk" not in table
        assert "[驻留]" in table
        assert "[移动]" not in table
        assert "[显隐]" not in table
        assert "参考「窗口探测」数据" not in system
        assert "做点当下能做的小事" in system
        assert "钓鱼" not in system
        assert "靠墙" not in system
        assert "可玩窗口" not in system
        assert "跳跃" not in system

    def test_chat_and_autonomous_user_prompts_include_note(self):
        class _Brain:
            _MAX_POOL_ENTRIES = 21

            def get_multi_turn_messages(self, **_kw):
                return []

        builder = ContextBuilder(brain_mixin=_Brain(), dock_side_fn=lambda: "left")
        chat = builder.build_chat_decide("hi", "ctx", screenshot=False)
        chat_note = chat[-1]["content"]
        assert "[贴边状态]" in chat_note
        assert "屏幕左边缘" in chat_note
        assert "system" not in chat_note
        assert "后文步骤" not in chat_note
        assert "钓鱼" not in chat_note
        assert "靠墙" not in chat_note

        autonomous = builder.build_autonomous_decide("ctx", screenshot=False)
        auto_note = autonomous[-1]["content"]
        assert "[贴边状态]" in auto_note
        assert "专心工作" in auto_note
        assert "后文步骤" not in auto_note
        assert "有移动动作可用" in auto_note
        assert "靠边" not in auto_note
        assert "钓鱼" not in auto_note

        analyze = builder.build_analyze_decide("hi", "ctx", screenshot=False)
        assert "[贴边状态]" in analyze[-1]["content"]
        assert "后文步骤" not in analyze[-1]["content"]

    def test_interact_note_from_context(self):
        messages = ContextBuilder(dock_side_fn=lambda: "right").build_interact("用户摸了摸你的头")
        content = messages[1]["content"]
        assert "[贴边状态]" in content
        assert "屏幕右边缘" in content
        assert "做不到" in content
        assert "system" not in content
        assert "后文步骤" not in content
        assert "钓鱼" not in content
        assert "靠墙" not in content
        assert content.index("[贴边状态]") < content.index("用户摸了摸你的头")

    def test_dock_note_elements(self):
        # 贴边段只有三要素，追加句只给 autonomous
        for side, cn in (("left", "左"), ("right", "右")):
            for task in ("autonomous", "chat", "analyze", "interact"):
                note = prompts.dock_note(side, task)
                assert note.startswith("[贴边状态]")
                assert f"你现在贴在屏幕{cn}边缘，你自己下不来。" in note
                assert "直接说明做不到" in note
                assert "觅食" not in note
                assert "动作表" not in note
        assert "专心工作" in prompts.dock_note("left", "autonomous")
        for task in ("chat", "analyze", "interact"):
            assert "专心工作" not in prompts.dock_note("left", task)

    def test_autonomous_prompt_gates_movement_branches(self):
        # 有窗口/无窗口的分支都挂在“有移动动作可用”上，贴边段不再单独声明步骤不适用
        for fn in (prompts.autonomous_vision_user_prompt,
                   prompts.autonomous_non_vision_user_prompt):
            for note in ("", "[贴边状态]"):
                text = fn("CTX", dock_note=note)
                assert "有窗口且有移动动作可用" in text
                assert "无窗口且有移动动作可用" in text
                assert "如果你可以移动的话" not in text
        assert "后文步骤" not in prompts.dock_note("left", "autonomous")

    def test_lonely_hint_wall(self):
        assert "凑到你身边" in ContextBuilder._need_hint("lonely")
        assert "凑到你身边" not in ContextBuilder._need_hint("lonely", docked=True)

    def test_drowsy_hint_wall(self):
        nap = ContextBuilder._NAP_HOUR
        undocked = ContextBuilder._circadian_need(nap)
        docked = ContextBuilder._circadian_need(nap, docked=True)
        assert undocked is not None and "sit" in undocked[2]
        assert docked is not None and "sit" not in docked[2]

    def test_no_note_when_undocked(self):
        messages = ContextBuilder().build_interact("事件")
        assert "[贴边状态]" not in messages[1]["content"]

    def test_user_prompts_have_no_duration_phrase(self):
        assert "凑满时长" not in prompts.autonomous_vision_user_prompt("ctx")
        assert "凑满时长" not in prompts.autonomous_non_vision_user_prompt("ctx")
        assert "凑满时长" not in prompts.chat_vision_user_prompt("hi", "ctx")
        assert "凑满时长" not in prompts.chat_non_vision_user_prompt("hi", "ctx")

    def test_food_guide_follows_tool(self, monkeypatch):
        from pet.tools.registry import TOOL_REGISTRY
        if TOOL_REGISTRY.get_tool("food") is None:
            pytest.skip("food 工具未注册")
        monkeypatch.setattr(config, "FOOD_ENABLED", True)
        TOOL_REGISTRY.set_enabled("food", False)
        try:
            assert "[觅食]" not in "\n".join(prompts._chat_task())
            assert "自己生成食物" not in ContextBuilder._need_hint("hungry")
        finally:
            TOOL_REGISTRY.set_enabled("food", True)
        assert "[觅食]" in "\n".join(prompts._chat_task())
        assert "自己生成食物" in ContextBuilder._need_hint("hungry")


class TestActionTable:
    """动作表每行带类型标记：移动 / 驻留 / 显隐。"""

    @staticmethod
    def _table(docked: bool = False) -> str:
        from pet.action import registry
        exclude = []
        if docked:
            exclude = [name for name in registry.ACTION_NAMES
                       if name not in registry.EDGE_ACTION_NAMES]
        return registry.generate_action_section(exclude=exclude)

    def test_entries_carry_category(self):
        text = self._table()
        for entry in ("- drive [移动]", "- walk [移动]", "- bounce [移动]",
                      "- shake_arms [驻留]", "- sleep [驻留]",
                      "- fade_in [显隐]", "- fade_out [显隐]"):
            assert entry in text

    def test_docked_table_only_has_lingering(self):
        text = self._table(docked=True)
        assert "[驻留]" in text
        assert "[移动]" not in text
        assert "[显隐]" not in text


class TestActionLimits:
    """动作数量策略集中在 _ACTION_LIMITS / _DOCKED_ACTION_LIMIT。"""

    def test_mapping_values(self):
        assert prompts._action_limit("chat", True) == ("≥1", "至少 1")
        assert prompts._action_limit("chat", False) == ("≥3", "至少 3")
        assert prompts._action_limit("interact", False) == ("1-2", "只输出 1-2")
        assert prompts._action_limit("analyze", False) == ("≥3", "至少 3")
        assert prompts._action_limit("autonomous", True) == ("≥1", "至少 1")

    def test_autonomous_free_reads_min_action_count(self):
        n = prompts.min_action_count()
        assert prompts._action_limit("autonomous", False) == (f"≥{n}", f"最少 {n}")

    def test_examples_only_use_shared_actions(self):
        for fn in (prompts._autonomous_task, prompts._chat_task,
                   prompts._interact_task, prompts._analyze_task):
            for docked in (False, True):
                example = fn(docked=docked)[0]  # 任务段的输出格式块，即 few-shot 示例
                for name in ("walk", "drive", "sit", "look_around", "stretch"):
                    assert name not in example, f"{fn.__name__} docked={docked} 出现 {name}"


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

    def test_web_search_hint_follows_loaded_state(self, monkeypatch):
        """web_search 加载后才下发优先使用引导。"""
        from pet.tools.registry import TOOL_REGISTRY
        monkeypatch.setattr(config, "LLM_TOOLS_ENABLED", True)
        saved = TOOL_REGISTRY._tools.pop("web_search", None)
        saved_enabled = TOOL_REGISTRY.is_enabled("web_search")
        try:
            TOOL_REGISTRY.set_enabled("web_search", True)
            assert "搜索网页优先用 web_search" not in prompts._tool_list()
            TOOL_REGISTRY.register("web_search", "测试：直连搜索 API", group="web")
            assert "搜索网页优先用 web_search" in prompts._tool_list()
        finally:
            TOOL_REGISTRY._tools.pop("web_search", None)
            if saved is not None:
                TOOL_REGISTRY._tools["web_search"] = saved
            TOOL_REGISTRY.set_enabled("web_search", saved_enabled)

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
