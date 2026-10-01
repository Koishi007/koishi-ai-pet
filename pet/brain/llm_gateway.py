"""LLM 调用封装：补全请求、流式建流与重试参数，供决策编排直接调用。"""

import logging
import time
from datetime import datetime

from openai import BadRequestError

from pet.config import config
from pet.brain.llm_retry import llm_retry, llm_stream_with_retry

logger = logging.getLogger(__name__)

# 服务商是否不支持 thinking 参数（首次 400 后自动降级，后续请求不再携带）
_thinking_unsupported = False


class LlmGateway:
    """一次决策里的所有 LLM 请求都经这里：非流式补全与流式建流。

    重试前切换备选模型由 on_retry 回调负责（实现留在编排方），
    进展上报由 note_progress 负责。
    """

    def __init__(self, client, stats, on_retry=None, note_progress=None):
        self._llm = client
        self._stats = stats
        self._on_retry = on_retry
        self._note_progress = note_progress or (lambda: None)

    def _retry_hook(self, exc: BaseException | None = None) -> bool:
        """包装重试回调：回调异常不影响重试流程。"""
        if self._on_retry is None:
            return False
        try:
            return bool(self._on_retry(exc))
        except Exception:
            logger.exception("[Behavior] retry hook failed")
            return False

    @staticmethod
    def _apply_thinking_param(kwargs: dict, thinking: bool | None = None):
        """按配置注入思考模式参数；thinking 非 None 时覆盖全局开关。"""
        if _thinking_unsupported:
            return
        if thinking is None:
            state = "disabled" if config.LLM_THINKING_DISABLED else "enabled"
        else:
            state = "enabled" if thinking else "disabled"
        kwargs.setdefault("extra_body", {})["thinking"] = {"type": state}

    def _create_completion(self, kwargs: dict):
        """发起补全请求；若服务商不支持 thinking 参数（400），自动移除后重试一次。"""
        global _thinking_unsupported
        try:
            return self._llm.client.chat.completions.create(**kwargs)
        except BadRequestError:
            if "extra_body" not in kwargs:
                raise
            logger.warning("[Behavior] 请求被拒绝(400)，可能不支持 thinking 参数，自动降级重试")
            kwargs.pop("extra_body", None)
            resp = self._llm.client.chat.completions.create(**kwargs)
            _thinking_unsupported = True
            return resp

    @llm_retry(tag="Behavior")
    def completion(self, messages: list, max_tokens: int = 4000, tools: list = None,
                   thinking: bool | None = None):
        """非流式补全。"""
        self._stats.increment()
        t0 = time.perf_counter()
        kwargs = {"model": self._llm.model, "messages": messages,
                  "max_tokens": max_tokens, "temperature": config.LLM_TEMPERATURE}
        if tools:
            kwargs["tools"] = tools
        self._apply_thinking_param(kwargs, thinking)
        resp = self._create_completion(kwargs)
        self._note_progress()
        elapsed = time.perf_counter() - t0
        usage = resp.usage
        if usage:
            logger.info(f"[Behavior] LLM call completed in {elapsed:.2f}s, "
                        f"tokens: prompt={usage.prompt_tokens}, completion={usage.completion_tokens}, total={usage.total_tokens}")
        else:
            logger.info(f"[Behavior] LLM call completed in {elapsed:.2f}s")
        return resp

    def stream_chat(self, messages: list, max_tokens: int = 4000, tools: list = None,
                    thinking: bool | None = None):
        """建流（含建流看门狗与重试）；返回的流由解析器消费。"""

        def _build_kwargs() -> dict:
            # 每次尝试都按当前生效方案重建参数，回退到备选后模型名随之更新
            kwargs = {"model": self._llm.model, "messages": messages, "max_tokens": max_tokens,
                      "temperature": config.LLM_TEMPERATURE, "stream": True,
                      "stream_options": {"include_usage": True}}
            if tools:
                kwargs["tools"] = tools
            self._apply_thinking_param(kwargs, thinking)
            return kwargs

        def _create():
            return self._create_completion(_build_kwargs())

        self._stats.increment()
        return llm_stream_with_retry(
            _create,
            tag="Behavior.stream",
            create_timeout=config.LLM_CREATE_TIMEOUT,
            on_retry=self._retry_hook,
        )

    def log_prompt_size(self, messages: list, tag: str):
        """计算并打印 prompt 规模：文本字符数 + 图片 base64 大小。"""
        text_chars = 0
        image_count = 0
        image_bytes = 0
        image_fmt = ""
        for m in messages:
            content = m["content"]
            if isinstance(content, str):
                text_chars += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_chars += len(part.get("text", ""))
                    elif isinstance(part, dict) and part.get("type") == "image_url":
                        image_count += 1
                        url = part.get("image_url", {}).get("url", "")
                        if "," in url:
                            image_bytes += len(url.split(",", 1)[1])
                            # 从 data:image/jpeg;base64,... 中提取格式
                            if not image_fmt and "data:image/" in url:
                                fmt_start = url.find("data:image/") + len("data:image/")
                                fmt_end = url.find(";", fmt_start)
                                if fmt_end > fmt_start:
                                    image_fmt = url[fmt_start:fmt_end]
        t = datetime.now().strftime("%H:%M:%S")
        parts = [f"prompt_chars: {text_chars}"]
        if image_count:
            parts.append(f"images: {image_count}{' (' + image_fmt + ')' if image_fmt else ''} ({image_bytes // 1024}KB base64)")
        logger.info(f"[{t}] [Behavior]   {', '.join(parts)} ({tag})")
