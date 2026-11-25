"""工具通道降档：顺序、判定、缓存与消息降级形态。"""

from types import SimpleNamespace

import pytest

from pet.brain.llm_errors import ProviderBadRequest
from pet.brain.llm_gateway import LlmGateway
from pet.brain.projection import degrade_messages, uses_native_tools
from pet.brain.tool_channel import (ToolChannelState, default_state, is_tool_rejection,
                                    next_channel)


def _error(message, status=400):
    """构造中性 400 错误，body 里带错误消息。"""
    return ProviderBadRequest(message, status_code=status,
                              body=f'{{"message": "{message}"}}')


class TestOrder:
    def test_downgrade_walks_to_none(self):
        assert next_channel("native") == "user-note"
        assert next_channel("user-note") == "system-note"
        assert next_channel("system-note") == "none"
        assert next_channel("none") == "none"


class TestRejection:
    def test_rejects_unsupported_tool_role(self):
        assert is_tool_rejection(_error("role 'tool' is not supported"))

    def test_rejects_extra_fields(self):
        assert is_tool_rejection(_error("tool_calls: Extra inputs are not permitted", 422))

    def test_ignores_missing_function_name(self):
        assert not is_tool_rejection(
            _error("messages[3].tool_calls[0] is missing a function name"))

    def test_ignores_unrelated_400(self):
        assert not is_tool_rejection(_error("temperature out of range"))

    def test_ignores_server_error(self):
        assert not is_tool_rejection(_error("tool not supported", 500))

    def test_ignores_non_provider_error(self):
        assert not is_tool_rejection(ValueError("role 'tool' is not supported"))

    def test_recognizes_gemini_schema_rejection(self):
        """Gemini 报未知字段用 function_declarations / parameters_json_schema。"""
        assert is_tool_rejection(_error(
            "function_declarations[0].parameters_json_schema: Unknown field default"),
            "gemini")

    def test_recognizes_gemini_role_rejection(self):
        """Gemini 用「角色只能是 user 与 model」表达工具历史不可用。"""
        assert is_tool_rejection(_error(
            "Please use a valid role: user, model. invalid role"), "gemini")

    def test_model_word_alone_is_not_a_tool_rejection(self):
        """「模型不支持某参数」里的 model 不能当成工具字段。"""
        for text in (
            "thinking_level is not supported for this model",
            "The model gemini-2.5-flash is not supported for generateContent",
        ):
            assert not is_tool_rejection(_error(text), "gemini")

    def test_unknown_channel_matches_both_vocabularies(self):
        cases = [
            "tool_calls: Extra inputs are not permitted",
            "role 'tool' is not supported",
            "function_declarations[0].parameters_json_schema: Unknown field default",
        ]
        assert all(is_tool_rejection(_error(text)) for text in cases)


class TestCache:
    """降档缓存由 ToolChannelState 持有，不落模块级全局。"""

    def _state(self, scope=("openai", "m1"), native_tools=None):
        return ToolChannelState(lambda: scope, native_tools)

    def setup_method(self):
        default_state().reset()

    def teardown_method(self):
        default_state().reset()

    def test_default_channel_is_native(self, monkeypatch):
        from pet.config import config
        monkeypatch.setattr(config, "LLM_TOOL_ROLE_MODE", "auto")
        assert self._state().resolve() == "native"

    def test_downgrade_is_remembered(self, monkeypatch):
        from pet.config import config
        monkeypatch.setattr(config, "LLM_TOOL_ROLE_MODE", "auto")
        state = self._state()
        assert state.mark_downgraded("native") == "user-note"
        assert state.resolve() == "user-note"

    def test_explicit_mode_wins_over_cache(self, monkeypatch):
        from pet.config import config
        monkeypatch.setattr(config, "LLM_TOOL_ROLE_MODE", "auto")
        state = self._state()
        state.mark_downgraded("native")
        monkeypatch.setattr(config, "LLM_TOOL_ROLE_MODE", "native")
        assert state.channel() == "native"

    def test_state_is_isolated_per_instance(self, monkeypatch):
        """两套方案各持一份状态，互不影响。"""
        from pet.config import config
        monkeypatch.setattr(config, "LLM_TOOL_ROLE_MODE", "auto")
        primary = self._state(("openai", "primary-model"))
        alternative = self._state(("gemini", "alt-model"))

        assert primary.mark_downgraded("native") == "user-note"
        assert primary.resolve() == "user-note"
        assert alternative.resolve() == "native"

    def test_scope_change_hits_another_cache_entry(self, monkeypatch):
        """同一实例内切换生效方案时，缓存键随之改变。"""
        from pet.config import config
        monkeypatch.setattr(config, "LLM_TOOL_ROLE_MODE", "auto")
        scope = ["openai", "m1"]
        state = ToolChannelState(lambda: tuple(scope))

        assert state.mark_downgraded("native") == "user-note"
        scope[:] = ["gemini", "m2"]
        assert state.resolve() == "native"

    def test_initial_channel_follows_native_tools_capability(self, monkeypatch):
        """通道声明不支持原生工具时，未探测过的初始档位是 user-note。"""
        from pet.config import config
        monkeypatch.setattr(config, "LLM_TOOL_ROLE_MODE", "auto")
        assert self._state(native_tools=lambda: False).resolve() == "user-note"

    def test_capability_source_failure_defaults_to_native(self, monkeypatch):
        from pet.config import config
        monkeypatch.setattr(config, "LLM_TOOL_ROLE_MODE", "auto")

        def boom():
            raise RuntimeError("boom")

        assert self._state(native_tools=boom).resolve() == "native"

    def test_override_config(self, monkeypatch):
        from pet.config import config
        monkeypatch.setattr(config, "LLM_TOOL_ROLE_MODE", "auto")
        monkeypatch.setattr(config, "LLM_TOOL_ROLE_OVERRIDE", "qwen2.5=system-note")
        monkeypatch.setattr(config, "LLM_MODEL", "qwen2.5-7b")
        assert self._state().resolve() == "system-note"


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


def _native_messages():
    return [
        {"role": "user", "content": "帮我看视频"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "bilibili__like", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "点赞成功"},
    ]


class _RecordingProvider:
    """记录每次补全请求的 messages 与参数。"""

    def __init__(self):
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return SimpleNamespace(content="ok", tool_calls=(), finish_reason="stop",
                               usage=None)


class _RecordingLlm:
    """只暴露 provider 与通道状态的 LLMClient 替身。"""

    def __init__(self, provider, channel=None):
        self.provider = provider
        self.tool_channel = channel or default_state()

    def provider_name(self):
        return "openai"


class TestNativeStructure:
    def test_detects_tool_messages(self):
        assert uses_native_tools(_native_messages())

    def test_plain_messages_have_none(self):
        assert not uses_native_tools([{"role": "user", "content": "在吗"}])


class TestRetryWithoutToolChannel:
    def setup_method(self):
        default_state().reset()

    def teardown_method(self):
        default_state().reset()

    def _gateway(self, monkeypatch):
        from pet.config import config
        monkeypatch.setattr(config, "LLM_TOOL_ROLE_MODE", "auto")
        provider = _RecordingProvider()
        channel = ToolChannelState(lambda: ("openai", "m1"))
        return provider, channel, LlmGateway(_RecordingLlm(provider, channel),
                                            SimpleNamespace(increment=lambda: None))

    def test_native_channel_downgrades_one_step(self, monkeypatch):
        provider, channel, gateway = self._gateway(monkeypatch)
        messages = _native_messages()
        gateway._retry_without_tool_channel(
            lambda msgs=None: provider.complete(msgs),
            messages, _error("role 'tool' is not supported"))
        assert channel.resolve() == "user-note"
        assert all(m["role"] != "tool" for m in provider.calls[0]["messages"])

    def test_stale_native_messages_reuse_current_channel(self, monkeypatch):
        provider, channel, gateway = self._gateway(monkeypatch)
        channel.mark_downgraded("native")  # 缓存已落到 user-note
        messages = _native_messages()
        gateway._retry_without_tool_channel(
            lambda msgs=None: provider.complete(msgs),
            messages, _error("role 'tool' is not supported"))
        assert channel.resolve() == "user-note"
        assert all(m["role"] != "tool" for m in provider.calls[0]["messages"])

    def test_gemini_thinking_rejection_does_not_downgrade(self, monkeypatch):
        """模型侧参数被拒不是工具协议被拒，通道保持 native。"""
        provider, channel, gateway = self._gateway(monkeypatch)
        error = _error("thinking_level is not supported for this model")
        with pytest.raises(ProviderBadRequest):
            gateway._guard_tool_rejection(
                lambda msgs=None: (_ for _ in ()).throw(error),
                _native_messages())
        assert channel.resolve() == "native"
