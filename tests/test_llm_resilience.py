"""模型容灾测试：异常分类、重试、建流看门狗与首选/备选切换。"""

import threading

import httpx
import pytest
from openai import AuthenticationError, BadRequestError, RateLimitError

from pet.brain import llm_client as llm_client_mod
from pet.brain.llm_client import (
    LLMClient, normalize_profile, other_profile, resolve_llm_profile,
)
from pet.brain.llm_gateway import LlmGateway
from pet.brain.llm_stats import LlmStats
from pet.brain.llm_retry import (
    CreateStreamTimeout, _create_with_watchdog, is_retryable,
    llm_retry, llm_stream_with_retry,
)
from pet.config import config


def _http_error(exc_type, status: int):
    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return exc_type("boom", response=response, body=None)


class TestIsRetryable:
    @pytest.mark.parametrize("exc", [
        ConnectionError("net"),
        TimeoutError("slow"),
        OSError("io"),
        httpx.ReadTimeout("read"),
        httpx.RemoteProtocolError("reset"),
        _http_error(RateLimitError, 429),
    ])
    def test_retryable_exceptions(self, exc):
        assert is_retryable(exc) is True

    @pytest.mark.parametrize("exc", [
        ValueError("bad"),
        _http_error(AuthenticationError, 401),
        _http_error(BadRequestError, 400),
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
        def boom():
            raise RateLimitError("slow down", response=_http_error(RateLimitError, 429).response, body=None)

        with pytest.raises(RateLimitError):
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


class TestGatewayCompletionRetry:
    """非流式补全的重试契约：重试前必须通知调用方切换备选方案。"""

    class _Completions:
        def __init__(self, owner):
            self._owner = owner

        def create(self, **kwargs):
            self._owner.calls.append(kwargs)
            if len(self._owner.calls) <= self._owner.fail_times:
                raise ConnectionError("flaky")
            return self._owner.response

    class _Chat:
        def __init__(self, owner):
            self.completions = TestGatewayCompletionRetry._Completions(owner)

    class _Client:
        def __init__(self, owner):
            self.chat = TestGatewayCompletionRetry._Chat(owner)

    class _Llm:
        def __init__(self, owner):
            self.model = "m1"
            self.client = TestGatewayCompletionRetry._Client(owner)

    def _gateway(self, fail_times: int, on_retry):
        owner = type("Owner", (), {})()
        owner.calls, owner.fail_times = [], fail_times
        owner.response = type("Resp", (), {"choices": [], "usage": None})()
        gateway = LlmGateway(self._Llm(owner), LlmStats(), on_retry=on_retry)
        return gateway, owner

    def test_retry_notifies_on_retry_hook(self, fast_retry):
        notified = []
        gateway, owner = self._gateway(1, lambda exc: notified.append(exc) or True)
        gateway.completion([{"role": "user", "content": "hi"}])
        assert len(owner.calls) == 2
        assert len(notified) == 1

    def test_retry_continues_when_hook_returns_false(self, fast_retry):
        gateway, owner = self._gateway(1, lambda _exc: False)
        gateway.completion([{"role": "user", "content": "hi"}])
        assert len(owner.calls) == 2

    def test_hook_exception_does_not_break_retry(self, fast_retry):
        def boom(_exc):
            raise RuntimeError("hook 挂了")

        gateway, owner = self._gateway(1, boom)
        gateway.completion([{"role": "user", "content": "hi"}])
        assert len(owner.calls) == 2

    def test_no_retry_on_success(self, fast_retry):
        notified = []
        gateway, owner = self._gateway(0, lambda exc: notified.append(exc) or True)
        gateway.completion([{"role": "user", "content": "hi"}])
        assert len(owner.calls) == 1
        assert notified == []


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

    def test_alternative_falls_back_per_field(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_KEY", "k1")
        monkeypatch.setattr(config, "LLM_URL", "u1")
        monkeypatch.setattr(config, "LLM_MODEL", "m1")
        monkeypatch.setattr(config, "LLM_KEY_ALT", "")
        monkeypatch.setattr(config, "LLM_URL_ALT", "u2")
        monkeypatch.setattr(config, "LLM_MODEL_ALT", "")
        assert resolve_llm_profile("alternative") == ("k1", "u2", "m1")

    def test_empty_alternative_uses_primary(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_KEY", "k1")
        monkeypatch.setattr(config, "LLM_URL", "u1")
        monkeypatch.setattr(config, "LLM_MODEL", "m1")
        for name in ("LLM_KEY_ALT", "LLM_URL_ALT", "LLM_MODEL_ALT"):
            monkeypatch.setattr(config, name, "")
        assert resolve_llm_profile("alternative") == ("k1", "u1", "m1")


def _bare_client(clients: dict, models: dict, effective: str) -> LLMClient:
    """绕过 __init__，只装配切换逻辑所需的字段。"""
    obj = LLMClient.__new__(LLMClient)
    obj._clients = clients
    obj._models = models
    obj._effective = effective
    obj._lock = threading.RLock()
    return obj


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
        client = _bare_client({"primary": "c1", "alternative": "c2"},
                              {"primary": "m1", "alternative": "m2"}, "primary")
        assert client.activate_fallback() is True
        assert client.effective_profile == "alternative"
        assert client.using_fallback is True
        assert client.model == "m2"

    def test_no_switch_when_already_on_alternative(self, dual_profile):
        client = _bare_client({"primary": "c1", "alternative": "c2"},
                              {"primary": "m1", "alternative": "m2"}, "alternative")
        assert client.activate_fallback() is False
        assert client.using_fallback is True

    def test_no_switch_when_alternative_unavailable(self, dual_profile):
        client = _bare_client({"primary": "c1", "alternative": None},
                              {"primary": "m1", "alternative": None}, "primary")
        assert client.activate_fallback() is False
        assert client.using_fallback is False

    def test_no_switch_when_configs_identical(self, monkeypatch, dual_profile):
        # 备选留空 → 解析后与首选相同，切换没有意义
        for name in ("LLM_KEY_ALT", "LLM_URL_ALT", "LLM_MODEL_ALT"):
            monkeypatch.setattr(config, name, "")
        client = _bare_client({"primary": "c1", "alternative": "c2"},
                              {"primary": "m1", "alternative": "m1"}, "primary")
        assert client.activate_fallback() is False

    def test_reset_returns_to_user_choice(self, dual_profile):
        client = _bare_client({"primary": "c1", "alternative": "c2"},
                              {"primary": "m1", "alternative": "m2"}, "primary")
        client.activate_fallback()
        client.reset_effective()
        assert client.effective_profile == "primary"
        assert client.using_fallback is False
        assert client.model == "m1"

    def test_switches_when_primary_unavailable(self, dual_profile):
        # 首选不可用、备选可用时仍应切换（这正是需要容灾的场景）
        client = _bare_client({"primary": None, "alternative": "c2"},
                              {"primary": None, "alternative": "m2"}, "primary")
        assert client.client is None
        assert bool(client) is False
        assert client.activate_fallback() is True
        assert client.client == "c2"
        assert client.model == "m2"
        assert bool(client) is True


class TestBuildProfile:
    def test_local_brain_yields_no_client(self, monkeypatch):
        monkeypatch.setattr(config, "BRAIN", "local")
        obj = LLMClient.__new__(LLMClient)
        assert obj._build_profile("primary") == (None, None)

    def test_api_brain_without_key_yields_no_client(self, monkeypatch):
        monkeypatch.setattr(config, "BRAIN", "api")
        monkeypatch.setattr(config, "LLM_KEY", "")
        obj = LLMClient.__new__(LLMClient)
        assert obj._build_profile("primary") == (None, None)

    def test_api_brain_builds_client_with_resolved_model(self, monkeypatch):
        monkeypatch.setattr(config, "BRAIN", "api")
        monkeypatch.setattr(config, "LLM_KEY", "k1")
        monkeypatch.setattr(config, "LLM_URL", "u1")
        monkeypatch.setattr(config, "LLM_MODEL", "m1")
        captured = {}

        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(llm_client_mod, "OpenAI", FakeOpenAI)
        obj = LLMClient.__new__(LLMClient)
        client, model = obj._build_profile("primary")
        assert isinstance(client, FakeOpenAI)
        assert model == "m1"
        assert captured["api_key"] == "k1"
        assert captured["base_url"] == "u1"
        assert captured["max_retries"] == 0

    def test_ollama_brain_uses_default_local_model(self, monkeypatch):
        monkeypatch.setattr(config, "BRAIN", "ollama")
        monkeypatch.setattr(config, "LLM_MODEL", "")
        monkeypatch.setattr(config, "OLLAMA_BASE_URL", "http://localhost:11434/v1")
        captured = {}

        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(llm_client_mod, "OpenAI", FakeOpenAI)
        obj = LLMClient.__new__(LLMClient)
        _, model = obj._build_profile("primary")
        assert model == "llama3.2"
        assert captured["base_url"] == "http://localhost:11434/v1"
