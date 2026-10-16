"""输出预算：统一硬上限 + 动态配额（基线 / 思考预留 / 工具预留）。"""

import pytest

import pet.brain.budget as budget
from pet.brain.budget import output_budget, thinking_active
from pet.config import config


class TestThinkingActive:
    """思考状态的解析规则要和请求参数注入保持一致。"""

    def test_none_follows_global_switch(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_THINKING_DISABLED", False)
        assert thinking_active(None) is True
        monkeypatch.setattr(config, "LLM_THINKING_DISABLED", True)
        assert thinking_active(None) is False

    def test_explicit_value_overrides_global_switch(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_THINKING_DISABLED", True)
        assert thinking_active(True) is True
        monkeypatch.setattr(config, "LLM_THINKING_DISABLED", False)
        assert thinking_active(False) is False


class TestOutputBudget:
    def test_interact_base_is_small(self):
        assert output_budget("interact", thinking=False) == 512

    def test_thinking_reserve_is_added(self):
        assert output_budget("interact", thinking=True) == 2048

    def test_decide_modes_share_the_larger_tier(self):
        for mode in ("chat", "analyze", "autonomous"):
            assert output_budget(mode, thinking=False) == 2048
            assert output_budget(mode, thinking=True) == 6144

    def test_tools_add_reserve(self):
        assert output_budget("autonomous", thinking=True, has_tools=True) == 8192
        assert output_budget("autonomous", thinking=False, has_tools=True) == 4096

    def test_summary_stays_small(self):
        assert output_budget("summary", thinking=True) == 1024

    def test_thinking_none_counts_as_enabled_by_default(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_THINKING_DISABLED", False)
        assert output_budget("chat") == 6144

    def test_unknown_mode_is_a_programming_error(self):
        with pytest.raises(KeyError):
            output_budget("nope")

    def test_clamped_by_ceiling(self, monkeypatch):
        monkeypatch.setitem(budget._MODES, "chat", (budget._CEILING, 4096))
        assert output_budget("chat", thinking=True) == budget._CEILING

    @pytest.mark.parametrize("mode", ["interact", "chat", "analyze", "autonomous", "summary"])
    def test_never_exceeds_ceiling(self, mode):
        assert output_budget(mode, thinking=True, has_tools=True) <= budget._CEILING
