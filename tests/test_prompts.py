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
        assert text.index("walk") < marker       # 动作表
        assert text.index("Summary:") < marker   # 输出协议与任务约束

    def test_marker_omitted_when_requested(self):
        text = prompts.build_system_prompt("interact", "interact",
                                           include_feeling_marker=False)
        assert prompts.FEELING_MARKER not in text

    def test_runtime_blocks_land_after_static_prefix(self):
        builder = ContextBuilder(
            vitals=type("V", (), {"numeric_summary": staticmethod(lambda: {"satiety": 10, "energy": 10})})(),
            mood=type("M", (), {"numeric_summary": staticmethod(lambda: {"joy": 10, "affection": 10, "sanity": 100})})(),
        )
        feeling = builder._build_feeling()
        needs = builder._build_needs_note()
        system = builder._build_system("chat_non_vision", "chat")
        anchor = system.index("walk")
        assert feeling and needs
        assert system.index(feeling) > anchor
        assert system.index(needs) > anchor

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
        from pet.action.registry import ACTION_NAMES

        for name in ACTION_NAMES:
            assert name not in prompts._SELF_LIFE_GUIDE
        for hint in ContextBuilder._NEED_HINTS.values():
            assert hint not in prompts._SELF_LIFE_GUIDE

    def test_autonomous_user_prompts_have_no_need_actions(self):
        for text in (prompts.autonomous_vision_user_prompt("ctx"),
                     prompts.autonomous_non_vision_user_prompt("ctx")):
            for hint in ContextBuilder._NEED_HINTS.values():
                assert hint not in text


class TestDockedPrompts:
    """贴边时的动作表、任务段与 user 段贴边状态。"""

    def test_system_action_table_filtered(self):
        from pet.action.registry import ACTION_NAMES, EDGE_ACTION_NAMES

        text = prompts.build_system_prompt("chat_non_vision", "chat", docked=True)
        for name in EDGE_ACTION_NAMES:
            assert name in text
        for name in set(ACTION_NAMES) - EDGE_ACTION_NAMES:
            assert name not in text

    def test_docked_system_has_no_wall_or_nonwall_terms(self):
        from pet.action.registry import ACTION_NAMES, EDGE_ACTION_NAMES

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
            assert prompts._WINDOW_GUIDE not in text
            for name in set(ACTION_NAMES) - EDGE_ACTION_NAMES:
                assert name not in text, f"{mode}/{task} 出现了 {name}"

    def test_undocked_table_keeps_movement(self):
        text = prompts.build_system_prompt("chat_non_vision", "chat")
        assert "walk" in text
        assert "drive" in text
        assert "fade_in" in text

    def test_docked_task_action_floor(self):
        action_label, _ = prompts._DOCKED_ACTION_LIMIT
        for fn in (prompts._autonomous_task, prompts._chat_task,
                   prompts._interact_task, prompts._analyze_task):
            text = "\n".join(fn(docked=True))
            assert f"Action({action_label}个)" in text
            for name in ("walk", "drive", "sit", "look_around"):
                assert name not in text
        chat_text = "\n".join(prompts._chat_task(docked=False))
        undocked_label, _ = prompts._action_limit("chat", False)
        assert f"Action({undocked_label}个)" in chat_text

    def test_user_prompt_dock_note(self):
        note = "[贴边状态]\n测试"
        assert prompts.chat_non_vision_user_prompt("hi", "ctx", dock_note=note).startswith(note)
        assert prompts.chat_vision_user_prompt("hi", "ctx", dock_note=note).startswith(note)
        assert prompts.analyze_non_vision_user_prompt("hi", "ctx", dock_note=note).startswith(note)
        assert prompts.analyze_vision_user_prompt("hi", "ctx", dock_note=note).startswith(note)
        assert prompts.autonomous_non_vision_user_prompt("ctx", dock_note=note).startswith(note)
        assert prompts.autonomous_vision_user_prompt("ctx", dock_note=note).startswith(note)

    def test_system_prompt_from_context_builder_is_docked(self):
        from pet.action.registry import ACTION_NAMES, EDGE_ACTION_NAMES

        builder = ContextBuilder(dock_side_fn=lambda: "left")
        system = builder._build_system("chat_non_vision", "chat")
        assert prompts._WINDOW_GUIDE not in system
        assert prompts._SELF_LIFE_GUIDE in system
        for name in set(ACTION_NAMES) - EDGE_ACTION_NAMES:
            assert name not in system
        for name in EDGE_ACTION_NAMES:
            assert name in system

    def test_chat_and_autonomous_user_prompts_include_note(self):
        from pet.action.registry import ACTION_NAMES, EDGE_ACTION_NAMES

        class _Brain:
            _MAX_POOL_ENTRIES = 21

            def get_multi_turn_messages(self, **_kw):
                return []

        builder = ContextBuilder(brain_mixin=_Brain(), dock_side_fn=lambda: "left")
        chat = builder.build_chat_decide("hi", "ctx", screenshot=False)
        chat_note = chat[-1]["content"]
        assert chat_note.startswith(prompts.dock_note("left", "chat"))
        for name in set(ACTION_NAMES) - EDGE_ACTION_NAMES:
            assert name not in chat_note

        autonomous = builder.build_autonomous_decide("ctx", screenshot=False)
        auto_note = autonomous[-1]["content"]
        assert auto_note.startswith(prompts.dock_note("left", "autonomous"))
        for name in set(ACTION_NAMES) - EDGE_ACTION_NAMES:
            assert name not in auto_note

        analyze = builder.build_analyze_decide("hi", "ctx", screenshot=False)
        assert analyze[-1]["content"].startswith(prompts.dock_note("left", "analyze"))

    def test_history_note_precedes_current_prompt(self):
        """历史非空时，字段说明紧贴历史放在 user 段最前。"""
        class _Brain:
            _MAX_POOL_ENTRIES = 21

            def get_multi_turn_messages(self, **_kw):
                return [{"role": "assistant", "content": "Speech: 嗯"}]

        messages = ContextBuilder(brain_mixin=_Brain()).build_chat_decide(
            "在吗", "ctx", screenshot=False)
        assert messages[-1]["content"].startswith(prompts.history_note())

    def test_history_note_skipped_without_history(self):
        """首轮没有历史样例，不必花这段 token。"""
        class _Brain:
            _MAX_POOL_ENTRIES = 21

            def get_multi_turn_messages(self, **_kw):
                return []

        messages = ContextBuilder(brain_mixin=_Brain()).build_chat_decide(
            "在吗", "ctx", screenshot=False)
        assert "[历史说明]" not in messages[-1]["content"]

    def test_interact_note_from_context(self):
        messages = ContextBuilder(dock_side_fn=lambda: "right").build_interact("用户摸了摸你的头")
        content = messages[1]["content"]
        note = prompts.dock_note("right", "interact")
        assert content.startswith(note)
        assert content.index("用户摸了摸你的头") >= len(note)

    def test_dock_note_elements(self):
        # 贴边段基础三行，autonomous 追加一行
        expected_lines = {"autonomous": 4, "chat": 3, "analyze": 3, "interact": 3}
        for side in ("left", "right"):
            for task, lines in expected_lines.items():
                note = prompts.dock_note(side, task)
                assert note.strip()
                assert len(note.splitlines()) == lines
        for task in ("chat", "analyze", "interact"):
            left = prompts.dock_note("left", task).splitlines()
            right = prompts.dock_note("right", task).splitlines()
            assert len(left) == len(right)
            assert sum(a != b for a, b in zip(left, right)) == 1

    def test_lonely_hint_wall(self):
        undocked = ContextBuilder._need_hint("lonely")
        docked = ContextBuilder._need_hint("lonely", docked=True)
        assert undocked and docked
        assert undocked != docked

    def test_drowsy_hint_wall(self):
        nap = ContextBuilder._NAP_HOUR
        undocked = ContextBuilder._circadian_need(nap)
        docked = ContextBuilder._circadian_need(nap, docked=True)
        assert undocked is not None and "sit" in undocked[2]
        assert docked is not None and "sit" not in docked[2]

    def test_no_note_when_undocked(self):
        messages = ContextBuilder().build_interact("事件")
        assert prompts.dock_note("left", "interact") not in messages[1]["content"]

    def test_food_guide_follows_tool(self, monkeypatch):
        from pet.tools.registry import TOOL_REGISTRY
        if TOOL_REGISTRY.get_tool("food") is None:
            pytest.skip("food 工具未注册")
        monkeypatch.setattr(config, "FOOD_ENABLED", True)
        food_on = "\n".join(prompts._chat_task())
        hint_on = ContextBuilder._need_hint("hungry")
        TOOL_REGISTRY.set_enabled("food", False)
        try:
            food_off = "\n".join(prompts._chat_task())
            hint_off = ContextBuilder._need_hint("hungry")
        finally:
            TOOL_REGISTRY.set_enabled("food", True)
        assert food_on != food_off
        assert hint_on != hint_off


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
        from pet.action import registry

        text = self._table()
        for name, action in registry.REGISTRY.items():
            assert f"- {name} [{action.category}]" in text

    def test_docked_table_only_has_lingering(self):
        from pet.action import registry

        text = self._table(docked=True)
        categories = {action.category for action in registry.REGISTRY.values()
                      if action.name in registry.EDGE_ACTION_NAMES}
        assert len(categories) == 1
        category = categories.pop()
        for name in registry.EDGE_ACTION_NAMES:
            assert f"- {name} [{category}]" in text
        other = {action.category for action in registry.REGISTRY.values()} - {category}
        for marker in other:
            assert f"[{marker}]" not in text


class TestActionLimits:
    """动作数量策略集中在 _ACTION_RANGE / _DOCKED_ACTION_RANGE。"""

    def test_docked_uses_shared_limit(self):
        for task in prompts._ACTION_RANGE:
            assert prompts._action_limit(task, True) == prompts._DOCKED_ACTION_LIMIT

    def test_undocked_uses_task_range(self):
        for task in prompts._ACTION_RANGE:
            low, high = prompts.action_range(task)
            label, text = prompts._action_limit(task, False)
            assert (label, text) != prompts._DOCKED_ACTION_LIMIT
            assert label == (f"{low}-{high}" if high else f"≥{low}")
            assert str(low) in text

    def test_autonomous_free_reads_min_action_count(self):
        n = prompts.min_action_count()
        label, text = prompts._action_limit("autonomous", False)
        assert label == f"≥{n}"
        assert str(n) in text

    @staticmethod
    def _action_lines(block: str) -> int:
        return sum(1 for line in block.splitlines() if line.strip().startswith("Action:"))

    def test_examples_match_action_limit(self):
        cases = [
            (prompts._autonomous_task, "autonomous", False),
            (prompts._chat_task, "chat", False),
            (prompts._analyze_task, "analyze", False),
            (prompts._interact_task, "interact", False),
            (prompts._chat_task, "chat", True),
        ]
        for fn, task, docked in cases:
            low, high = prompts.action_range(task, docked)
            count = self._action_lines(fn(docked=docked)[0])
            assert count >= low
            if high:
                assert count <= high

    def test_format_block_states_required_fields(self):
        block = prompts._chat_task(False)[0]
        assert "必填：第一行 Summary、至少 3 个 Action" in block

    def test_every_task_offers_vitals(self):
        for build in (prompts._autonomous_task, prompts._chat_task,
                      prompts._interact_task, prompts._analyze_task):
            sections = build(False)
            assert "Vitals(可选)" in sections[0]
            assert prompts._VITALS_GUIDE in sections

    def test_user_prompt_leaves_field_list_to_format_block(self):
        """字段顺序只在[输出格式]里写一次，user 段不再重复清单。"""
        prompt = prompts.chat_vision_user_prompt("在吗", "")
        assert "按[输出格式]写出完整输出" in prompt
        assert "Actions(至少" not in prompt

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
            before = prompts._tool_list()
            assert "web_search" not in before
            TOOL_REGISTRY.register("web_search", "测试：直连搜索 API", group="web")
            after = prompts._tool_list()
            assert "web_search" in after
            assert len(after.splitlines()) == len(before.splitlines()) + 2
        finally:
            TOOL_REGISTRY._tools.pop("web_search", None)
            if saved is not None:
                TOOL_REGISTRY._tools["web_search"] = saved
            TOOL_REGISTRY.set_enabled("web_search", saved_enabled)

    def test_disabled_tools_falls_back_to_aside(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_TOOLS_ENABLED", False)
        guide = prompts._tool_guide()
        assert guide == prompts._TOOL_ASIDE_GUIDE

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
            assert "fake_tool" in text
            assert "tool_search__list_groups" in text

    def test_interact_task_has_no_tool_list(self, monkeypatch):
        # 交互是即时反射，不带工具段
        monkeypatch.setattr(config, "LLM_TOOLS_ENABLED", True)
        text = "\n".join(prompts._interact_task())
        assert "tool_search" not in text
