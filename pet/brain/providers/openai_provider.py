"""OpenAI 兼容通道：消息编码、流式解码、异常映射与思考降级判据。"""

import logging
from typing import Iterator, Optional

import httpx
from openai import (APIConnectionError, APITimeoutError, APIStatusError,
                    AuthenticationError, BadRequestError, InternalServerError,
                    NotFoundError, OpenAI, PermissionDeniedError, RateLimitError,
                    UnprocessableEntityError)

from pet.brain.budget import thinking_active
from pet.brain.llm_errors import (ProviderAuthError, ProviderBadRequest,
                                  ProviderConnection, ProviderError,
                                  ProviderNotFound, ProviderRateLimit,
                                  ProviderServerError, ProviderTimeout)
from pet.brain.llm_types import (FINISH_STOP, Completion, StreamEvent, ToolCall,
                                 ToolCallDelta, Usage)
from pet.brain.providers import Capabilities, LLMConnection
from pet.brain.reasoning import EFFORT_IDS, normalize

logger = logging.getLogger(__name__)

# 中性档位 -> 本通道协议值；OpenAI 兼容端点使用同名取值
EFFORT_WIRE = {effort: effort for effort in EFFORT_IDS}

# 400 消息里指向推理强度的词；判定先于 thinking
_EFFORT_WORD = "effort"

# 中性图片片段的类型名
_IMAGE_PART = "image"

# 结束原因：OpenAI 原值直接落在中性四值里，不认识的按 stop 处理
_FINISH_ALLOWED = {"stop", "length", "content_filter", "tool_calls"}


def _by_status(status, message: str, body: str) -> ProviderError:
    """按 HTTP 状态码归类，供只带 status_code 的通用异常使用。"""
    if status in (401, 403):
        return ProviderAuthError(message, status_code=status, body=body)
    if status == 404:
        return ProviderNotFound(message, status_code=status, body=body)
    if status == 429:
        return ProviderRateLimit(message, status_code=status, body=body)
    if status and status >= 500:
        return ProviderServerError(message, status_code=status, body=body)
    return ProviderBadRequest(message, status_code=status, body=body)


def _map_error(exc: BaseException) -> ProviderError:
    """把 openai SDK 异常映射到中性错误族。"""
    status = getattr(exc, "status_code", None)
    body = str(getattr(exc, "body", "") or "")
    message = str(exc)
    if isinstance(exc, AuthenticationError):
        return ProviderAuthError(message, status_code=status or 401, body=body)
    if isinstance(exc, PermissionDeniedError):
        return ProviderAuthError(message, status_code=status or 403, body=body)
    if isinstance(exc, (BadRequestError, UnprocessableEntityError)):
        return ProviderBadRequest(message, status_code=status or 400, body=body)
    if isinstance(exc, NotFoundError):
        return ProviderNotFound(message, status_code=status or 404, body=body)
    if isinstance(exc, RateLimitError):
        return ProviderRateLimit(message, status_code=status or 429, body=body)
    if isinstance(exc, InternalServerError):
        return ProviderServerError(message, status_code=status or 500, body=body)
    if isinstance(exc, APITimeoutError):
        return ProviderTimeout(message)
    if isinstance(exc, APIConnectionError):
        return ProviderConnection(message)
    if isinstance(exc, APIStatusError):
        return _by_status(status, message, body)
    if isinstance(exc, (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException)):
        return ProviderTimeout(message)
    if isinstance(exc, (httpx.RemoteProtocolError, httpx.TransportError)):
        return ProviderConnection(message)
    return ProviderConnection(f"{type(exc).__name__}: {exc}")


def encode_messages(messages: list) -> list:
    """中性消息 -> OpenAI messages；图片片段转成 image_url 的 data URI。"""
    out = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            for part in content:
                kind = part.get("type")
                if kind == "text":
                    item = {"type": "text", "text": part.get("text", "")}
                    if part.get("cache_control"):
                        item["cache_control"] = part["cache_control"]
                    parts.append(item)
                elif kind == _IMAGE_PART:
                    parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{part.get('mime', 'image/jpeg')};base64,"
                                   f"{part.get('data', '')}"
                        },
                    })
            out.append({**{k: v for k, v in message.items() if k != "content"},
                        "content": parts})
            continue
        encoded = dict(message)
        calls = encoded.pop("tool_calls", None)
        if calls:
            encoded["tool_calls"] = [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["name"], "arguments": c.get("arguments") or "{}"}}
                for c in calls
            ]
        out.append(encoded)
    return out


def encode_tools(tools: Optional[list]) -> Optional[list]:
    """中性工具声明 -> OpenAI function calling 格式。"""
    if not tools:
        return None
    return [{"type": "function", "function": spec} for spec in tools]


def _usage_of(raw) -> Optional[Usage]:
    if raw is None:
        return None
    return Usage(prompt_tokens=getattr(raw, "prompt_tokens", 0) or 0,
                 completion_tokens=getattr(raw, "completion_tokens", 0) or 0,
                 total_tokens=getattr(raw, "total_tokens", 0) or 0)


def _finish_of(value) -> str:
    return value if value in _FINISH_ALLOWED else FINISH_STOP


def _without_field(kwargs: dict, field: str) -> dict:
    """返回去掉 extra_body 中对应字段的请求参数副本。"""
    key = "reasoning_effort" if field == "effort" else "thinking"
    body = dict(kwargs.get("extra_body") or {})
    body.pop(key, None)
    updated = {k: v for k, v in kwargs.items() if k != "extra_body"}
    if body:
        updated["extra_body"] = body
    return updated


class OpenAIProvider:
    """OpenAI 兼容通道，含 Ollama。"""

    name = "openai"

    def __init__(self, connection: LLMConnection, timeout: float | None = None,
                 *, reasoning_effort: str = ""):
        self.model = connection.model
        self.capabilities = Capabilities(
            vision=True, native_tools=True, thinking=True,
            embedding=True, model_list=True,
        )
        self._timeout = timeout
        self._effort = normalize(reasoning_effort)
        # 端点拒绝对应字段后置位，只影响本实例
        self._thinking_unsupported = False
        self._effort_unsupported = False
        # 重试由 pet/brain/llm_retry.py 统一负责，SDK 内建重试会与之叠乘
        self._client = OpenAI(api_key=connection.key or "empty",
                              base_url=connection.url or "",
                              timeout=connection_timeout(self._timeout or 60.0),
                              max_retries=0)

    def _extra_body(self, thinking: Optional[bool]) -> dict:
        """思考开关与推理强度；关闭思考时不带档位。"""
        active = thinking_active(thinking)
        body = {}
        if self.capabilities.thinking and not self._thinking_unsupported:
            body["thinking"] = {"type": "enabled" if active else "disabled"}
        wire = EFFORT_WIRE.get(self._effort)
        if wire and active and not self._effort_unsupported:
            body["reasoning_effort"] = wire
        return body

    def _kwargs(self, messages: list, max_tokens: int, tools: Optional[list],
                thinking: Optional[bool], temperature: Optional[float]) -> dict:
        kwargs = {
            "model": self.model,
            "messages": encode_messages(messages),
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        encoded_tools = encode_tools(tools)
        if encoded_tools:
            kwargs["tools"] = encoded_tools
        body = self._extra_body(thinking)
        if body:
            kwargs["extra_body"] = body
        return kwargs

    def complete(self, messages: list, *, max_tokens: int, tools: Optional[list] = None,
                 thinking: Optional[bool] = None,
                 temperature: Optional[float] = None) -> Completion:
        kwargs = self._kwargs(messages, max_tokens, tools, thinking, temperature)
        try:
            resp = self._create(kwargs)
        except ProviderError:
            raise
        choice = resp.choices[0]
        message = choice.message
        calls = tuple(
            ToolCall(id=tc.id, name=tc.function.name,
                     arguments=tc.function.arguments or "{}")
            for tc in (message.tool_calls or ())
        )
        return Completion(content=message.content or "", tool_calls=calls,
                          finish_reason=_finish_of(choice.finish_reason),
                          usage=_usage_of(getattr(resp, "usage", None)))

    def _create(self, kwargs: dict):
        """发请求；档位或 thinking 字段被拒时逐个去掉重发，字段各一次。"""
        while True:
            try:
                return self._client.chat.completions.create(**kwargs)
            except (BadRequestError, UnprocessableEntityError) as exc:
                error = _map_error(exc)
                field = self._rejection_field(error)
                if not field or not self._drop_field(field, kwargs):
                    raise error from exc
                kwargs = _without_field(kwargs, field)
            except Exception as exc:
                raise _map_error(exc) from exc

    def _rejection_field(self, exc: ProviderError) -> str:
        """拒绝消息指向的字段：effort / thinking；都不匹配返回空串。"""
        if not isinstance(exc, ProviderBadRequest):
            return ""
        text = f"{exc.body}{exc}".lower()
        if _EFFORT_WORD in text:
            return "effort"
        if "thinking" in text:
            return "thinking"
        return ""

    def _drop_field(self, field: str, kwargs: dict) -> bool:
        """标记该字段不再下发；已标记或本次请求没带时返回假。"""
        body = kwargs.get("extra_body") or {}
        if field == "effort":
            if self._effort_unsupported or "reasoning_effort" not in body:
                return False
            self._effort_unsupported = True
            logger.warning("[Provider] 端点拒绝推理强度参数，去掉后重试")
            return True
        if self._thinking_unsupported or "thinking" not in body:
            return False
        self._thinking_unsupported = True
        logger.warning("[Provider] 端点拒绝 thinking 参数，去掉后重试")
        return True

    def stream(self, messages: list, *, max_tokens: int, tools: Optional[list] = None,
               thinking: Optional[bool] = None,
               temperature: Optional[float] = None) -> Iterator[StreamEvent]:
        """调用即建连并返回带 close() 的迭代器；不能写成 generator。"""
        kwargs = self._kwargs(messages, max_tokens, tools, thinking, temperature)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        try:
            raw = self._create(kwargs)
        except ProviderError:
            raise
        return _OpenAIStream(raw)

    def reasoning_effort_rejected(self) -> bool:
        """该实例是否已因端点拒绝而停发推理强度。"""
        return self._effort_unsupported

    def thinking_rejected(self, exc: ProviderError) -> bool:
        """400 是否指向 thinking 参数。"""
        return self._rejection_field(exc) == "thinking"

    def embed(self, texts: list, dim: int) -> list:
        try:
            resp = self._client.embeddings.create(model=self.model, input=texts,
                                                  dimensions=dim)
        except Exception as exc:
            raise _map_error(exc) from exc
        data = sorted(resp.data, key=lambda d: d.index)
        return [list(d.embedding) for d in data]

    def list_models(self) -> list:
        try:
            resp = self._client.models.list()
        except Exception as exc:
            raise _map_error(exc) from exc
        return sorted(m.id for m in resp.data)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            logger.debug("[Provider] close failed", exc_info=True)


def connection_timeout(read: float):
    """构造 httpx 超时；read 由调用方从配置取。"""
    return httpx.Timeout(connect=10.0, read=read, write=10.0, pool=5.0)


def _convert_chunk(chunk) -> list[StreamEvent]:
    """一个 chunk 转成若干中性事件：正文、思考、结束原因、用量，以及每个工具调用各一条。"""
    usage = _usage_of(getattr(chunk, "usage", None))
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return [StreamEvent(usage=usage)] if usage else [StreamEvent()]
    choice = choices[0]
    delta = choice.delta
    finish = _finish_of(choice.finish_reason) if choice.finish_reason else None
    content = getattr(delta, "content", "") or ""
    reasoning = getattr(delta, "reasoning_content", "") or ""
    events = [StreamEvent(content=content, reasoning=reasoning)] if content or reasoning else []
    events.extend(
        StreamEvent(tool_call=ToolCallDelta(
            index=getattr(tc, "index", 0) or 0,
            id=getattr(tc, "id", "") or "",
            name=getattr(getattr(tc, "function", None), "name", "") or "",
            arguments=getattr(getattr(tc, "function", None), "arguments", "") or "",
        ))
        for tc in (getattr(delta, "tool_calls", None) or ())
    )
    if not events:
        events.append(StreamEvent())
    # 结束原因与用量挂在最后一条事件上，流结束的判定只在一处
    last = events[-1]
    events[-1] = StreamEvent(content=last.content, reasoning=last.reasoning,
                             tool_call=last.tool_call, finish_reason=finish,
                             usage=usage)
    return events


class _OpenAIStream:
    """包装 OpenAI 流对象，产出 StreamEvent 并透传 close()。

    一个 chunk 的多个工具调用排队逐条交出；迭代期异常转成中性错误。
    """

    def __init__(self, raw):
        self._raw = raw
        self._it = iter(raw)
        self._closed = False
        self._pending: list[StreamEvent] = []

    def __iter__(self):
        return self

    def __next__(self) -> StreamEvent:
        if not self._pending:
            try:
                chunk = next(self._it)
            except StopIteration:
                raise
            except Exception as exc:
                raise _map_error(exc) from exc
            self._pending = _convert_chunk(chunk)
        return self._pending.pop(0)

    def close(self):
        if self._closed:
            return
        self._closed = True
        close = getattr(self._raw, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                logger.debug("[Provider] stream close failed", exc_info=True)
