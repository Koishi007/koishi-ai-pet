"""模型容灾测试：异常分类、重试、建流看门狗与首选/备选切换。"""

import threading
from types import SimpleNamespace

import pytest

from pet.brain import llm_client as llm_client_mod
from pet.brain.llm_errors import (ProviderAuthError, ProviderBadRequest,
                                  ProviderConnection, ProviderNotFound,
                                  ProviderRateLimit, ProviderServerError,
                                  ProviderTimeout)
from pet.brain.llm_types import Completion
from pet.brain.llm_client import (
    LLMClient, normalize_profile, other_profile, resolve_connection,
    resolve_llm_profile, resolve_provider_name,
)
from pet.brain.llm_gateway import LlmGateway
from pet.brain.llm_stats import LlmStats
from pet.brain.llm_retry import (
    CreateStreamTimeout, _create_with_watchdog, is_retryable,
    llm_retry, llm_stream_with_retry,
)
from pet.brain.tool_channel import ToolChannelState
from pet.config import config


class TestIsRetryable:
    @pytest.mark.parametrize("exc", [
        ConnectionError("net"),
        TimeoutError("slow"),
        OSError("io"),
        ProviderConnection("reset"),
        ProviderTimeout("read"),
        ProviderRateLimit("429", status_code=429),
        ProviderServerError("500", status_code=500),
    ])
    def test_retryable_exceptions(self, exc):
        assert is_retryable(exc) is True

    @pytest.mark.parametrize("exc", [
        ValueError("bad"),
        ProviderAuthError("401", status_code=401),
        ProviderBadRequest("400", status_code=400),
        ProviderNotFound("404", status_code=404),
    ])
    def test_non_retryable_exceptions(self, exc):
        assert is_retryable(exc) is False


@pytest.fixture
def fast_retry(monkeypatch):
    """关掉重试等待，避免测试真的睡几秒。"""
    monkeypatch.setattr(config, "LLM_MAX_RETRIES", 3)
    monkeypatch.setattr(config, "LLM_RETRY_DELAY", 0)
    monkeypatch.setattr(config, "LLM_RETRY_MAX_DELAY", 0)
    monkeypatch.setattr("time.sleep", lambda _s: None)


class TestLlmRetryDecorator:
    def test_retries_then_succeeds(self, fast_retry):
        calls = []

        @llm_retry(tag="T")
        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError("flaky")
            return "ok"

        assert fn() == "ok"
        assert len(calls) == 3

    def test_exhausted_retries_reraise_original(self, fast_retry):
        calls = []

        @llm_retry(tag="T")
        def fn():
            calls.append(1)
            raise ConnectionError("always down")

        with pytest.raises(ConnectionError):
            fn()
        assert len(calls) == 3

    def test_non_retryable_raises_immediately(self, fast_retry):
        calls = []

        @llm_retry(tag="T")
        def fn():
            calls.append(1)
            raise ValueError("bad request")

        with pytest.raises(ValueError):
            fn()
        assert len(calls) == 1

    def test_on_retry_notified_before_each_retry(self, fast_retry):
        seen = []

        @llm_retry(tag="T")
        def fn():
            raise ConnectionError("down")

        with pytest.raises(ConnectionError):
            fn(_on_retry=lambda exc: seen.append(type(exc).__name__))
        assert seen == ["ConnectionError", "ConnectionError"]

    def test_on_retry_exception_is_swallowed(self, fast_retry):
        def boom(_exc):
            raise RuntimeError("callback failed")

        @llm_retry(tag="T")
        def fn():
            raise ConnectionError("down")

        with pytest.raises(ConnectionError):
            fn(_on_retry=boom)


class TestCreateWithWatchdog:
    def test_returns_stream_on_success(self):
        assert _create_with_watchdog(lambda: "stream", 1.0) == "stream"

    def test_propagates_create_exception_unchanged(self):
        error = ProviderRateLimit("slow down", status_code=429)

        def boom():
            raise error

        with pytest.raises(ProviderRateLimit):
            _create_with_watchdog(boom, 1.0)

    def test_timeout_raises_create_stream_timeout(self):
        release = threading.Event()
        try:
            with pytest.raises(CreateStreamTimeout):
                _create_with_watchdog(lambda: release.wait(5), 0.05)
        finally:
            release.set()


class TestStreamWithRetry:
    def test_returns_on_first_success(self, fast_retry):
        assert llm_stream_with_retry(lambda: "stream", tag="T") == "stream"

    def test_retries_retryable_connect_error(self, fast_retry):
        calls = []
        notified = []

        def create():
            calls.append(1)
            if len(calls) < 2:
                raise ConnectionError("reset")
            return "stream"

        result = llm_stream_with_retry(
            create, tag="T", on_retry=lambda exc: notified.append(exc) or True)
        assert result == "stream"
        assert len(notified) == 1

    def test_non_retryable_connect_error_raises(self, fast_retry):
        def create():
            raise ValueError("bad")

        with pytest.raises(ValueError):
            llm_stream_with_retry(create, tag="T")

    def test_create_timeout_without_fallback_switch_gives_up(self, fast_retry):
        release = threading.Event()
        try:
            with pytest.raises(CreateStreamTimeout):
                llm_stream_with_retry(lambda: release.wait(5), tag="T",
                                      create_timeout=0.05,
                                      on_retry=lambda _exc: False)
        finally:
            release.set()

    def test_create_timeout_retries_after_fallback_switch(self, fast_retry):
        release = threading.Event()
        calls = []

        def create():
            calls.append(1)
            if len(calls) == 1:
                release.wait(5)
            return "stream"

        try:
            result = llm_stream_with_retry(create, tag="T", create_timeout=0.05,
                                           on_retry=lambda _exc: True)
        finally:
            release.set()
        assert result == "stream"
        assert len(calls) == 2


class TestGatewayCompleteRetry:
    """非流式补全的重试契约：重试前必须通知调用方切换备选方案。"""

    class _Provider:
        def __init__(self, owner):
            self._owner = owner
            self.model = "m1"

        def complete(self, messages, **kwargs):
            self._owner.calls.append(kwargs)
            if len(self._owner.calls) <= self._owner.fail_times:
                raise ProviderConnection("flaky")
            return self._owner.response

    class _Llm:
        def __init__(self, owner):
            self.provider = TestGatewayCompleteRetry._Provider(owner)

    def _gateway(self, fail_times: int, on_retry):
        owner = type("Owner", (), {})()
        owner.calls, owner.fail_times = [], fail_times
        owner.response = Completion(content="ok")
        gateway = LlmGateway(self._Llm(owner), LlmStats(), on_retry=on_retry)
        return gateway, owner

    def test_retry_notifies_on_retry_hook(self, fast_retry):
        notified = []
        gateway, owner = self._gateway(1, lambda exc: notified.append(exc) or True)
        gateway.complete([{"role": "user", "content": "hi"}])
        assert len(owner.calls) == 2
        assert len(notified) == 1

    def test_retry_continues_when_hook_returns_false(self, fast_retry):
        gateway, owner = self._gateway(1, lambda _exc: False)
        gateway.complete([{"role": "user", "content": "hi"}])
        assert len(owner.calls) == 2

    def test_hook_exception_does_not_break_retry(self, fast_retry):
        def boom(_exc):
            raise RuntimeError("hook 挂了")

        gateway, owner = self._gateway(1, boom)
        gateway.complete([{"role": "user", "content": "hi"}])
        assert len(owner.calls) == 2

    def test_no_retry_on_success(self, fast_retry):
        notified = []
        gateway, owner = self._gateway(0, lambda exc: notified.append(exc) or True)
        gateway.complete([{"role": "user", "content": "hi"}])
        assert len(owner.calls) == 1
        assert notified == []


def _bad_request(message: str) -> ProviderBadRequest:
    return ProviderBadRequest(message, status_code=400,
                              body=f'{{"error": {{"message": "{message}"}}}}')


class TestGatewayBadRequestDowngrade:
    """400 的处理范围：工具协议被拒才降档；thinking 由 provider 自己负责。"""

    class _Provider:
        def __init__(self, owner):
            self._owner = owner
            self.model = "m1"

        def complete(self, messages, **kwargs):
            self._owner.calls.append(kwargs)
            if len(self._owner.calls) <= self._owner.fail_times:
                raise self._owner.error
            return self._owner.response

    def _gateway(self, error, fail_times=1):
        owner = SimpleNamespace(calls=[], fail_times=fail_times, error=error,
                                response=Completion(content="ok"))
        llm = SimpleNamespace(provider=self._Provider(owner))
        return LlmGateway(llm, LlmStats()), owner

    def test_message_structure_400_is_not_retried(self):
        # 400 指向请求体结构而非工具字段：不降档，原样抛出
        gateway, owner = self._gateway(_bad_request("messages[17] is invalid"))
        with pytest.raises(ProviderBadRequest):
            gateway.complete([{"role": "user", "content": "hi"}])
        assert len(owner.calls) == 1

    def test_tool_field_400_downgrades_channel(self, monkeypatch):
        # 命中工具字段且明确不支持：降一档后重试
        channel = ToolChannelState(lambda: ("openai", "m1"))
        gateway, owner = self._gateway(
            ProviderBadRequest("bad", status_code=400,
                               body="unsupported tool_calls in messages"))
        gateway._channel = channel
        result = gateway.complete([{"role": "user", "content": "hi"}])
        assert result.content == "ok"
        assert len(owner.calls) == 2
        assert channel.resolve() == "user-note"


class TestProfileResolution:
    def test_normalize_profile(self):
        assert normalize_profile("alternative") == "alternative"
        assert normalize_profile("primary") == "primary"
        assert normalize_profile(None) == "primary"
        assert normalize_profile("garbage") == "primary"

    def test_other_profile_is_mutual(self):
        assert other_profile("primary") == "alternative"
        assert other_profile("alternative") == "primary"
        assert other_profile("garbage") == "alternative"

    def test_primary_resolution(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_KEY", "k1")
        monkeypatch.setattr(config, "LLM_URL", "u1")
        monkeypatch.setattr(config, "LLM_MODEL", "m1")
        assert resolve_llm_profile("primary") == ("k1", "u1", "m1")

    def test_alternative_fully_set(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_KEY_ALT", "k2")
        monkeypatch.setattr(config, "LLM_URL_ALT", "u2")
        monkeypatch.setattr(config, "LLM_MODEL_ALT", "m2")
        assert resolve_llm_profile("alternative") == ("k2", "u2", "m2")

    @pytest.mark.parametrize("missing", ["LLM_KEY_ALT", "LLM_URL_ALT",
                                         "LLM_MODEL_ALT"])
    def test_alternative_needs_all_three(self, monkeypatch, missing):
        """备选缺任一项即整方案沿用首选，不再逐字段拼接。"""
        monkeypatch.setattr(config, "LLM_KEY", "k1")
        monkeypatch.setattr(config, "LLM_URL", "u1")
        monkeypatch.setattr(config, "LLM_MODEL", "m1")
        monkeypatch.setattr(config, "LLM_KEY_ALT", "k2")
        monkeypatch.setattr(config, "LLM_URL_ALT", "u2")
        monkeypatch.setattr(config, "LLM_MODEL_ALT", "m2")
        monkeypatch.setattr(config, missing, "")
        assert resolve_llm_profile("alternative") == ("k1", "u1", "m1")

    def test_empty_alternative_uses_primary(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_KEY", "k1")
        monkeypatch.setattr(config, "LLM_URL", "u1")
        monkeypatch.setattr(config, "LLM_MODEL", "m1")
        for name in ("LLM_KEY_ALT", "LLM_URL_ALT", "LLM_MODEL_ALT"):
            monkeypatch.setattr(config, name, "")
        assert resolve_llm_profile("alternative") == ("k1", "u1", "m1")


def _bare_client(providers: dict, effective: str) -> LLMClient:
    """绕过 __init__，只装配切换逻辑所需的字段。"""
    obj = LLMClient.__new__(LLMClient)
    obj._providers = providers
    obj._effective = effective
    obj._lock = threading.RLock()
    return obj


class _FakeProvider:
    """只带 model 属性的 provider 替身。"""

    def __init__(self, model: str):
        self.model = model


@pytest.fixture
def dual_profile(monkeypatch):
    """首选与备选配置明显不同，可被判定为「真正切换」。"""
    monkeypatch.setattr(config, "LLM_ACTIVE_PROFILE", "primary")
    monkeypatch.setattr(config, "LLM_KEY", "k1")
    monkeypatch.setattr(config, "LLM_URL", "u1")
    monkeypatch.setattr(config, "LLM_MODEL", "m1")
    monkeypatch.setattr(config, "LLM_KEY_ALT", "k2")
    monkeypatch.setattr(config, "LLM_URL_ALT", "u2")
    monkeypatch.setattr(config, "LLM_MODEL_ALT", "m2")


class TestActivateFallback:
    def test_switches_to_alternative(self, dual_profile):
        alt = _FakeProvider("m2")
        client = _bare_client({"primary": _FakeProvider("m1"), "alternative": alt},
                              "primary")
        assert client.activate_fallback() is True
        assert client.effective_profile == "alternative"
        assert client.using_fallback is True
        assert client.model == "m2"

    def test_no_switch_when_already_on_alternative(self, dual_profile):
        client = _bare_client({"primary": _FakeProvider("m1"),
                               "alternative": _FakeProvider("m2")}, "alternative")
        assert client.activate_fallback() is False
        assert client.using_fallback is True

    def test_no_switch_when_alternative_unavailable(self, dual_profile):
        client = _bare_client({"primary": _FakeProvider("m1"), "alternative": None},
                              "primary")
        assert client.activate_fallback() is False
        assert client.using_fallback is False

    def test_no_switch_when_configs_identical(self, monkeypatch, dual_profile):
        # 备选留空 → 解析后与首选相同，切换没有意义
        for name in ("LLM_KEY_ALT", "LLM_URL_ALT", "LLM_MODEL_ALT"):
            monkeypatch.setattr(config, name, "")
        client = _bare_client({"primary": _FakeProvider("m1"),
                               "alternative": _FakeProvider("m1")}, "primary")
        assert client.activate_fallback() is False

    def test_reset_returns_to_user_choice(self, dual_profile):
        client = _bare_client({"primary": _FakeProvider("m1"),
                               "alternative": _FakeProvider("m2")}, "primary")
        client.activate_fallback()
        client.reset_effective()
        assert client.effective_profile == "primary"
        assert client.using_fallback is False
        assert client.model == "m1"

    def test_switches_when_primary_unavailable(self, dual_profile):
        # 首选不可用、备选可用时仍应切换（这正是需要容灾的场景）
        alt = _FakeProvider("m2")
        client = _bare_client({"primary": None, "alternative": alt}, "primary")
        assert client.provider is None
        assert bool(client) is False
        assert client.activate_fallback() is True
        assert client.provider is alt
        assert client.model == "m2"
        assert bool(client) is True


class TestConnectionAssembly:
    """既有配置到连接参数的映射：BRAIN 三档、通道归属与备选回落。"""

    def test_local_brain_yields_no_provider(self, monkeypatch):
        monkeypatch.setattr(config, "BRAIN", "local")
        obj = LLMClient.__new__(LLMClient)
        assert obj._build_profile("primary") is None

    def test_api_brain_without_any_value_yields_no_provider(self, monkeypatch):
        monkeypatch.setattr(config, "BRAIN", "api")
        for name in ("LLM_KEY", "LLM_URL", "LLM_MODEL"):
            monkeypatch.setattr(config, name, "")
        obj = LLMClient.__new__(LLMClient)
        assert obj._build_profile("primary") is None

    def test_ollama_brain_uses_openai_channel_and_default_model(self, monkeypatch):
        monkeypatch.setattr(config, "BRAIN", "ollama")
        monkeypatch.setattr(config, "LLM_MODEL", "")
        monkeypatch.setattr(config, "OLLAMA_BASE_URL", "http://localhost:11434/v1")

        connection = resolve_connection("primary")

        assert connection.provider == "openai"
        assert connection.key == llm_client_mod.OLLAMA_API_KEY
        assert connection.url == "http://localhost:11434/v1"
        assert connection.model == "llama3.2"

    def test_ollama_ignores_provider_config(self, monkeypatch):
        monkeypatch.setattr(config, "BRAIN", "ollama")
        monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
        assert resolve_connection("primary").provider == "openai"

    def test_api_brain_keeps_configured_channel(self, monkeypatch):
        monkeypatch.setattr(config, "BRAIN", "api")
        monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
        monkeypatch.setattr(config, "LLM_KEY", "k1")
        monkeypatch.setattr(config, "LLM_MODEL", "gemini-2.5-flash")

        connection = resolve_connection("primary")

        assert connection.provider == "gemini"
        assert connection.key == "k1"

    def test_alternative_channel_falls_back_to_primary(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
        monkeypatch.setattr(config, "LLM_PROVIDER_ALT", "")
        assert resolve_provider_name("alternative") == "gemini"

    def test_alternative_channel_overrides(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
        monkeypatch.setattr(config, "LLM_PROVIDER_ALT", "openai")
        monkeypatch.setattr(config, "LLM_KEY", "k1")
        monkeypatch.setattr(config, "LLM_MODEL", "m1")
        monkeypatch.setattr(config, "LLM_KEY_ALT", "k2")
        monkeypatch.setattr(config, "LLM_URL_ALT", "u2")
        monkeypatch.setattr(config, "LLM_MODEL_ALT", "m2")
        assert resolve_provider_name("alternative") == "openai"
        assert resolve_connection("alternative").key == "k2"


class TestClientLifecycle:
    """连接池由 LLMClient 持有，退出流程负责关闭。"""

    def test_close_releases_both_providers(self):
        closed = []

        class _P:
            def __init__(self, name):
                self.name, self.model = name, "m"

            def close(self):
                closed.append(self.name)

        client = _bare_client({"primary": _P("p"), "alternative": _P("a")}, "primary")
        client.close()

        assert sorted(closed) == ["a", "p"]
        assert client.provider is None
        assert bool(client) is False

    def test_close_twice_does_not_close_again(self):
        closed = []

        class _P:
            name, model = "p", "m"

            def close(self):
                closed.append(1)

        client = _bare_client({"primary": _P(), "alternative": None}, "primary")
        client.close()
        client.close()

        assert len(closed) == 1

    def test_tool_channel_follows_effective_profile(self, dual_profile):
        """降档缓存的键取当前生效方案：回退后是另一条记录。"""
        primary = SimpleNamespace(name="openai", model="m1")
        alternative = SimpleNamespace(name="gemini", model="m2")
        client = _bare_client({"primary": primary, "alternative": alternative},
                              "primary")
        client.tool_channel = ToolChannelState(client._scope_key)

        assert client.tool_channel.mark_downgraded("native") == "user-note"
        client.activate_fallback()

        assert client.tool_channel.resolve() == "native"
        assert client.provider_name() == "gemini"
