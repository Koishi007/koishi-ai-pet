"""拖入文件的附件通道测试：正文只进当轮、图片替代截图、模板内容与数值规则。"""

import pytest

from pet.brain.context_builder import ContextBuilder
from pet.brain.prompts import interact_take_a_bite_prompt, interact_file_reject_prompt
from pet.config import config


class _BrainStub:
    """只提供上下文组装用到的两处接口。"""

    _MAX_POOL_ENTRIES = 20

    def __init__(self, history=None):
        self._history = history or []

    def get_multi_turn_messages(self, max_entries=10, skip_last=0, token_budget=0):
        return list(self._history)


class _ScreenStub:
    """按是否传入图片区分：附件编码与截图编码返回不同标记。"""

    def prepare_image(self, image=None, vision_scale=1.0, min_px=1536):
        return "ATTACH_B64" if image is not None else "SCREEN_B64"


def _builder(history=None) -> ContextBuilder:
    return ContextBuilder(memory_store=None, screen_reader=_ScreenStub(),
                          vitals=None, mood=None, brain_mixin=_BrainStub(history))


def _last_text(messages) -> str:
    content = messages[-1]["content"]
    if isinstance(content, str):
        return content
    return content[0]["text"]


class TestChatAttachment:
    def test_body_only_in_current_turn(self):
        history = [{"role": "user", "content": "上一轮的对话"}]
        messages = _builder(history).build_chat_decide(
            "看看这个文件", "无窗口", attachment_text="MARKER")

        assert "MARKER" in _last_text(messages)
        assert messages[1]["content"] == "上一轮的对话"
        assert all("MARKER" not in str(message.get("content")) for message in messages[1:-1])

    def test_attachment_image_replaces_screenshot(self, monkeypatch):
        monkeypatch.setattr(config, "VISION_ENABLED", True)
        messages = _builder().build_chat_decide(
            "看图", "无窗口", attachment_image=object())

        parts = messages[-1]["content"]
        assert isinstance(parts, list)
        assert "ATTACH_B64" in parts[1]["image_url"]["url"]
        assert "SCREEN_B64" not in str(parts)

    def test_screenshot_used_without_attachment(self, monkeypatch):
        monkeypatch.setattr(config, "VISION_ENABLED", True)
        messages = _builder().build_chat_decide("看屏幕", "无窗口", screenshot=True)
        assert "SCREEN_B64" in str(messages[-1]["content"])

    def test_attachment_image_dropped_without_vision(self, monkeypatch):
        monkeypatch.setattr(config, "VISION_ENABLED", False)
        messages = _builder().build_chat_decide("看图", "无窗口", attachment_image=object())
        assert isinstance(messages[-1]["content"], str)


class TestInteractAttachment:
    def test_body_wrapped_as_material(self):
        messages = _builder().build_interact("尝尝看", attachment_text="MARKER")
        content = messages[-1]["content"]
        assert "MARKER" in content
        assert "观察资料" in content

    def test_attachment_image_added(self, monkeypatch):
        monkeypatch.setattr(config, "VISION_ENABLED", True)
        messages = _builder().build_interact("尝尝看", attachment_image=object())
        parts = messages[-1]["content"]
        assert isinstance(parts, list)
        assert "ATTACH_B64" in parts[1]["image_url"]["url"]

    def test_attachment_image_dropped_without_vision(self, monkeypatch):
        monkeypatch.setattr(config, "VISION_ENABLED", False)
        messages = _builder().build_interact("尝尝看", attachment_image=object())
        assert messages[-1]["content"] == "尝尝看"

    def test_no_attachment_keeps_plain_text(self):
        messages = _builder().build_interact("尝尝看")
        assert messages[-1]["content"] == "尝尝看"


class TestPrompts:
    def test_taste_prompt_is_taste_and_mood(self):
        text = interact_take_a_bite_prompt("报告.md、图.png")
        assert "报告.md" in text
        assert "味道" in text
        assert "joy" in text and "sanity" in text
        assert "satiety" not in text and "energy" not in text

    def test_taste_template_override(self, monkeypatch):
        monkeypatch.setattr(config, "INTERACT_TAKE_A_BITE_PROMPT", "只认「{names}」")
        assert interact_take_a_bite_prompt("a.txt") == "只认「a.txt」"


class TestAnalyzeTask:
    def test_system_uses_analyze_rules(self):
        system = _builder().build_analyze_decide(
            "用户把文件交给了你：\n- a.txt", "无窗口")[0]["content"]
        assert "分析模式" in system
        assert "至少 3 个 Action" in system

    def test_user_prompt_is_analyze_variant(self):
        messages = _builder().build_analyze_decide(
            "用户把文件交给了你：\n- a.txt", "无窗口")
        text = _last_text(messages)
        assert "=== 用户交给你看的东西 ===" in text
        assert "a.txt" in text

    def test_no_quote_guard_lives_in_analyze_segments(self):
        """不复述原文的约束由 analyze 段承担：用户段第 3 步与系统段第 2 条。"""
        messages = _builder().build_analyze_decide("元信息", "无窗口")
        assert "不要复述原文" in _last_text(messages)
        assert "不逐句复述" in messages[0]["content"]

    def test_body_wrapped_as_material(self):
        messages = _builder().build_analyze_decide("元信息", "无窗口", attachment_text="MARKER")
        text = _last_text(messages)
        assert "MARKER" in text
        assert "观察资料" in text

    def test_no_screenshot_with_vision(self, monkeypatch):
        monkeypatch.setattr(config, "VISION_ENABLED", True)
        messages = _builder().build_analyze_decide("元信息", "无窗口")
        assert isinstance(messages[-1]["content"], str)
        assert "SCREEN_B64" not in str(messages)

    def test_attachment_image_goes_through(self, monkeypatch):
        monkeypatch.setattr(config, "VISION_ENABLED", True)
        messages = _builder().build_analyze_decide("元信息", "无窗口", attachment_image=object())
        parts = messages[-1]["content"]
        assert isinstance(parts, list)
        assert "ATTACH_B64" in parts[1]["image_url"]["url"]

    def test_reject_prompt_differs_by_reason(self):
        replies = {reason: interact_file_reject_prompt(reason)
                   for reason in ("too_large", "too_many", "forbidden")}
        assert len(set(replies.values())) == 3
        assert "Vitals 不变" in replies["too_large"]
        assert "joy-0~2" in replies["forbidden"]
        assert "affection" in replies["forbidden"]

    def test_reject_prompt_lists_names(self):
        single = interact_file_reject_prompt("too_large", ("big.zip",))
        assert "「big.zip」太大了" in single

        batch = interact_file_reject_prompt("too_many", tuple(f"f{i}.txt" for i in range(6)))
        assert "f0.txt" in batch and "f2.txt" in batch
        assert "f3.txt" not in batch
        assert "等 6 个" in batch

    def test_reject_prompt_unknown_reason_falls_back(self):
        assert "没有接住" in interact_file_reject_prompt("something_else")

    def test_reject_template_override(self, monkeypatch):
        monkeypatch.setattr(config, "INTERACT_FILE_REJECT_PROMPT", "固定台词 {reason} {names}")
        assert interact_file_reject_prompt("too_large", ("big.zip",)) == "固定台词 too_large big.zip"
