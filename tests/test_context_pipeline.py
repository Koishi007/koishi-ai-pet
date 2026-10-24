"""上下文池测试：容量与淘汰、摘要与对话的席位分配。"""

import time

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


class TestTextSimilarity:
    def test_identical_text_scores_one(self):
        assert BrainMixin._text_similarity("今天天气不错", "今天天气不错") == pytest.approx(1.0)

    def test_unrelated_text_scores_low(self):
        assert BrainMixin._text_similarity("今天天气不错", "abcxyz") < 0.3

    def test_empty_side_scores_zero(self):
        assert BrainMixin._text_similarity("", "内容") == 0.0


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
            brain.add_context(role="user", content=f"消息{i}")
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
        monkeypatch.setattr(config, "CONTEXT_HISTORY_ENTRIES", 1)
        entries = (
            [_entry(f"摘要{i}", summary=True, age_s=100 + i) for i in range(2)]
            + [_entry(f"对话{i}", age_s=i) for i in range(8)]
        )
        brain = _brain(entries)
        brain._evict_context()

        remaining_normal = [e for e in brain._context if not e.is_summary]
        assert remaining_normal == []
        assert len(brain._pending_summary_queue) == 8


class TestInjectionCoversWholePool:
    """每轮注入的条数上限要覆盖池子的常态上界，否则池里"多出来的"那几条
    会被条数裁剪当轮排除、又够不上淘汰，形成"选不进也进不了摘要"的缺口。"""

    def test_pool_ceiling_is_eviction_soft_limit(self, monkeypatch):
        monkeypatch.setattr(config, "CONTEXT_HISTORY_ENTRIES", 5)
        brain = _brain([])
        assert brain._MAX_POOL_ENTRIES == 5 + BrainMixin._EVICT_BATCH_SIZE

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
