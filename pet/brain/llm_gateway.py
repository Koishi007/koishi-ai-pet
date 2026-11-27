"""LLM 调用封装：补全请求、流式建流与重试参数，供决策编排直接调用。"""

import logging
import time
from datetime import datetime

from pet.config import config
from pet.brain.llm_errors import ProviderBadRequest, ProviderError
from pet.brain.llm_retry import llm_stream_with_retry, retryer_for
from pet.brain.projection import degrade_messages, uses_native_tools
from pet.brain.tool_channel import default_state, is_tool_rejection

logger = logging.getLogger(__name__)


class LlmGateway:
    """一次决策里的所有 LLM 请求都经这里；切换备选与进展上报由回调负责。"""

    def __init__(self, client, stats, on_retry=None, note_progress=None):
        self._llm = client
        self._stats = stats
        self._on_retry = on_retry
        self._note_progress = note_progress or (lambda: None)
        self._channel = getattr(client, "tool_channel", None) or default_state()
        self._provider_name = getattr(client, "provider_name", None)

    def _retry_hook(self, exc: BaseException | None = None) -> bool:
        """包装重试回调：回调异常不影响重试流程。"""
        if self._on_retry is None:
            return False
        try:
            return bool(self._on_retry(exc))
        except Exception:
            logger.exception("[Behavior] retry hook failed")
            return False

    def _complete_once(self, messages: list, max_tokens: int = 4000, tools: list = None,
                       thinking: bool | None = None, temperature: float | None = None):
        self._stats.increment()
        t0 = time.perf_counter()
        provider = self._llm.provider
        if provider is None:
            raise ProviderError("没有可用的 provider")
        kwargs = {"max_tokens": max_tokens, "tools": tools, "thinking": thinking,
                  "temperature": (config.LLM_TEMPERATURE if temperature is None
                                  else temperature)}
        result = self._guard_tool_rejection(
            lambda msgs=None: provider.complete(messages if msgs is None else msgs,
                                                **kwargs),
            messages)
        self._note_progress()
        elapsed = time.perf_counter() - t0
        usage = result.usage
        if usage:
            logger.info(f"[Behavior] LLM call completed in {elapsed:.2f}s, "
                        f"tokens: prompt={usage.prompt_tokens}, "
                        f"completion={usage.completion_tokens}, total={usage.total_tokens}")
        else:
            logger.info(f"[Behavior] LLM call completed in {elapsed:.2f}s")
        return result

    def _guard_tool_rejection(self, call, messages: list):
        """发起请求；工具协议被拒时降一档重试一次。"""
        try:
            return call()
        except ProviderBadRequest as e:
            if is_tool_rejection(e, self._current_provider()):
                return self._retry_without_tool_channel(call, messages, e)
            raise

    def _current_provider(self) -> str:
        """当前生效通道名；调用方没暴露通道查询时返回空串，判据同时匹配两张词表。"""
        if self._provider_name is None:
            return ""
        try:
            return self._provider_name() or ""
        except Exception:
            logger.debug("[Behavior] provider name unavailable", exc_info=True)
            return ""

    def _retry_without_tool_channel(self, call, messages: list, exc: BaseException):
        """工具协议被拒：降一档通道后重试一次；缓存已低于 native 时只改写不记降档。"""
        current = self._channel.resolve()
        stale_native = current != "native" and uses_native_tools(messages)
        downgraded = current if stale_native else self._channel.mark_downgraded(current)
        if downgraded == current and not stale_native:
            raise exc
        rewritten = degrade_messages(messages, downgraded)
        if not rewritten:
            raise exc
        logger.warning(f"[Behavior] 端点拒绝工具协议，本次请求降级为 {downgraded} 重试")
        return call(rewritten)

    def complete(self, messages: list, max_tokens: int = 4000, tools: list = None,
                 thinking: bool | None = None, temperature: float | None = None):
        """非流式补全，含重试与备选方案切换。"""
        return retryer_for(tag="Behavior", on_retry=self._retry_hook)(self._complete_once)(
            messages, max_tokens=max_tokens, tools=tools, thinking=thinking,
            temperature=temperature)

    def stream(self, messages: list, max_tokens: int = 4000, tools: list = None,
               thinking: bool | None = None, temperature: float | None = None):
        """建流，含建流看门狗与重试；返回的迭代器由解析器消费。"""

        def _open_stream():
            # 每次尝试都取当前生效的 provider，回退后通道与模型随之更新
            provider = self._llm.provider
            if provider is None:
                raise ProviderError("没有可用的 provider")
            return self._guard_tool_rejection(
                lambda msgs=None: provider.stream(
                    messages if msgs is None else msgs,
                    max_tokens=max_tokens, tools=tools, thinking=thinking,
                    temperature=(config.LLM_TEMPERATURE if temperature is None
                                 else temperature)),
                messages)

        self._stats.increment()
        return llm_stream_with_retry(
            _open_stream,
            tag="Behavior.stream",
            create_timeout=config.LLM_CREATE_TIMEOUT,
            on_retry=self._retry_hook,
        )

    def log_prompt_size(self, messages: list, tag: str):
        """计算并打印 prompt 规模：文本字符数 + 图片 base64 大小。"""
        text_chars = 0
        image_count = 0
        image_bytes = 0
        image_mime = ""
        for m in messages:
            content = m.get("content")
            if isinstance(content, str):
                text_chars += len(content)
            elif isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        text_chars += len(part.get("text", ""))
                    elif part.get("type") == "image":
                        image_count += 1
                        image_bytes += len(part.get("data", "") or "")
                        image_mime = image_mime or part.get("mime", "")
        t = datetime.now().strftime("%H:%M:%S")
        parts = [f"prompt_chars: {text_chars}"]
        if image_count:
            suffix = f" ({image_mime})" if image_mime else ""
            parts.append(f"images: {image_count}{suffix} ({image_bytes // 1024}KB base64)")
        logger.info(f"[{t}] [Behavior]   {', '.join(parts)} ({tag})")
