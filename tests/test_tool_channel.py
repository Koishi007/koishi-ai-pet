"""工具通道降档：顺序、判定、缓存与消息降级形态。"""

import pytest
from openai import BadRequestError, UnprocessableEntityError

from pet.brain.projection import degrade_messages
from pet.brain.tool_channel import (next_channel, is_tool_rejection, mark_downgraded,
                                    reset_cache, resolve_channel, channel_for)


def _error(cls, message, status=400):
    return cls(message=message, response=__import__("httpx").Response(
        status_code=status, request=__import__("httpx").Request("POST", "http://x")),
        body={"message": message})


class TestOrder:
    def test_downgrade_walks_to_none(self):
        assert next_channel("native") == "user-note"
        assert next_channel("user-note") == "system-note"
        assert next_channel("system-note") == "none"
        assert next_channel("none") == "none"


class TestRejection:
    def test_rejects_unsupported_tool_role(self):
        assert is_tool_rejection(_error(BadRequestError, "role 'tool' is not supported"))

    def test_rejects_extra_fields(self):
        assert is_tool_rejection(_error(UnprocessableEntityError,
                                        "tool_calls: Extra inputs are not permitted", 422))

    def test_ignores_missing_function_name(self):
        assert not is_tool_rejection(
            _error(BadRequestError, "messages[3].tool_calls[0] is missing a function name"))

    def test_ignores_unrelated_400(self):
        assert not is_tool_rejection(_error(BadRequestError, "temperature out of range"))

    def test_ignores_server_error(self):
        assert not is_tool_rejection(_error(BadRequestError, "tool not supported", 500))


class TestCache:
    def setup_method(self):
        reset_cache()

    def teardown_method(self):
        reset_cache()

    def test_default_channel_is_native(self, monkeypatch):
        from pet.config import config
        monkeypatch.setattr(config, "LLM_TOOL_ROLE_MODE", "auto")
        assert resolve_channel() == "native"

    def test_downgrade_is_remembered(self, monkeypatch):
        from pet.config import config
        monkeypatch.setattr(config, "LLM_TOOL_ROLE_MODE", "auto")
        assert mark_downgraded("native") == "user-note"
        assert resolve_channel() == "user-note"

    def test_explicit_mode_wins_over_cache(self, monkeypatch):
        from pet.config import config
        monkeypatch.setattr(config, "LLM_TOOL_ROLE_MODE", "auto")
        mark_downgraded("native")
        monkeypatch.setattr(config, "LLM_TOOL_ROLE_MODE", "native")
        assert channel_for() == "native"

    def test_override_config(self, monkeypatch):
        from pet.config import config
        monkeypatch.setattr(config, "LLM_TOOL_ROLE_MODE", "auto")
        monkeypatch.setattr(config, "LLM_TOOL_ROLE_OVERRIDE", "qwen2.5=system-note")
        monkeypatch.setattr(config, "LLM_MODEL", "qwen2.5-7b")
        assert resolve_channel() == "system-note"


class TestDegradeMessages:
    def _native(self):
        return [
            {"role": "user", "content": "帮我看视频"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "bilibili__like", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "点赞成功"},
            {"role": "user", "content": "好的"},
        ]

    def test_native_pair_becomes_user_note(self):
        out = degrade_messages(self._native(), "user-note")
        assert all(m["role"] != "tool" for m in out)
        assert all("tool_calls" not in m for m in out)
        assert out[1]["role"] == "user"
        assert "点赞成功" in out[1]["content"]
        assert out[-1]["content"] == "好的"

    def test_system_note_uses_system_role(self):
        out = degrade_messages(self._native(), "system-note")
        assert out[1]["role"] == "system"

    def test_none_channel_drops_records(self):
        out = degrade_messages(self._native(), "none")
        assert [m["role"] for m in out] == ["user", "user"]

    def test_keeps_assistant_text_when_present(self):
        messages = self._native()
        messages[1]["content"] = "我去点赞"
        out = degrade_messages(messages, "user-note")
        assert out[1] == {"role": "assistant", "content": "我去点赞"}
        assert out[2]["role"] == "user"
