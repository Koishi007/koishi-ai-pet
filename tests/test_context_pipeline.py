"""上下文裁剪测试：时间格式化、条数淘汰、token 预算、system 归位。"""

import time
from datetime import datetime, timedelta

import pytest

from pet.brain.base import BrainMixin, ContextEntry
from pet.brain.context_builder import ContextBuilder
from pet.config import config


def _brain(entries) -> BrainMixin:
    """db_path=None → 不落库，仅使用内存上下文。"""
    obj = BrainMixin(db_path=None)
    obj._context = list(entries)
    return obj


def _entry(content: str, role: str = "assistant", age_s: float = 0.0, summary: bool = False):
    return ContextEntry(role=role, content=content,
                        timestamp=time.time() - age_s, is_summary=summary)


class TestEstimateTokens:
    def test_chinese_counted_heavier(self):
        # 10 个汉字 → 15 token
        assert BrainMixin._estimate_tokens("汉字" * 5) == 15

    def test_ascii_lighter(self):
        assert BrainMixin._estimate_tokens("a" * 100) == 25

    def test_empty_text_at_least_one(self):
        assert BrainMixin._estimate_tokens("") == 1


class TestFormatContextTime:
    def test_today_uses_clock_only(self):
        now = datetime.now().replace(hour=9, minute=30)
        assert BrainMixin._format_context_time(now.timestamp()) == "09:30"

    def test_yesterday_and_before_yesterday(self):
        yesterday = datetime.now() - timedelta(days=1)
        assert BrainMixin._format_context_time(yesterday.timestamp()).startswith("昨天 ")
        two_days = datetime.now() - timedelta(days=2)
        assert BrainMixin._format_context_time(two_days.timestamp()).startswith("前天 ")

    def test_older_uses_date(self):
        older = datetime.now() - timedelta(days=5)
        assert len(BrainMixin._format_context_time(older.timestamp())) == len("09-01 09:30")


class TestGetMultiTurnMessages:
    def test_empty_context(self):
        assert _brain([]).get_multi_turn_messages() == []

    def test_keeps_chronological_order_with_time_prefix(self):
        entries = [
            _entry("第二条", age_s=10),
            _entry("第一条", age_s=100),
        ]
        messages = _brain(entries).get_multi_turn_messages()
        assert [m["content"].split("] ")[1] for m in messages] == ["第一条", "第二条"]

    def test_count_eviction_drops_oldest_dialogue_first(self):
        entries = [_entry(f"对话{i}", age_s=1000 - i * 10) for i in range(6)]
        entries.append(_entry("旧摘要", summary=True, age_s=5000))
        messages = _brain(entries).get_multi_turn_messages(max_entries=4)
        contents = [m["content"] for m in messages]
        # 摘要与最新的对话保留，最旧的对话先被丢弃
        assert any("旧摘要" in c for c in contents)
        assert not any("对话0" in c for c in contents)
        assert len(messages) == 4

    def test_summary_and_system_keep_system_role(self):
        entries = [_entry("摘要", summary=True), _entry("备注", role="system")]
        messages = _brain(entries).get_multi_turn_messages()
        assert all(m["role"] == "system" for m in messages)

    def test_skip_last_excludes_tail(self):
        entries = [_entry("保留", age_s=100), _entry("丢弃", age_s=1)]
        messages = _brain(entries).get_multi_turn_messages(skip_last=1)
        assert [m["content"].split("] ")[1] for m in messages] == ["保留"]

    def test_token_budget_truncates_but_keeps_one(self):
        entries = [_entry("字" * 200, age_s=1000 - i * 10) for i in range(5)]
        messages = _brain(entries).get_multi_turn_messages(max_entries=10, token_budget=100)
        assert len(messages) == 1


class TestTextSimilarity:
    def test_identical_text_scores_one(self):
        assert BrainMixin._text_similarity("今天天气不错", "今天天气不错") == pytest.approx(1.0)

    def test_unrelated_text_scores_low(self):
        assert BrainMixin._text_similarity("今天天气不错", "abcxyz") < 0.3

    def test_empty_side_scores_zero(self):
        assert BrainMixin._text_similarity("", "内容") == 0.0


class TestToolCallDowngrade:
    def test_tool_call_entries_scored_below_dialogue(self):
        brain = _brain([])
        tool_call = _entry("[工具调用] weather__get_current", age_s=10)
        dialogue = _entry("普通对话", age_s=10)
        assert brain._score_entry(tool_call) < brain._score_entry(dialogue)


class TestMergeSystemHistory:
    def test_system_notes_merged_into_first_system_message(self):
        builder = object.__new__(ContextBuilder)
        merged = builder._merge_system_history(
            "系统提示",
            [{"role": "system", "content": "备注一"},
             {"role": "user", "content": "你好"},
             {"role": "assistant", "content": "在的"}],
        )
        assert merged[0]["role"] == "system"
        assert "系统提示" in merged[0]["content"]
        assert "备注一" in merged[0]["content"]
        assert [m["content"] for m in merged[1:]] == ["你好", "在的"]

    def test_no_notes_keeps_system_untouched(self):
        builder = object.__new__(ContextBuilder)
        merged = builder._merge_system_history("系统提示", [{"role": "user", "content": "嗨"}])
        assert merged[0]["content"] == "系统提示"


class TestPoolCapUnified:
    """池子容量与每轮注入上限同源：都取 CONTEXT_HISTORY_ENTRIES。"""

    def test_max_entries_follows_history_entries_config(self, monkeypatch):
        monkeypatch.setattr(config, "CONTEXT_HISTORY_ENTRIES", 5)
        brain = _brain([])
        assert brain._MAX_ENTRIES == 5

    def test_pool_size_stays_bounded_by_history_entries(self, monkeypatch):
        monkeypatch.setattr(config, "CONTEXT_HISTORY_ENTRIES", 5)
        brain = _brain([])
        for i in range(20):
            brain.add_context(role="assistant", content=f"消息{i}")
        # 池子上界 = 配置值 + 批量淘汰软上限（见 spec §3）；
        # 断言直接对照配置值而非 brain._MAX_ENTRIES，避免用被测实现自证其行为。
        assert brain.context_count() <= config.CONTEXT_HISTORY_ENTRIES + BrainMixin._EVICT_BATCH_SIZE

    def test_orphan_config_keys_removed(self):
        with pytest.raises(AttributeError):
            config.CONTEXT_MAX_ENTRIES
        with pytest.raises(AttributeError):
            config.CONTEXT_MAX_SUMMARIES

    def test_max_summaries_property_removed(self):
        assert not hasattr(BrainMixin, "_MAX_SUMMARIES")


class TestEvictContextNegativeBaseLimit:
    """摘要与工具调用的预留席位超过上限时，普通对话要全部被淘汰进摘要队列，
    不能因为 base_limit 变负而被负数切片误保留。"""

    def test_normal_chats_all_queued_when_reserved_slots_exceed_cap(self, monkeypatch):
        monkeypatch.setattr(config, "CONTEXT_HISTORY_ENTRIES", 3)
        entries = (
            [_entry(f"摘要{i}", summary=True, age_s=100 + i) for i in range(2)]
            + [_entry(f"[工具调用] tool{i}", age_s=50 + i) for i in range(2)]
            + [_entry(f"对话{i}", age_s=i) for i in range(8)]
        )
        brain = _brain(entries)
        brain._evict_context()

        remaining_normal = [
            e for e in brain._context
            if not e.is_summary and not e.content.startswith("[工具调用]")
        ]
        assert remaining_normal == []
        assert len(brain._pending_summary_queue) == 8


class TestInjectionCoversWholePool:
    """每轮注入的条数上限要覆盖池子的常态上界，否则池里"多出来的"那几条
    会被条数裁剪当轮排除、又够不上淘汰，形成"选不进也进不了摘要"的缺口。"""

    def test_pool_ceiling_is_eviction_soft_limit(self, monkeypatch):
        monkeypatch.setattr(config, "CONTEXT_HISTORY_ENTRIES", 5)
        brain = _brain([])
        assert brain._MAX_POOL_ENTRIES == 5 + BrainMixin._EVICT_BATCH_SIZE

    def test_whole_pool_is_injected_in_normal_regime(self, monkeypatch):
        """摘要与工具调用不挤占配额时，池子不会超过上界，池内条目应全部入选本轮。"""
        monkeypatch.setattr(config, "CONTEXT_HISTORY_ENTRIES", 5)
        brain = _brain([])
        for i in range(20):
            brain.add_context(role="assistant", content=f"消息{i}")

        # 先确认确实处在震荡区间（池子 > _MAX_ENTRIES），否则这条断言没有意义
        assert brain._MAX_ENTRIES < brain.context_count() <= brain._MAX_POOL_ENTRIES
        messages = brain.get_multi_turn_messages(max_entries=brain._MAX_POOL_ENTRIES)
        assert len(messages) == brain.context_count()

    def test_builders_request_whole_pool(self, monkeypatch):
        """两个多轮构建入口传的必须是池子上界 _MAX_POOL_ENTRIES。"""
        monkeypatch.setattr(config, "CONTEXT_HISTORY_ENTRIES", 5)
        brain = _brain([])
        seen: list[int] = []

        def spy(*args, **kwargs):
            seen.append(kwargs["max_entries"])
            return []

        monkeypatch.setattr(brain, "get_multi_turn_messages", spy)

        builder = object.__new__(ContextBuilder)
        builder._brain = brain
        builder._build_multi_turn_autonomous("系统提示", "", False, None)
        builder._build_multi_turn_chat("系统提示", "你好", "", False, None)

        assert seen == [5 + BrainMixin._EVICT_BATCH_SIZE] * 2
