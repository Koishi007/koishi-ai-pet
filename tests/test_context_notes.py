"""上下文备注章节测试：最近事件、未满足需求、作息困倦、旧事记忆、感受描述、窗口标题提取。"""

import re
import time
from types import SimpleNamespace

import pytest

from pet.brain.context_builder import ContextBuilder
from pet.config import config


class _FakeVitals:
    def __init__(self, satiety=100.0, energy=100.0):
        self._data = {"satiety": satiety, "energy": energy}

    def numeric_summary(self):
        return dict(self._data)


class _FakeMood:
    def __init__(self, joy=100.0, affection=100.0, sanity=100.0):
        self._data = {"joy": joy, "affection": affection, "sanity": sanity}

    def numeric_summary(self):
        return dict(self._data)


class _FakeMemoryStore:
    """只实现 ContextBuilder 用到的接口。"""

    def __init__(self, rows=()):
        self._rows = list(rows)

    def random_events(self, limit=2, exclude_ids=None):
        skip = set(exclude_ids or ())
        return [r for r in self._rows if r["id"] not in skip][:limit]

    def format_memory_time(self, created_at):
        return "3天前"

    def retrieve_context(self, user_message):
        return ""


class TestExtractWindowTitles:
    def test_empty_input(self):
        assert ContextBuilder._extract_window_titles("") == ""

    def test_titles_joined_and_noise_dropped(self):
        text = '1. "main.py - VSCode" ｜ 范围: 左0 上0 右100 下100\n2. "文档.md" ｜ 范围: 左0 上0 右50 下50'
        assert ContextBuilder._extract_window_titles(text) == "main.py - VSCode，文档.md"

    def test_falls_back_to_raw_text_without_quotes(self):
        assert ContextBuilder._extract_window_titles("没有标题的探测结果") == "没有标题的探测结果"


class TestRecentEventsNote:
    def _builder(self, events):
        return ContextBuilder(recent_events_fn=lambda: events)

    def test_no_provider_returns_empty(self):
        assert ContextBuilder()._recent_events_note() == ""

    def test_provider_exception_returns_empty(self):
        builder = ContextBuilder(recent_events_fn=lambda: 1 / 0)
        assert builder._recent_events_note() == ""

    def test_builtin_label_used_when_text_missing(self):
        now = time.time()
        note = self._builder([("grabbed", now, "")])._recent_events_note()
        assert "用户把你抓了起来" in note

    def test_custom_text_wins_over_label(self):
        now = time.time()
        note = self._builder([("timer", now, "你设的「吃药」定时器响了")])._recent_events_note()
        assert "吃药" in note

    def test_same_kind_keeps_latest_only(self):
        now = time.time()
        note = self._builder([
            ("head_pat", now - 100, "旧的一次摸头"),
            ("head_pat", now - 5, "新的一次摸头"),
        ])._recent_events_note()
        assert "新的一次摸头" in note
        assert "旧的一次摸头" not in note

    def test_events_outside_window_dropped(self, monkeypatch):
        monkeypatch.setattr(config, "RECENT_EVENT_WINDOW_S", 60)
        now = time.time()
        note = self._builder([("grabbed", now - 600, "")])._recent_events_note()
        assert note == ""

    def test_unknown_kind_without_text_skipped(self):
        assert self._builder([("mystery", time.time(), "")])._recent_events_note() == ""

    def test_events_ordered_oldest_first(self):
        now = time.time()
        note = self._builder([
            ("released", now - 1, "后发生的"),
            ("grabbed", now - 50, "先发生的"),
        ])._recent_events_note()
        assert note.index("先发生的") < note.index("后发生的")

    def test_max_lines_keeps_most_recent(self):
        now = time.time()
        # 7 条各有文案的有效事件，只有最近 5 条应被注入
        events = [(f"kind{i}", now - (7 - i), f"事件{i}") for i in range(7)]
        note = self._builder(events)._recent_events_note()
        assert len(note.splitlines()) == ContextBuilder._MAX_EVENT_LINES
        assert "事件0" not in note
        assert "事件1" not in note
        assert "事件6" in note


class TestOnceEvents:
    """一次性事件：注入一轮后即被消费，不留在窗口里反复出现。"""

    def _builder(self, once_events):
        """once_events 为可被消费的列表，模拟 PetAgent.take_once_events。"""
        def take():
            events, once_events[:] = list(once_events), []
            return events

        return ContextBuilder(recent_events_fn=lambda: [], once_events_fn=take)

    def test_injected_then_consumed(self):
        builder = self._builder([("fishing", time.time(), "你钓到了一条鲫鱼")])
        assert "你钓到了一条鲫鱼" in builder._recent_events_note()
        assert builder._recent_events_note() == ""

    def test_line_carries_time_prefix(self):
        # 一次性事件与窗口期事件同格式：[HH:MM] 文案
        builder = self._builder([("fishing", time.time(), "你钓到了一条鲫鱼")])
        line = builder._recent_events_note().splitlines()[0]
        assert re.fullmatch(r"\[\d{2}:\d{2}\] 你钓到了一条鲫鱼", line)

    def test_no_provider_returns_empty(self):
        assert ContextBuilder()._recent_events_note() == ""

    def test_provider_exception_ignored(self):
        builder = ContextBuilder(recent_events_fn=lambda: [], once_events_fn=lambda: 1 / 0)
        assert builder._recent_events_note() == ""

    def test_merged_with_window_events_in_time_order(self):
        now = time.time()
        builder = ContextBuilder(
            recent_events_fn=lambda: [("head_pat", now - 10, "")],
            once_events_fn=lambda: [("fishing", now, "你钓到了一条鲤鱼")],
        )
        note = builder._recent_events_note()
        assert note.index("用户摸了摸你的头") < note.index("你钓到了一条鲤鱼")

    def test_same_kind_not_deduped(self):
        # 每次钓鱼都是独立的一次经历，不该像窗口事件那样只留最近一条
        now = time.time()
        builder = ContextBuilder(
            recent_events_fn=lambda: [],
            once_events_fn=lambda: [
                ("fishing", now - 5, "你钓到了一条鲫鱼"),
                ("fishing", now, "你钓到了一条鲤鱼"),
            ],
        )
        note = builder._recent_events_note()
        assert "鲫鱼" in note
        assert "鲤鱼" in note


class TestNeedsNote:
    @pytest.fixture(autouse=True)
    def _no_circadian(self, monkeypatch):
        """默认屏蔽作息需求。

        作息由运行时刻（本地钟点）决定，会让断言随跑测时间漂移——CI 跑在 UTC，
        夜里会凭空多出「熬夜太久了」。需要测作息的用例自行覆盖 _circadian_need。
        """
        monkeypatch.setattr(ContextBuilder, "_circadian_need",
                            staticmethod(lambda hour, docked=False: None))

    def _builder(self, satiety=100, energy=100, joy=100, affection=100, sanity=100):
        return ContextBuilder(
            vitals=_FakeVitals(satiety, energy),
            mood=_FakeMood(joy, affection, sanity),
        )

    def test_no_vitals_or_mood_returns_empty(self):
        assert ContextBuilder()._build_needs_note() == ""
        assert ContextBuilder(vitals=_FakeVitals())._build_needs_note() == ""

    def test_all_satisfied_returns_empty(self):
        assert self._builder()._build_needs_note() == ""

    def test_threshold_boundary_excluded(self):
        # 阈值 60：等于阈值不算未满足
        note = self._builder(satiety=60)._build_needs_note()
        assert note == ""
        note = self._builder(satiety=59)._build_needs_note()
        assert "吃点东西" in note

    def test_all_four_needs_mapped(self):
        note = self._builder(satiety=10, energy=10, joy=10, affection=10)._build_needs_note()
        for label in ("吃点东西", "歇一歇", "找点乐子", "想被陪陪"):
            assert label in note

    def test_first_occurrence_marked_as_new(self):
        note = self._builder(satiety=10)._build_needs_note()
        assert "刚起念" in note

    def test_elapsed_time_reported(self):
        builder = self._builder(satiety=10)
        builder._active_needs["hungry"] = time.time() - 180
        note = builder._build_needs_note()
        assert "已持续 3 分钟" in note

    def test_recovered_need_removed(self):
        builder = self._builder(satiety=10)
        assert "吃点东西" in builder._build_needs_note()
        builder._vitals = _FakeVitals(satiety=100)
        assert builder._build_needs_note() == ""
        assert builder._active_needs == {}

    def test_exception_from_vitals_returns_empty(self):
        broken = SimpleNamespace(numeric_summary=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        builder = ContextBuilder(vitals=broken, mood=_FakeMood())
        assert builder._build_needs_note() == ""

    def test_low_sanity_mapped_to_need(self):
        note = self._builder(sanity=5)._build_needs_note()
        assert "脑子有点乱" in note
        assert "摸摸头" in note

    def test_sanity_uses_critical_threshold(self):
        t = config.SANITY_CRITICAL_THRESHOLD
        assert "脑子有点乱" not in self._builder(sanity=t)._build_needs_note()
        assert "脑子有点乱" in self._builder(sanity=t - 1)._build_needs_note()

    def test_line_carries_action_hint(self):
        note = self._builder(satiety=10)._build_needs_note()
        assert "自己生成食物" in note

    def test_food_disabled_hint_only_begs_user(self, monkeypatch):
        monkeypatch.setattr(config, "FOOD_ENABLED", False)
        note = self._builder(satiety=10)._build_needs_note()
        assert "讨点吃的" in note
        assert "生成食物" not in note

    def test_interact_task_excludes_needs_block(self):
        builder = self._builder(satiety=10)
        assert "你惦记着的事" not in builder._build_system("interact", "interact")
        assert "你惦记着的事" in builder._build_system("chat_non_vision", "chat")

    def test_bedtime_line_wired_into_note(self, monkeypatch):
        monkeypatch.setattr(ContextBuilder, "_circadian_need",
                            staticmethod(lambda hour, docked=False: ("bedtime", "困了", "去睡")))
        assert "困了（刚起念）→ 去睡" in self._builder()._build_needs_note()

    def test_bedtime_suppresses_tired(self, monkeypatch):
        monkeypatch.setattr(ContextBuilder, "_circadian_need",
                            staticmethod(lambda hour, docked=False: ("bedtime", "困了", "去睡")))
        note = self._builder(energy=10)._build_needs_note()
        assert "困了" in note
        assert "歇一歇" not in note

    def test_tired_kept_outside_bedtime(self, monkeypatch):
        monkeypatch.setattr(ContextBuilder, "_circadian_need", staticmethod(lambda hour, docked=False: None))
        assert "歇一歇" in self._builder(energy=10)._build_needs_note()

    def test_bedtime_cleared_after_window(self, monkeypatch):
        builder = self._builder()
        monkeypatch.setattr(ContextBuilder, "_circadian_need",
                            staticmethod(lambda hour, docked=False: ("bedtime", "困了", "去睡")))
        assert "困了" in builder._build_needs_note()
        monkeypatch.setattr(ContextBuilder, "_circadian_need", staticmethod(lambda hour, docked=False: None))
        assert builder._build_needs_note() == ""
        assert builder._active_needs == {}


class TestCircadianNeed:
    @pytest.mark.parametrize("hour,label", [
        (23, "困了"), (0, "困了"), (1, "熬夜太久了"), (5, "熬夜太久了"), (13, "犯困"),
    ])
    def test_window_labels(self, hour, label):
        assert ContextBuilder._circadian_need(hour)[1] == label

    @pytest.mark.parametrize("hour", [6, 9, 12, 14, 18, 22])
    def test_outside_windows_returns_none(self, hour):
        assert ContextBuilder._circadian_need(hour) is None

    def test_night_stages_share_key(self):
        # key 不变，「已持续」才能累计成熬夜时长
        assert (ContextBuilder._circadian_need(23)[0]
                == ContextBuilder._circadian_need(3)[0] == "bedtime")

    def test_hints_point_to_sleep_action(self):
        for hour in (23, 3, 13):
            assert "sleep" in ContextBuilder._circadian_need(hour)[2]


class TestFeelingNote:
    def _builder(self, satiety=100, energy=100, joy=100, affection=100, sanity=100):
        return ContextBuilder(
            vitals=_FakeVitals(satiety, energy),
            mood=_FakeMood(joy, affection, sanity),
        )

    def test_action_guides_only_in_needs_section(self):
        feeling = self._builder(satiety=10, energy=10, joy=10)._build_feeling()
        assert "food__spawn" not in feeling
        assert "讨" not in feeling

    def test_sanity_feeling_keeps_safety_norm(self):
        feeling = self._builder(sanity=3)._build_feeling()
        assert "绝不写或覆盖文件" in feeling
        assert "摸摸头" not in feeling


class TestEventMemoryNote:
    def _row(self, mid, content):
        return {"id": mid, "content": content, "created_at": ""}

    def _builder(self, rows):
        return ContextBuilder(memory_store=_FakeMemoryStore(rows))

    def test_no_store_returns_empty(self):
        assert ContextBuilder()._memory_event_note() == ""

    def test_disabled_by_config(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_EVENT_RECALL_COUNT", 0)
        assert self._builder([self._row(1, "旧事")])._memory_event_note() == ""

    def test_empty_memory_returns_empty(self):
        assert self._builder([])._memory_event_note() == ""

    def test_store_exception_returns_empty(self):
        broken = SimpleNamespace(random_events=lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
        assert ContextBuilder(memory_store=broken)._memory_event_note() == ""

    def test_line_carries_content_and_age(self):
        note = self._builder([self._row(1, "用户上周带猫去看了医生")])._memory_event_note()
        assert "- 用户上周带猫去看了医生（3天前）" in note

    def test_previous_round_ids_skipped(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_EVENT_RECALL_COUNT", 1)
        builder = self._builder([self._row(1, "旧事一"), self._row(2, "旧事二")])
        assert "旧事一" in builder._memory_event_note()
        assert "旧事二" in builder._memory_event_note()

    def test_candidates_exhausted_falls_back_to_repeat(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_EVENT_RECALL_COUNT", 2)
        builder = self._builder([self._row(1, "唯一旧事")])
        builder._memory_event_note()
        assert "唯一旧事" in builder._memory_event_note()

    def test_injected_into_needs_section(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_EVENT_RECALL_COUNT", 1)
        builder = ContextBuilder(
            vitals=_FakeVitals(satiety=10), mood=_FakeMood(),
            memory_store=_FakeMemoryStore([self._row(1, "用户上周带猫去看了医生")]),
        )
        system = builder._build_system("chat_non_vision", "chat")
        head = system.index("[你惦记着的事]")
        assert system.index("吃点东西") > head
        assert system.index("用户上周带猫去看了医生") > head

    def test_interact_task_excludes_memory_note(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_EVENT_RECALL_COUNT", 1)
        builder = ContextBuilder(
            vitals=_FakeVitals(), mood=_FakeMood(),
            memory_store=_FakeMemoryStore([self._row(1, "唯一旧事")]),
        )
        assert "唯一旧事" not in builder._build_system("interact", "interact")
