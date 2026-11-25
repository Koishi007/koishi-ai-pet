"""OpenAI 通道的编码、异常映射与流式解码。

断言中性类型与 payload 的结构，不断言用户可见文案。
"""

import pytest

from pet.brain.llm_errors import (ProviderAuthError, ProviderBadRequest,
                                  ProviderConnection, ProviderNotFound,
                                  ProviderRateLimit, ProviderServerError,
                                  ProviderTimeout)
from pet.brain.llm_types import (FINISH_LENGTH, FINISH_STOP, StreamEvent,
                                 ToolCall, ToolCallDelta, Usage)
from pet.brain.providers import LLMConnection
from pet.brain.providers.openai_provider import (OpenAIProvider, _map_error,
                                                 _OpenAIStream, encode_messages,
                                                 encode_tools)


def _provider(**kwargs) -> OpenAIProvider:
    connection = LLMConnection(provider="openai", key="test-key",
                               url="http://localhost:1/v1", model="test-model")
    provider = OpenAIProvider(connection)
    provider._client = kwargs.get("client") or _FakeClient()
    return provider


def _effort_provider(effort: str = "", client=None) -> OpenAIProvider:
    connection = LLMConnection(provider="openai", key="test-key",
                               url="http://localhost:1/v1", model="test-model")
    provider = OpenAIProvider(connection, reasoning_effort=effort)
    provider._client = client or _FakeClient()
    return provider


class _FakeClient:
    """最小 OpenAIClient 替身。"""

    def __init__(self, payload=None):
        self.payload = payload
        self.calls = []
        self.closed = False
        self.chat = _FakeChat(self)

    def close(self):
        self.closed = True


class _FakeChat:
    def __init__(self, client):
        self.completions = _FakeCompletions(client)


class _FakeCompletions:
    def __init__(self, client):
        self._client = client

    def create(self, **kwargs):
        self._client.calls.append(kwargs)
        return self._client.payload


def test_encode_messages_maps_image_part():
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "看图"},
        {"type": "image", "mime": "image/png", "data": "QUJD"},
    ]}]
    out = encode_messages(messages)
    parts = out[0]["content"]
    assert parts[0] == {"type": "text", "text": "看图"}
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,QUJD"


def test_encode_messages_keeps_cache_control():
    messages = [{"role": "system", "content": [
        {"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}},
    ]}]
    out = encode_messages(messages)
    assert out[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_encode_messages_maps_neutral_tool_calls():
    messages = [{"role": "assistant", "content": None,
                 "tool_calls": [{"id": "c1", "name": "t", "arguments": "{}"}]}]
    out = encode_messages(messages)
    call = out[0]["tool_calls"][0]
    assert call["id"] == "c1"
    assert call["type"] == "function"
    assert call["function"] == {"name": "t", "arguments": "{}"}


def test_encode_tools_wraps_function_shape():
    specs = [{"name": "a", "description": "d", "parameters": {"type": "object"}}]
    assert encode_tools(specs) == [{"type": "function", "function": specs[0]}]
    assert encode_tools(None) is None


@pytest.mark.parametrize("exc, expected", [
    (type("E", (Exception,), {"status_code": 401, "body": ""})(), ProviderAuthError),
    (type("E", (Exception,), {"status_code": 400, "body": "bad"})(), ProviderBadRequest),
    (type("E", (Exception,), {"status_code": 404, "body": ""})(), ProviderNotFound),
    (type("E", (Exception,), {"status_code": 429, "body": ""})(), ProviderRateLimit),
    (type("E", (Exception,), {"status_code": 503, "body": ""})(), ProviderServerError),
])
def test_map_error_by_status(exc, expected):
    from openai import APIStatusError
    error = APIStatusError.__new__(APIStatusError)
    Exception.__init__(error, "boom")
    error.status_code = exc.status_code
    error.body = exc.body
    assert isinstance(_map_error(error), expected)


def test_sdk_retries_disabled():
    """重试统一由 llm_retry 负责，SDK 内建重试会与之叠乘。"""
    provider = OpenAIProvider(LLMConnection(provider="openai", key="k",
                                            url="http://localhost:1/v1",
                                            model="m"))
    try:
        assert provider._client.max_retries == 0
    finally:
        provider.close()


def test_map_error_unknown_is_connection():
    assert isinstance(_map_error(RuntimeError("x")), ProviderConnection)


def test_map_error_sdk_specific_types():
    """SDK 专用异常按类型映射，不依赖状态码分组。"""
    import httpx
    from openai import (APIConnectionError, APITimeoutError,
                        AuthenticationError, PermissionDeniedError)

    def built(cls, **fields):
        error = cls.__new__(cls)
        Exception.__init__(error, "boom")
        for key, value in fields.items():
            setattr(error, key, value)
        return error

    assert isinstance(_map_error(built(AuthenticationError, status_code=401)),
                      ProviderAuthError)
    assert isinstance(_map_error(built(PermissionDeniedError, status_code=403)),
                      ProviderAuthError)
    assert isinstance(_map_error(built(APITimeoutError, request=None)),
                      ProviderTimeout)
    assert isinstance(_map_error(built(APIConnectionError, message="x")),
                      ProviderConnection)
    assert isinstance(_map_error(httpx.ReadTimeout("slow")), ProviderTimeout)
    assert isinstance(_map_error(httpx.RemoteProtocolError("bad")),
                      ProviderConnection)


def test_map_error_body_accepts_dict():
    """SDK 的 body 是 dict，拼接判据时不能抛异常。"""
    from openai import BadRequestError
    error = BadRequestError.__new__(BadRequestError)
    Exception.__init__(error, "boom")
    error.status_code = 400
    error.body = {"error": {"message": "unsupported thinking"}}
    mapped = _map_error(error)
    assert isinstance(mapped, ProviderBadRequest)
    assert "thinking" in mapped.body
    assert _provider().thinking_rejected(mapped) is True


def test_thinking_rejected_detects_body():
    provider = _provider()
    yes = ProviderBadRequest("400", status_code=400, body="unsupported thinking")
    no = ProviderBadRequest("400", status_code=400, body="bad tools")
    assert provider.thinking_rejected(yes) is True
    assert provider.thinking_rejected(no) is False
    assert provider.thinking_rejected(ProviderTimeout("t")) is False


def test_complete_maps_content_and_tool_calls():
    call = type("C", (), {"id": "c1", "function": type(
        "F", (), {"name": "timer__set", "arguments": '{"s":1}'})()})()
    message = type("M", (), {"content": "hi", "tool_calls": [call]})()
    choice = type("Ch", (), {"message": message, "finish_reason": "tool_calls"})()
    usage = type("U", (), {"prompt_tokens": 3, "completion_tokens": 4,
                           "total_tokens": 7})()
    payload = type("R", (), {"choices": [choice], "usage": usage})()
    provider = _provider(client=_FakeClient(payload))

    result = provider.complete([{"role": "user", "content": "x"}], max_tokens=10)

    assert result.content == "hi"
    assert result.tool_calls == (ToolCall("c1", "timer__set", '{"s":1}'),)
    assert result.finish_reason == "tool_calls"
    assert result.usage == Usage(3, 4, 7)


def test_complete_without_tool_calls():
    message = type("M", (), {"content": "只有文本", "tool_calls": None})()
    choice = type("Ch", (), {"message": message, "finish_reason": "stop"})()
    payload = type("R", (), {"choices": [choice], "usage": None})()
    provider = _provider(client=_FakeClient(payload))

    result = provider.complete([{"role": "user", "content": "x"}], max_tokens=10)

    assert result.tool_calls == ()
    assert result.finish_reason == FINISH_STOP
    assert result.usage is None


def test_complete_unknown_finish_reason_falls_back_to_stop():
    message = type("M", (), {"content": "x", "tool_calls": None})()
    choice = type("Ch", (), {"message": message, "finish_reason": "weird"})()
    payload = type("R", (), {"choices": [choice], "usage": None})()
    provider = _provider(client=_FakeClient(payload))

    assert provider.complete([{"role": "user", "content": "x"}],
                             max_tokens=10).finish_reason == FINISH_STOP


def test_complete_retries_without_thinking_on_rejection():
    message = type("M", (), {"content": "ok", "tool_calls": None})()
    choice = type("Ch", (), {"message": message, "finish_reason": "stop"})()
    payload = type("R", (), {"choices": [choice], "usage": None})()

    class Rejecting(_FakeClient):
        def __init__(self):
            super().__init__(payload)
            self.chat = _RejectingChat(self)

    provider = _provider(client=Rejecting())
    result = provider.complete([{"role": "user", "content": "x"}], max_tokens=10,
                               thinking=True)

    assert result.content == "ok"
    assert provider._thinking_unsupported is True
    assert "extra_body" not in provider._client.calls[-1]


def test_thinking_flag_not_sent_after_rejection():
    provider = _provider()
    provider._thinking_unsupported = True
    kwargs = provider._kwargs([{"role": "user", "content": "x"}], 10, None, True, None)
    assert "extra_body" not in kwargs


@pytest.mark.parametrize("effort", ["minimal", "low", "medium", "high", "xhigh", "max"])
def test_effort_sent_with_thinking(effort):
    kwargs = _effort_provider(effort)._kwargs(
        [{"role": "user", "content": "x"}], 10, None, True, None)
    assert kwargs["extra_body"]["reasoning_effort"] == effort
    assert kwargs["extra_body"]["thinking"] == {"type": "enabled"}


def test_effort_skipped_when_thinking_disabled():
    kwargs = _effort_provider("high")._kwargs(
        [{"role": "user", "content": "x"}], 10, None, False, None)
    assert "reasoning_effort" not in kwargs["extra_body"]
    assert kwargs["extra_body"]["thinking"] == {"type": "disabled"}


def test_undeclared_effort_is_not_sent():
    kwargs = _effort_provider("ultra")._kwargs(
        [{"role": "user", "content": "x"}], 10, None, True, None)
    assert "reasoning_effort" not in kwargs["extra_body"]


def test_rejection_field_prefers_effort():
    """同时命中两套词表时归为档位拒绝，避免把 thinking 一起去掉。"""
    provider = _effort_provider("high")
    both = ProviderBadRequest("400", status_code=400,
                              body="thinking.effort is not supported")
    assert provider._rejection_field(both) == "effort"
    assert provider.thinking_rejected(both) is False


def test_stream_returns_closable_iterator():
    events = [type("Chunk", (), {
        "choices": [type("Ch", (), {
            "delta": type("D", (), {"content": "a", "reasoning_content": None,
                                    "tool_calls": None})(),
            "finish_reason": None})()],
        "usage": None})()]
    provider = _provider(client=_StreamClient(events))
    stream = provider.stream([{"role": "user", "content": "x"}], max_tokens=10)

    assert hasattr(stream, "close")
    assert isinstance(next(iter(stream)), StreamEvent)
    stream.close()
    assert provider._client.stream_rows[0].closed is True


def test_stream_emits_finish_and_usage():
    chunk = type("Chunk", (), {
        "choices": [type("Ch", (), {
            "delta": type("D", (), {"content": "", "reasoning_content": None,
                                    "tool_calls": None})(),
            "finish_reason": "length"})()],
        "usage": type("U", (), {"prompt_tokens": 1, "completion_tokens": 2,
                                "total_tokens": 3})()})()
    provider = _provider(client=_StreamClient([chunk]))
    stream = provider.stream([{"role": "user", "content": "x"}], max_tokens=10)

    event = next(iter(stream))
    assert event.finish_reason == FINISH_LENGTH
    assert event.usage == Usage(1, 2, 3)


class _FakeStream:
    def __init__(self, rows):
        self._rows = list(rows)
        self.closed = False

    def __iter__(self):
        return iter(self._rows)

    def close(self):
        self.closed = True


class _StreamClient(_FakeClient):
    def __init__(self, rows):
        super().__init__()
        self.stream_rows = [_FakeStream(rows)]
        self.chat = _StreamChat(self)


class _StreamChat(_FakeChat):
    def __init__(self, client):
        self.completions = _StreamCompletions(client)


class _StreamCompletions:
    def __init__(self, client):
        self._client = client

    def create(self, **kwargs):
        self._client.calls.append(kwargs)
        return self._client.stream_rows[0]


class _RejectingChat(_FakeChat):
    def __init__(self, client):
        self.completions = _RejectingCompletions(client)


class _RejectingCompletions(_FakeCompletions):
    def create(self, **kwargs):
        super().create(**kwargs)
        if "extra_body" in kwargs:
            from openai import BadRequestError
            error = BadRequestError.__new__(BadRequestError)
            Exception.__init__(error, "unsupported thinking")
            error.status_code = 400
            error.body = "unsupported thinking"
            raise error
        return self._client.payload


def _ok_payload():
    message = type("M", (), {"content": "ok", "tool_calls": None})()
    choice = type("Ch", (), {"message": message, "finish_reason": "stop"})()
    return type("R", (), {"choices": [choice], "usage": None})()


class _FieldRejectingCompletions(_FakeCompletions):
    """按 extra_body 实际内容抛 400，用于验证逐字段降级。"""

    def create(self, **kwargs):
        self._client.calls.append(kwargs)
        body = kwargs.get("extra_body") or {}
        for field in self._client.reject_fields:
            if field in body:
                from openai import BadRequestError
                error = BadRequestError.__new__(BadRequestError)
                Exception.__init__(error, f"unsupported {field}")
                error.status_code = 400
                error.body = f"unsupported {field}"
                raise error
        return self._client.payload


class _FieldRejectingChat(_FakeChat):
    def __init__(self, client):
        self.completions = _FieldRejectingCompletions(client)


class _FieldRejectingClient(_FakeClient):
    def __init__(self, payload, reject_fields):
        super().__init__(payload)
        self.reject_fields = tuple(reject_fields)
        self.chat = _FieldRejectingChat(self)


def test_effort_rejection_keeps_thinking():
    client = _FieldRejectingClient(_ok_payload(), ("reasoning_effort",))
    provider = _effort_provider("high", client=client)

    result = provider.complete([{"role": "user", "content": "x"}], max_tokens=10,
                               thinking=True)

    assert result.content == "ok"
    assert provider.reasoning_effort_rejected() is True
    assert provider._thinking_unsupported is False
    assert "reasoning_effort" in client.calls[0]["extra_body"]
    assert "reasoning_effort" not in client.calls[1]["extra_body"]
    assert "thinking" in client.calls[1]["extra_body"]


def test_both_fields_rejected_are_dropped_one_at_a_time():
    client = _FieldRejectingClient(_ok_payload(), ("reasoning_effort", "thinking"))
    provider = _effort_provider("high", client=client)

    result = provider.complete([{"role": "user", "content": "x"}], max_tokens=10,
                               thinking=True)

    assert result.content == "ok"
    assert len(client.calls) == 3
    assert "extra_body" not in client.calls[-1]
    assert provider.reasoning_effort_rejected() is True
    assert provider._thinking_unsupported is True

    provider.complete([{"role": "user", "content": "x"}], max_tokens=10, thinking=True)
    assert "extra_body" not in client.calls[-1]
