"""动作产出注册表测试：注册、查表、注入方式、结算时机，以及内置玩法的装配。"""

import pytest

from pet.action import outcome


class TestOutcomeRegistry:
    def test_unregistered_returns_none(self):
        assert outcome.outcome_for("no_such_action") is None

    def test_register_then_lookup(self, monkeypatch):
        monkeypatch.setitem(outcome._OUTCOMES, "_probe", outcome.Outcome(lambda: "旧的"))
        outcome.register("_probe", lambda: "新的")
        assert outcome.outcome_for("_probe").handler() == "新的"

    def test_once_defaults_to_true(self, monkeypatch):
        monkeypatch.setitem(outcome._OUTCOMES, "_probe", outcome.Outcome(lambda: "x"))
        outcome.register("_probe", lambda: "y")
        assert outcome.outcome_for("_probe").once is True

    def test_once_can_be_disabled(self, monkeypatch):
        monkeypatch.setitem(outcome._OUTCOMES, "_probe", outcome.Outcome(lambda: "x"))
        outcome.register("_probe", lambda: "y", once=False)
        assert outcome.outcome_for("_probe").once is False

    def test_registered_actions_includes_new(self, monkeypatch):
        monkeypatch.setitem(outcome._OUTCOMES, "_probe", outcome.Outcome(lambda: ""))
        assert "_probe" in outcome.registered_actions()

    def test_handler_may_return_empty(self, monkeypatch):
        # 空串表示本次无产出，调用方据此跳过注入
        monkeypatch.setitem(outcome._OUTCOMES, "_probe", outcome.Outcome(lambda: ""))
        assert not outcome.outcome_for("_probe").handler()


class TestTiming:
    def test_defaults_to_end(self, monkeypatch):
        monkeypatch.setitem(outcome._OUTCOMES, "_probe", outcome.Outcome(lambda: "x"))
        outcome.register("_probe", lambda: "y")
        assert outcome.outcome_for("_probe").timing == outcome.TIMING_END

    def test_can_settle_at_action_start(self, monkeypatch):
        monkeypatch.setitem(outcome._OUTCOMES, "_probe", outcome.Outcome(lambda: "x"))
        outcome.register("_probe", lambda: "y", timing=outcome.TIMING_START)
        assert outcome.outcome_for("_probe").timing == outcome.TIMING_START

    def test_unknown_timing_is_rejected(self, monkeypatch):
        monkeypatch.setitem(outcome._OUTCOMES, "_probe", outcome.Outcome(lambda: "x"))
        with pytest.raises(ValueError):
            outcome.register("_probe", lambda: "y", timing="middle")


class TestFishingIsWired:
    def test_fishing_registered_on_import(self):
        from pet.action import fishing  # noqa: F401

        assert "fishing" in outcome.registered_actions()

    def test_fishing_is_recent(self):
        from pet.action import fishing  # noqa: F401

        # 钓鱼是「结果」，进入最近事件窗口期供上下文反复引用
        assert outcome.outcome_for("fishing").once is False

    def test_fishing_effect_only_on_catch(self, monkeypatch):
        from pet.action import fishing

        spec = outcome.outcome_for("fishing")
        assert spec.effect is not None
        monkeypatch.setattr(fishing, "roll_catch", lambda: ("鲫鱼", "common"))
        spec.handler()
        assert spec.effect() == "fish"
        monkeypatch.setattr(fishing, "roll_catch", lambda: None)
        spec.handler()
        assert spec.effect() is None

    def test_fishing_handler_always_returns_text(self):
        from pet.action import fishing  # noqa: F401

        spec = outcome.outcome_for("fishing")
        assert spec is not None
        # 无论钓到与否都要有交代，不能返回空
        for _ in range(20):
            assert spec.handler().strip()


class TestCallingIsWired:
    def test_calling_registered_on_import(self):
        from pet.action import calling  # noqa: F401

        assert "calling" in outcome.registered_actions()

    def test_calling_settles_when_action_starts(self):
        from pet.action import calling  # noqa: F401

        # 铃一响就该通知，不能等通话动画播完
        assert outcome.outcome_for("calling").timing == outcome.TIMING_START

    def test_calling_notifies_without_injecting_text(self, monkeypatch):
        from pet.action import calling

        sent: list[tuple] = []
        monkeypatch.setattr(calling.TOOL_CTX, "notify",
                            lambda title, message, duration=5000: sent.append((title, message, duration)))

        assert calling.notify_call() is None  # 只通知，不往上下文注入事件
        assert sent == [(calling.NOTIFY_TITLE, calling.NOTIFY_TEXT, calling.NOTIFY_DURATION_MS)]
