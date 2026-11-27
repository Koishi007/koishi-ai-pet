"""Gemini 通道的编码、结束原因映射与流式解码。

用最小替身覆盖编码与映射逻辑，不依赖真实 SDK 的类型与网络。
"""

from types import SimpleNamespace

import pytest

from pet.brain.llm_errors import (ProviderAuthError, ProviderBadRequest,
                                  ProviderConnection, ProviderNotFound,
                                  ProviderRateLimit, ProviderServerError,
                                  ProviderTimeout)
from pet.brain.llm_types import (FINISH_CONTENT_FILTER, FINISH_LENGTH,
                                 FINISH_STOP, StreamEvent, ToolCallDelta, Usage)
from pet.brain.providers import Capabilities
from pet.brain.providers.gemini_provider import (GeminiProvider, _finish_of,
                                                 _map_error, encode_contents,
                                                 encode_tools)


def test_encode_contents_separates_system():
    contents, system = encode_contents([
        {"role": "system", "content": "人设"},
        {"role": "user", "content": "在吗"},
    ])
    assert system == "人设"
    assert contents == [{"role": "user", "parts": [{"text": "在吗"}]}]


def test_encode_contents_maps_image_part():
    contents, _ = encode_contents([{"role": "user", "content": [
        {"type": "text", "text": "看图"},
        {"type": "image", "mime": "image/png", "data": "QUJD"},
    ]}])
    parts = contents[0]["parts"]
    assert parts[0] == {"text": "看图"}
    assert parts[1] == {"inline_data": {"mime_type": "image/png", "data": "QUJD"}}


def test_encode_contents_maps_tool_call_and_response():
    contents, _ = encode_contents([
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "name": "timer__set", "arguments": '{"s": 1}'}]},
        {"role": "tool", "tool_call_id": "c1", "content": "设好了"},
    ])
    assert contents[0]["role"] == "model"
    assert contents[0]["parts"][0]["function_call"] == {
        "name": "timer__set", "args": {"s": 1}}
    # 结果按名配对，id 只用来查表
    assert contents[1] == {"role": "user", "parts": [{"function_response": {
        "name": "timer__set", "response": {"result": "设好了"}}}]}


def test_consecutive_tool_results_merge_into_one_content():
    """同一轮的多条工具结果合并进一条 user content，保持配对序列不断开。"""
    contents, _ = encode_contents([
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "name": "timer__set", "arguments": '{"s": 1}'},
            {"id": "c2", "name": "timer__set", "arguments": '{"s": 2}'}]},
        {"role": "tool", "tool_call_id": "c1", "content": "甲"},
        {"role": "tool", "tool_call_id": "c2", "content": "乙"},
    ])
    assert [c["role"] for c in contents] == ["model", "user"]
    responses = contents[1]["parts"]
    assert len(responses) == 2
    assert [r["function_response"]["response"]["result"] for r in responses] == ["甲", "乙"]


def test_merged_results_keep_call_order():
    """合并后仍按调用顺序排列：协议靠顺序区分同名调用。"""
    contents, _ = encode_contents([
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "name": "same", "arguments": "{}"},
            {"id": "c2", "name": "same", "arguments": "{}"}]},
        {"role": "tool", "tool_call_id": "c2", "content": "第二个"},
        {"role": "tool", "tool_call_id": "c1", "content": "第一个"},
    ])
    results = [r["function_response"]["response"]["result"]
               for r in contents[1]["parts"]]
    # 编码按消息顺序，不重排；调用方负责按调用顺序放结果
    assert results == ["第二个", "第一个"]


def test_tool_ids_resolve_to_their_own_round():
    """流式与非流式的 id 各自编号，跨轮重复时按最近一次取函数名。"""
    contents, _ = encode_contents([
        {"role": "user", "content": "第一轮"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "call_0", "name": "timer__set", "arguments": "{}"}]},
        {"role": "tool", "tool_call_id": "call_0", "content": "定时器已设置"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "call_0", "name": "memorize", "arguments": "{}"}]},
        {"role": "tool", "tool_call_id": "call_0", "content": "已记住"},
    ])
    names = [part["function_response"]["name"]
             for content in contents for part in content["parts"]
             if "function_response" in part]
    assert names == ["timer__set", "memorize"]


def test_stream_and_complete_use_disjoint_call_ids():
    """两条路径的序号起点不同，同一段历史里不会互相覆盖。"""
    from pet.brain.providers.gemini_provider import _GeminiStream

    call = SimpleNamespace(name="timer__set", args={})
    stream = _GeminiStream(_FakeStream([_response([_part(function_call=call)])]))
    assert next(iter(stream)).tool_call.id == "call_1"

    provider = GeminiProvider.__new__(GeminiProvider)
    response = SimpleNamespace(function_calls=[call])
    assert provider._to_completion(response).tool_calls[0].id == "call_0"


def test_tool_results_do_not_leak_internal_marker():
    """合并标记是内部状态，不能出现在传给 SDK 的字典里。"""
    contents, _ = encode_contents([
        {"role": "tool", "tool_call_id": "missing", "content": "结果"},
    ])
    assert all("tool_results" not in c for c in contents)


def test_non_consecutive_tool_results_stay_separate():
    """中间夹了别的消息时不合并。"""
    contents, _ = encode_contents([
        {"role": "tool", "tool_call_id": "c1", "content": "甲"},
        {"role": "user", "content": "插话"},
        {"role": "tool", "tool_call_id": "c2", "content": "乙"},
    ])
    assert [c["role"] for c in contents] == ["user", "user", "user"]


def test_encode_contents_unknown_tool_id_falls_back():
    contents, _ = encode_contents([
        {"role": "tool", "tool_call_id": "missing", "content": "结果"},
    ])
    assert contents[0]["parts"][0]["function_response"]["name"] == "tool"


def test_encode_contents_skips_empty_parts():
    contents, _ = encode_contents([{"role": "assistant", "content": None}])
    assert contents == []


def test_encode_tools_uses_json_schema_field():
    specs = [{"name": "a", "description": "d",
              "parameters": {"type": "object", "properties": {}}}]
    out = encode_tools(specs)
    assert out[0]["parameters_json_schema"] == specs[0]["parameters"]
    assert encode_tools(None) is None


@pytest.mark.parametrize("raw, expected", [
    ("STOP", FINISH_STOP),
    ("MAX_TOKENS", FINISH_LENGTH),
    ("SAFETY", FINISH_CONTENT_FILTER),
    ("MALFORMED_FUNCTION_CALL", FINISH_CONTENT_FILTER),
    ("UNEXPECTED_TOOL_CALL", FINISH_CONTENT_FILTER),
    ("OTHER", FINISH_STOP),
    (None, FINISH_STOP),
])
def test_finish_reason_mapping(raw, expected):
    assert _finish_of(raw) == expected


def test_finish_reason_reads_enum_name():
    class Enum:
        name = "MAX_TOKENS"
    assert _finish_of(Enum()) == FINISH_LENGTH


@pytest.mark.parametrize("code, expected", [
    (401, ProviderAuthError),
    (403, ProviderAuthError),
    (400, ProviderBadRequest),
    (404, ProviderNotFound),
    (429, ProviderRateLimit),
    (503, ProviderServerError),
])
def test_map_error_by_code(code, expected):
    exc = SimpleNamespace(code=code)
    assert isinstance(_map_error(exc), expected)


def test_map_error_timeout_and_connection():
    class ClientTimeout(Exception):
        pass

    class TransportError(Exception):
        pass

    assert isinstance(_map_error(ClientTimeout("slow")), ProviderTimeout)
    assert isinstance(_map_error(TransportError("reset")), ProviderConnection)


class _FakeStream:
    """按顺序回放预置响应。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.closed = False

    def __iter__(self):
        return iter(self._responses)

    def close(self):
        self.closed = True


def _response(parts, finish=None, usage=None):
    return SimpleNamespace(
        candidates=[SimpleNamespace(
            content=SimpleNamespace(parts=parts), finish_reason=finish)],
        usage_metadata=usage,
    )


def _part(text=None, thought=False, function_call=None):
    return SimpleNamespace(text=text, thought=thought, function_call=function_call)


def test_stream_separates_thought_from_content():
    from pet.brain.providers.gemini_provider import _GeminiStream
    stream = _GeminiStream(_FakeStream([
        _response([_part("想一下", thought=True), _part("你好")]),
    ]))
    event = next(iter(stream))
    assert event.reasoning == "想一下"
    assert event.content == "你好"


def test_stream_emits_complete_tool_delta():
    from pet.brain.providers.gemini_provider import _GeminiStream
    call = SimpleNamespace(name="timer__set", args={"s": 1})
    stream = _GeminiStream(_FakeStream([
        _response([_part(function_call=call)]),
    ]))
    event = next(iter(stream))
    assert isinstance(event.tool_call, ToolCallDelta)
    assert event.tool_call.complete is True
    assert event.tool_call.name == "timer__set"
    assert event.tool_call.arguments == '{"s": 1}'
    assert event.tool_call.id


def test_stream_emits_finish_and_usage():
    from pet.brain.providers.gemini_provider import _GeminiStream
    usage = SimpleNamespace(prompt_token_count=1, candidates_token_count=2,
                            total_token_count=3)
    stream = _GeminiStream(_FakeStream([_response([_part("完")], "MAX_TOKENS", usage)]))
    event = next(iter(stream))
    assert event.finish_reason == FINISH_LENGTH
    assert event.usage == Usage(1, 2, 3)


def test_stream_close_propagates():
    from pet.brain.providers.gemini_provider import _GeminiStream
    raw = _FakeStream([])
    stream = _GeminiStream(raw)
    stream.close()
    assert raw.closed is True


def test_stream_emits_every_parallel_function_call():
    """同一块里的多个 function_call 各成一条事件，id 按顺序递增。"""
    from pet.brain.providers.gemini_provider import _GeminiStream
    parts = [_part(function_call=SimpleNamespace(name="timer__set", args={"s": 1})),
             _part(function_call=SimpleNamespace(name="todo__add", args={"t": "x"}))]
    stream = _GeminiStream(_FakeStream([_response(parts)]))

    events = list(stream)

    assert [e.tool_call.name for e in events] == ["timer__set", "todo__add"]
    assert [e.tool_call.index for e in events] == [1, 2]
    assert events[0].tool_call.id == "call_1"
    assert events[1].tool_call.id == "call_2"


def test_stream_text_shares_block_with_function_calls():
    """正文与函数调用同块时，正文随最后一条事件交出，不重复。"""
    from pet.brain.providers.gemini_provider import _GeminiStream
    parts = [_part("好"),
             _part(function_call=SimpleNamespace(name="timer__set", args={})),
             _part(function_call=SimpleNamespace(name="todo__add", args={}))]
    stream = _GeminiStream(_FakeStream([_response(parts, finish="STOP")]))

    events = list(stream)

    assert "".join(e.content for e in events) == "好"
    assert events[-1].finish_reason == FINISH_STOP


class _BrokenStream:
    """首条数据正常，之后迭代抛异常。"""

    def __init__(self, error):
        self._rows = [_response([_part("a")])]
        self._error = error

    def __iter__(self):
        return self

    def __next__(self):
        if self._rows:
            return self._rows.pop(0)
        raise self._error


def test_stream_iteration_error_is_mapped():
    """响应头成功、响应体读取失败时，异常在通道边界转换成中性错误。"""
    from pet.brain.providers.gemini_provider import _GeminiStream
    stream = _GeminiStream(_BrokenStream(RuntimeError("reset")))

    assert next(iter(stream)).content == "a"
    with pytest.raises(ProviderConnection):
        next(iter(stream))


def test_stream_stop_iteration_passes_through():
    from pet.brain.providers.gemini_provider import _GeminiStream
    stream = _GeminiStream(_FakeStream([]))

    with pytest.raises(StopIteration):
        next(iter(stream))


class _FakeModels:
    """按调用记录参数并返回预置结果的 models 资源。"""

    def __init__(self, response=None, models=None, embeddings=None):
        self._response = response
        self._models = models or []
        self._embeddings = embeddings
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(("generate_content", kwargs))
        return self._response

    def generate_content_stream(self, **kwargs):
        self.calls.append(("generate_content_stream", kwargs))
        return _FakeStream(self._response)

    def embed_content(self, **kwargs):
        self.calls.append(("embed_content", kwargs))
        return self._embeddings

    def list(self):
        self.calls.append(("list", {}))
        return iter(self._models)


class _FakeClient:
    def __init__(self, models, has_close=True):
        self.models = models
        self.closed = False
        if has_close:
            self.close = self._close

    def _close(self):
        self.closed = True


class _FakeTypes:
    """只提供 GeminiProvider 构造请求体时用到的类型。"""

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

    class EmbedContentConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)


def _provider_with(client, model="gemini-3-flash", effort=""):
    provider = GeminiProvider.__new__(GeminiProvider)
    provider._types = _FakeTypes
    provider._client = client
    provider._effort = effort
    provider._thinking_unsupported = False
    provider._effort_unsupported = False
    provider.model = model
    provider.capabilities = Capabilities(
        vision=True, native_tools=True, thinking=True,
        embedding=True, model_list=True)
    return provider


def test_response_text_skips_thought_parts():
    from pet.brain.providers.gemini_provider import _response_text
    response = _response([_part("想", thought=True), _part("答")])
    assert _response_text(response) == "答"


def test_response_text_falls_back_to_text_property():
    from pet.brain.providers.gemini_provider import _response_text
    response = SimpleNamespace(candidates=[], text="兜底")
    assert _response_text(response) == "兜底"


def test_response_finish_without_candidates_is_stop():
    from pet.brain.providers.gemini_provider import _response_finish
    assert _response_finish(SimpleNamespace(candidates=None)) == FINISH_STOP


def test_to_completion_maps_calls_usage_and_finish():
    call = SimpleNamespace(name="timer__set", args={"s": 1})
    usage = SimpleNamespace(prompt_token_count=3, candidates_token_count=4,
                            total_token_count=7)
    response = SimpleNamespace(
        candidates=[SimpleNamespace(
            content=SimpleNamespace(parts=[_part("好")]), finish_reason="MAX_TOKENS")],
        function_calls=[call], usage_metadata=usage)
    provider = _provider_with(_FakeClient(_FakeModels()))
    result = provider._to_completion(response)
    assert result.content == "好"
    assert result.tool_calls[0].name == "timer__set"
    assert result.tool_calls[0].arguments == '{"s": 1}'
    assert result.finish_reason == FINISH_LENGTH
    assert result.usage == Usage(3, 4, 7)


def test_to_completion_without_calls_yields_empty_tuple():
    provider = _provider_with(_FakeClient(_FakeModels()))
    result = provider._to_completion(_response([_part("只有正文")]))
    assert result.tool_calls == ()
    assert result.finish_reason == FINISH_STOP


def test_embed_passes_output_dimensionality():
    embeddings = [SimpleNamespace(values=[1.0, 0.0])]
    models = _FakeModels(embeddings=SimpleNamespace(embeddings=embeddings))
    provider = _provider_with(_FakeClient(models))
    assert provider.embed(["甲"], 2) == [[1.0, 0.0]]
    _, kwargs = models.calls[0]
    assert kwargs["config"].output_dimensionality == 2


def test_embed_handles_missing_values():
    """个别响应的 values 为空，返回空列表而不是抛异常。"""
    models = _FakeModels(embeddings=SimpleNamespace(
        embeddings=[SimpleNamespace(values=None)]))
    provider = _provider_with(_FakeClient(models))
    assert provider.embed(["甲"], 2) == [[]]


def test_list_models_filters_embedding_only_models():
    models = _FakeModels(models=[
        SimpleNamespace(name="models/gemini-3-flash",
                        supported_actions=["generateContent"]),
        SimpleNamespace(name="models/text-embedding-004",
                        supported_actions=["embedContent"]),
        SimpleNamespace(name="models/gemini-2.5-pro", supported_actions=None),
    ])
    provider = _provider_with(_FakeClient(models))
    assert provider.list_models() == ["gemini-2.5-pro", "gemini-3-flash"]


def test_close_calls_sdk_close():
    client = _FakeClient(_FakeModels())
    _provider_with(client).close()
    assert client.closed is True


def test_close_is_noop_without_sdk_method():
    """SDK 没有 close 时不抛异常。"""
    provider = _provider_with(_FakeClient(_FakeModels(), has_close=False))
    provider.close()


def test_complete_wraps_sdk_error():
    class Boom(Exception):
        pass

    class BoomModels(_FakeModels):
        def generate_content(self, **kwargs):
            raise Boom("bad")

    provider = _provider_with(_FakeClient(BoomModels()))
    with pytest.raises(ProviderConnection):
        provider.complete([{"role": "user", "content": "在吗"}], max_tokens=8)


def test_stream_wraps_sdk_error():
    class Boom(Exception):
        pass

    class BoomModels(_FakeModels):
        def generate_content_stream(self, **kwargs):
            raise Boom("bad")

    provider = _provider_with(_FakeClient(BoomModels()))
    with pytest.raises(ProviderConnection):
        provider.stream([{"role": "user", "content": "在吗"}], max_tokens=8)


@pytest.mark.parametrize("raw, expected", [
    ("{}", {}),
    ('{"a": 1}', {"a": 1}),
    ("[1,2]", {}),
    ("not json", {}),
    (None, {}),
])
def test_loads_falls_back_to_empty_dict(raw, expected):
    from pet.brain.providers.gemini_provider import _loads
    assert _loads(raw) == expected


def test_complete_attaches_system_instruction():
    response = SimpleNamespace(
        candidates=[SimpleNamespace(
            content=SimpleNamespace(parts=[_part("好")]), finish_reason="STOP")],
        usage_metadata=None)
    models = _FakeModels(response=response)
    provider = _provider_with(_FakeClient(models))
    provider.complete([{"role": "system", "content": "人设"},
                       {"role": "user", "content": "在吗"}], max_tokens=16)
    _, kwargs = models.calls[0]
    assert kwargs["config"].system_instruction == "人设"
    assert kwargs["config"].max_output_tokens == 16
