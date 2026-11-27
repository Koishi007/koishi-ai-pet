"""Gemini 推理强度：thinking_level 的下发条件与降级。

google-genai 未安装，用最小的 types 替身覆盖构造逻辑。
"""

from types import SimpleNamespace

import pytest

from pet.brain.llm_errors import ProviderBadRequest
from pet.brain.providers import Capabilities as _Capabilities
from pet.brain.providers.gemini_provider import GeminiProvider


class _FakeTypes:
    """只提供 GeminiProvider 用到的构造器。"""

    class ThinkingConfig:
        def __init__(self, thinking_budget=None, thinking_level=None):
            self.thinking_budget = thinking_budget
            self.thinking_level = thinking_level

    class Tool:
        def __init__(self, function_declarations=None):
            self.function_declarations = function_declarations

    class FunctionDeclaration:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class GenerateContentConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)


def _provider(model="gemini-3-flash", effort=""):
    """绕过 __init__ 的 SDK 构造，只装配思考参数相关字段。"""
    provider = GeminiProvider.__new__(GeminiProvider)
    provider._types = _FakeTypes
    provider._effort = effort
    provider._thinking_unsupported = False
    provider._effort_unsupported = False
    provider.model = model
    return provider


@pytest.mark.parametrize("effort", ["minimal", "low", "medium", "high"])
def test_effort_passes_through(effort):
    config = _provider(effort=effort)._thinking_config(True)
    assert config.thinking_level == effort
    assert config.thinking_budget is None


def test_effort_omitted_when_unset():
    """未选档位时不下发 thinking_config，交给模型默认。"""
    assert _provider(effort="")._thinking_config(True) is None


@pytest.mark.parametrize("effort", ["xhigh", "max"])
def test_unavailable_effort_is_not_sent(effort):
    """Gemini 没有 xhigh 与 max，配置里出现也不下发。"""
    assert _provider(effort=effort)._thinking_config(True) is None


@pytest.mark.parametrize("model", ["gemini-3-flash", "gemini-2.5-flash"])
def test_disabled_always_sends_zero_budget(model):
    config = _provider(model=model, effort="high")._thinking_config(False)
    assert config.thinking_budget == 0
    assert config.thinking_level is None


def test_rejected_effort_is_not_sent_again():
    """端点拒绝档位后，同一实例不再下发该字段。"""
    provider = _provider(effort="high")
    provider._effort_unsupported = True
    assert provider._thinking_config(True) is None


def test_rejected_thinking_flag_sends_zero_budget():
    provider = _provider(effort="high")
    provider._thinking_unsupported = True
    assert provider._thinking_config(True).thinking_budget == 0


def test_config_carries_effort_for_supported_model():
    provider = _provider(effort="low")
    config = provider._config(2048, None, True, 0.7)
    assert config.thinking_config.thinking_level == "low"
    assert config.max_output_tokens == 2048


def test_client_passes_configured_effort(monkeypatch):
    """重建客户端时按当前配置取档位，保存后无需重启。"""
    from pet.brain import llm_client as llm_client_mod
    from pet.config import config

    captured = {}

    def fake_build(connection, timeout, reasoning_effort=""):
        captured.update(provider=connection.provider, effort=reasoning_effort)
        return object()

    monkeypatch.setattr(config, "BRAIN", "api")
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "LLM_KEY", "k")
    monkeypatch.setattr(config, "LLM_MODEL", "gemini-3-flash")
    monkeypatch.setattr(config, "LLM_REASONING_EFFORT", "high")
    monkeypatch.setattr(llm_client_mod, "build_provider", fake_build)

    client = llm_client_mod.LLMClient.__new__(llm_client_mod.LLMClient)
    client._build_profile("primary")

    assert captured == {"provider": "gemini", "effort": "high"}


class _Boom(Exception):
    """模拟 SDK 的 400：带 code 让 _map_error 归为 ProviderBadRequest。"""

    code = 400

    def __init__(self, message):
        super().__init__(message)
        self._message = message

    def __str__(self):
        return self._message


class _CountingModels:
    """记录调用次数，按脚本决定第几次抛错。"""

    def __init__(self, failures, response=None):
        self.calls = 0
        self._failures = failures
        self._response = response

    def generate_content(self, **kwargs):
        self.calls += 1
        if self.calls <= self._failures:
            raise _Boom("thinking_level is not supported for this model")
        return self._response

    def generate_content_stream(self, **kwargs):
        self.calls += 1
        if self.calls <= self._failures:
            raise _Boom("thinking_level is not supported for this model")
        return iter([])


def _live_provider(models, effort="high", model="gemini-3-flash"):
    provider = GeminiProvider.__new__(GeminiProvider)
    provider._types = _FakeTypes
    provider._client = SimpleNamespace(models=models)
    provider._effort = effort
    provider._thinking_unsupported = False
    provider._effort_unsupported = False
    provider.model = model
    provider.capabilities = _Capabilities(thinking=True)
    return provider


def test_rejected_effort_is_retried_inside_complete():
    """档位被拒时在 provider 内降级重发，调用方拿到结果。"""
    response = SimpleNamespace(
        candidates=[SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(text="好", thought=False,
                                                           function_call=None)]),
            finish_reason="STOP")],
        usage_metadata=None, function_calls=None)
    models = _CountingModels(failures=1, response=response)
    provider = _live_provider(models)

    result = provider.complete([{"role": "user", "content": "hi"}], max_tokens=8,
                               thinking=True)

    assert result.content == "好"
    assert models.calls == 2
    assert provider.reasoning_effort_rejected() is True


def test_rejected_effort_is_retried_inside_stream():
    models = _CountingModels(failures=1)
    provider = _live_provider(models)

    stream = provider.stream([{"role": "user", "content": "hi"}], max_tokens=8,
                             thinking=True)
    assert list(stream) == []
    assert models.calls == 2
    assert provider.reasoning_effort_rejected() is True


def test_retry_happens_once_only():
    """降级只重发一次；重发仍失败时把错误抛给调用方。"""
    models = _CountingModels(failures=2)
    provider = _live_provider(models)

    with pytest.raises(ProviderBadRequest):
        provider.complete([{"role": "user", "content": "hi"}], max_tokens=8,
                          thinking=True)
    assert models.calls == 2


def test_no_retry_when_effort_unset():
    """没选档位时不带该字段，被拒也与档位无关，不重发。"""
    models = _CountingModels(failures=1)
    provider = _live_provider(models, effort="")

    with pytest.raises(ProviderBadRequest):
        provider.complete([{"role": "user", "content": "hi"}], max_tokens=8,
                          thinking=True)
    assert models.calls == 1


def test_no_retry_when_thinking_disabled():
    models = _CountingModels(failures=1)
    provider = _live_provider(models)

    with pytest.raises(ProviderBadRequest):
        provider.complete([{"role": "user", "content": "hi"}], max_tokens=8,
                          thinking=False)
    assert models.calls == 1


def test_unlisted_generation_retries_then_remembers():
    """代际不由模型名推断：档位照发，400 后按默认档位重发一次并记住。"""
    models = _CountingModels(failures=1)
    provider = _live_provider(models, model="gemini-2.5-flash")

    provider.complete([{"role": "user", "content": "hi"}], max_tokens=8,
                      thinking=True)
    assert models.calls == 2
    assert provider.reasoning_effort_rejected() is True
