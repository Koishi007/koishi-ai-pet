"""Google Gemini 通道：消息编码、流式解码、异常映射与思考强度。

google-genai 属于必装依赖，仍只在构造时导入，避免模块级加载 SDK。
"""

import json
import logging
from typing import Iterator, Optional

from pet.brain.budget import thinking_active
from pet.brain.llm_errors import (ProviderAuthError, ProviderBadRequest,
                                  ProviderConnection, ProviderError,
                                  ProviderNotFound, ProviderRateLimit,
                                  ProviderServerError, ProviderTimeout)
from pet.brain.llm_types import (FINISH_CONTENT_FILTER, FINISH_LENGTH,
                                 FINISH_STOP, Completion, StreamEvent,
                                 ToolCall, ToolCallDelta, Usage)
from pet.brain.providers import Capabilities, LLMConnection
from pet.brain.reasoning import normalize

logger = logging.getLogger(__name__)

# 中性档位 -> thinking_level 取值；Gemini 只有四档，没有 xhigh 与 max
EFFORT_WIRE = {effort: effort for effort in ("minimal", "low", "medium", "high")}

# FinishReason 到中性四值；工具调用失败归 content_filter，走空响应与修复轮路径
_FINISH_MAP = {
    "STOP": FINISH_STOP,
    "MAX_TOKENS": FINISH_LENGTH,
    "SAFETY": FINISH_CONTENT_FILTER,
    "RECITATION": FINISH_CONTENT_FILTER,
    "BLOCKLIST": FINISH_CONTENT_FILTER,
    "PROHIBITED_CONTENT": FINISH_CONTENT_FILTER,
    "SPII": FINISH_CONTENT_FILTER,
    "IMAGE_SAFETY": FINISH_CONTENT_FILTER,
    "MALFORMED_FUNCTION_CALL": FINISH_CONTENT_FILTER,
    "UNEXPECTED_TOOL_CALL": FINISH_CONTENT_FILTER,
    "TOO_MANY_TOOL_CALLS": FINISH_CONTENT_FILTER,
}

_TEXT_PART = "text"
_IMAGE_PART = "image"


def _finish_of(value) -> str:
    """FinishReason 枚举或字符串映射为中性值。"""
    if value is None:
        return FINISH_STOP
    name = getattr(value, "name", None) or str(value)
    return _FINISH_MAP.get(name.upper(), FINISH_STOP)


def encode_contents(messages: list) -> tuple:
    """中性消息 -> (contents, system_instruction)；工具历史按 function_call 表达。

    function_response 按 name 配对，所以同一轮的同名并行调用只能靠顺序消歧：
    连续的多条 tool 消息会合并进同一条 user content，保持与 function_call 的
    出现顺序一致。拆成多条 content 会打断配对序列。

    调用 id 由 stream() 与 complete() 按各自的序号起点生成，历史里可能重复。
    查函数名时取最近一次出现，保证每条 function_response 对上它自己那一轮的
    function_call。
    """
    system_parts = []
    contents = []
    # 工具调用 id 到 (出现位置, 函数名)：id 跨轮可能重复，按位置取最近一次
    calls: dict = {}
    for position, message in enumerate(messages):
        for call in message.get("tool_calls") or ():
            if call.get("id"):
                calls.setdefault(call["id"], []).append((position, call.get("name", "")))

    for position, message in enumerate(messages):
        role = message.get("role")
        content = message.get("content")
        if role == "system":
            system_parts.append(_text_of(content))
            continue

        if role == "tool":
            response = {"function_response": {
                "name": _call_name(calls, message.get("tool_call_id", ""), position),
                "response": {"result": _text_of(content)},
            }}
            # 紧邻上一条也是工具结果时并入同一条 user content
            if contents and contents[-1]["role"] == "user" \
                    and contents[-1].get("tool_results"):
                contents[-1]["parts"].append(response)
            else:
                contents.append({"role": "user", "parts": [response],
                                 "tool_results": True})
            continue

        parts = []
        if isinstance(content, list):
            for part in content:
                if part.get("type") == _TEXT_PART:
                    parts.append({"text": part.get("text", "")})
                elif part.get("type") == _IMAGE_PART:
                    parts.append({"inline_data": {
                        "mime_type": part.get("mime", "image/jpeg"),
                        "data": part.get("data", ""),
                    }})
        elif content:
            parts.append({"text": content})

        for call in message.get("tool_calls") or ():
            parts.append({"function_call": {
                "name": call.get("name", ""),
                "args": _loads(call.get("arguments")),
            }})

        if not parts:
            continue
        # Gemini 只有 user 与 model 两个角色
        contents.append({"role": "model" if role == "assistant" else "user",
                         "parts": parts})
    return _strip_markers(contents), "\n".join(p for p in system_parts if p)


def _call_name(calls: dict, call_id: str, position: int) -> str:
    """按位置取该 id 最近一次出现在此之前的函数名；找不到时回落到 tool。"""
    best = ""
    for seen_at, name in calls.get(call_id) or ():
        if seen_at < position:
            best = name
    return best or "tool"


def _strip_markers(contents: list) -> list:
    """清掉合并用的内部标记，SDK 只认 role 与 parts。"""
    for item in contents:
        item.pop("tool_results", None)
    return contents


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text", "")) for p in content
                       if isinstance(p, dict) and p.get("type") == _TEXT_PART)
    return ""


def _loads(raw) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def encode_tools(tools: Optional[list]):
    """中性工具声明 -> FunctionDeclaration 列表。"""
    if not tools:
        return None
    return [{"name": spec.get("name", ""),
             "description": spec.get("description", ""),
             "parameters_json_schema": spec.get("parameters") or {"type": "object"}}
            for spec in tools]


def _usage_of(metadata) -> Optional[Usage]:
    if metadata is None:
        return None
    return Usage(
        prompt_tokens=getattr(metadata, "prompt_token_count", 0) or 0,
        completion_tokens=getattr(metadata, "candidates_token_count", 0) or 0,
        total_tokens=getattr(metadata, "total_token_count", 0) or 0,
    )


class GeminiProvider:
    """Gemini 官方接口通道。"""

    name = "gemini"

    def __init__(self, connection: LLMConnection, timeout: float | None = None,
                 *, reasoning_effort: str = ""):
        from google import genai
        from google.genai import types

        self._types = types
        self._effort = normalize(reasoning_effort)
        self.model = connection.model
        # 嵌入固定走 OpenAI 兼容端点，见 pet/brain/embedding_client.py
        self.capabilities = Capabilities(
            vision=True, native_tools=True, thinking=True,
            embedding=False, model_list=True,
        )
        self._thinking_unsupported = False
        # 端点拒绝档位后置位：该模型按默认档位处理
        self._effort_unsupported = False
        # HttpOptions.timeout 的单位是毫秒；base_url 只能给根地址，SDK 会自行追加 /v1
        http_options = types.HttpOptions(
            timeout=int((timeout or 60.0) * 1000),
            base_url=connection.url.strip() or None,
        )
        self._client = genai.Client(api_key=connection.key or None,
                                    http_options=http_options)

    def _config(self, max_tokens: int, tools: Optional[list],
                thinking: Optional[bool], temperature: Optional[float]):
        types = self._types
        kwargs = {"max_output_tokens": max_tokens}
        if temperature is not None:
            kwargs["temperature"] = temperature
        declarations = encode_tools(tools)
        if declarations:
            kwargs["tools"] = [types.Tool(function_declarations=[
                types.FunctionDeclaration(**d) for d in declarations])]
        thinking_config = self._thinking_config(thinking)
        if thinking_config is not None:
            kwargs["thinking_config"] = thinking_config
        return types.GenerateContentConfig(**kwargs)

    def _thinking_config(self, thinking: Optional[bool]):
        """思考参数；关闭时下发 0，开启时按档位下发，未选档位则交给模型默认。"""
        types = self._types
        if self._thinking_unsupported or not thinking_active(thinking):
            return types.ThinkingConfig(thinking_budget=0)
        wire = EFFORT_WIRE.get(self._effort)
        if wire and not self._effort_unsupported:
            return types.ThinkingConfig(thinking_level=wire)
        return None

    def reasoning_effort_rejected(self) -> bool:
        """该实例是否已因端点拒绝而停发推理强度。"""
        return self._effort_unsupported

    def _create(self, call, thinking: Optional[bool]):
        """发请求；推理强度被端点拒绝时去掉重发一次并记住。"""
        try:
            return call()
        except Exception as exc:
            error = _map_error(exc)
            if not self._effort_retryable(thinking, error):
                raise error from exc
            logger.warning("[Provider] 端点拒绝推理强度参数，去掉后重试")
            self._effort_unsupported = True
            try:
                return call()
            except Exception as retry_exc:
                raise _map_error(retry_exc) from retry_exc

    def _effort_retryable(self, thinking: Optional[bool], error: ProviderError) -> bool:
        """本次请求确实带了档位，且端点拒绝指向它，才值得重发。"""
        if self._effort_unsupported or not self._effort:
            return False
        if not thinking_active(thinking):
            return False
        return self.thinking_rejected(error)

    def complete(self, messages: list, *, max_tokens: int, tools: Optional[list] = None,
                 thinking: Optional[bool] = None,
                 temperature: Optional[float] = None) -> Completion:
        contents, system = encode_contents(messages)

        def _call():
            config = self._config(max_tokens, tools, thinking, temperature)
            if system:
                config.system_instruction = system
            return self._client.models.generate_content(
                model=self.model, contents=contents, config=config)

        return self._to_completion(self._create(_call, thinking))

    def _to_completion(self, response) -> Completion:
        """非流式结果；id 序号从 0 起，与流式路径的起点区分。"""
        text = _response_text(response)
        calls = tuple(
            ToolCall(id=f"call_{i}", name=call.name,
                     arguments=_dumps(getattr(call, "args", None)))
            for i, call in enumerate(getattr(response, "function_calls", None) or ())
        )
        return Completion(content=text, tool_calls=calls,
                          finish_reason=_response_finish(response),
                          usage=_usage_of(getattr(response, "usage_metadata", None)))

    def stream(self, messages: list, *, max_tokens: int, tools: Optional[list] = None,
               thinking: Optional[bool] = None,
               temperature: Optional[float] = None) -> Iterator[StreamEvent]:
        """调用即建连并返回带 close() 的迭代器。"""
        contents, system = encode_contents(messages)

        def _call():
            config = self._config(max_tokens, tools, thinking, temperature)
            if system:
                config.system_instruction = system
            return self._client.models.generate_content_stream(
                model=self.model, contents=contents, config=config)

        return _GeminiStream(self._create(_call, thinking))

    def thinking_rejected(self, exc: ProviderError) -> bool:
        """400 是否指向思考参数。"""
        if not isinstance(exc, ProviderBadRequest):
            return False
        text = f"{exc.body}{exc}".lower()
        return "thinking" in text or "thinking_config" in text

    def embed(self, texts: list, dim: int) -> list:
        types = self._types
        try:
            response = self._client.models.embed_content(
                model=self.model, contents=list(texts),
                config=types.EmbedContentConfig(output_dimensionality=dim))
        except Exception as exc:
            raise _map_error(exc) from exc
        return [list(item.values or ()) for item in (response.embeddings or ())]

    def list_models(self) -> list:
        try:
            models = self._client.models.list()
        except Exception as exc:
            raise _map_error(exc) from exc
        out = []
        for model in models:
            actions = getattr(model, "supported_actions", None)
            if actions and "generateContent" not in actions:
                continue  # 嵌入等专用模型不进对话下拉
            name = (getattr(model, "name", "") or "").removeprefix("models/")
            if name:
                out.append(name)
        return sorted(out)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close is None:
            return
        try:
            close()
        except Exception:
            logger.debug("[Provider] gemini close failed", exc_info=True)


def _dumps(value) -> str:
    try:
        return json.dumps(value or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def _response_text(response) -> str:
    """正文；跳过 thought part，思考内容不进台词。"""
    pieces = []
    for candidate in getattr(response, "candidates", None) or ():
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or ():
            if getattr(part, "thought", False):
                continue
            text = getattr(part, "text", None)
            if text:
                pieces.append(text)
    if pieces:
        return "".join(pieces)
    return getattr(response, "text", "") or ""


def _response_finish(response) -> str:
    candidates = getattr(response, "candidates", None) or ()
    if not candidates:
        return FINISH_STOP
    return _finish_of(getattr(candidates[0], "finish_reason", None))


def _map_error(exc: BaseException) -> ProviderError:
    """把 google-genai 异常映射到中性错误族。"""
    status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    message = str(exc)
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return ProviderTimeout(message)
    if "connect" in name or "transport" in name:
        return ProviderConnection(message)
    if isinstance(status, int):
        if status in (401, 403):
            return ProviderAuthError(message, status_code=status)
        if status == 404:
            return ProviderNotFound(message, status_code=status)
        if status == 429:
            return ProviderRateLimit(message, status_code=status)
        if status >= 500:
            return ProviderServerError(message, status_code=status)
        return ProviderBadRequest(message, status_code=status)
    return ProviderConnection(f"{type(exc).__name__}: {exc}")


class _GeminiStream:
    """包装 SDK 流，按 part 产出 StreamEvent 并透传 close()。

    一个响应块的多个 function_call 排队逐条交出；迭代期异常转成中性错误。
    """

    def __init__(self, raw):
        self._raw = raw
        self._it = iter(raw)
        self._closed = False
        self._pending: list[StreamEvent] = []
        # 序号从 1 起：0 留给非流式 complete()，两条路径的 id 不会撞
        self._index = 1

    def __iter__(self):
        return self

    def __next__(self) -> StreamEvent:
        if not self._pending:
            try:
                response = next(self._it)
            except StopIteration:
                raise
            except Exception as exc:
                raise _map_error(exc) from exc
            self._pending = self._convert(response)
        return self._pending.pop(0)

    def _convert(self, response) -> list[StreamEvent]:
        """一个响应块转成若干中性事件：全部正文与思考合成一条，每个函数调用各一条。"""
        content, reasoning = [], []
        calls: list[StreamEvent] = []
        for candidate in getattr(response, "candidates", None) or ():
            for part in getattr(getattr(candidate, "content", None), "parts", None) or ():
                call = getattr(part, "function_call", None)
                if call is not None:
                    calls.append(StreamEvent(tool_call=ToolCallDelta(
                        index=self._index,
                        id=f"call_{self._index}",
                        name=getattr(call, "name", "") or "",
                        arguments=_dumps(getattr(call, "args", None)),
                        complete=True,
                    )))
                    self._index += 1
                    continue
                text = getattr(part, "text", None)
                if not text:
                    continue
                if getattr(part, "thought", False):
                    reasoning.append(text)
                else:
                    content.append(text)
        finish = None
        candidates = getattr(response, "candidates", None) or ()
        if candidates and getattr(candidates[0], "finish_reason", None):
            finish = _finish_of(candidates[0].finish_reason)
        usage = _usage_of(getattr(response, "usage_metadata", None))
        events = calls or [StreamEvent()]
        # 正文、结束原因与用量挂在最后一条事件上，流结束的判定只在一处
        last = events[-1]
        events[-1] = StreamEvent(
            content=last.content + "".join(content),
            reasoning=last.reasoning + "".join(reasoning),
            tool_call=last.tool_call,
            finish_reason=finish,
            usage=usage,
        )
        return events

    def close(self):
        if self._closed:
            return
        self._closed = True
        close = getattr(self._raw, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                logger.debug("[Provider] gemini stream close failed", exc_info=True)
