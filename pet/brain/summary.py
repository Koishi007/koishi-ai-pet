"""摘要流水线的执行端：把上下文淘汰产生的待摘要条目压成一句写回上下文。

有 LLM 就用 LLM 总结，不可用或调用失败时退化为拼接。
"""

import logging
from dataclasses import dataclass
from typing import Callable, List, Optional

from pet.config import config

logger = logging.getLogger(__name__)


@dataclass
class SummaryHooks:
    """摘要流水线需要的能力：队列、LLM 调用与写回，都由编排方注入。

    `summarize` 为 None 表示 LLM 不可用（未配 API key 等），直接走 `fallback`。
    """

    drain: Callable[[], List[str]]
    summarize: Optional[Callable[[List[str]], Optional[str]]]
    add_context: Callable[..., None]
    fallback: Callable[[List[str]], str]


def flush_summaries(hooks: SummaryHooks) -> None:
    """处理待摘要队列：LLM 优先，失败退回拼接，最后作为系统摘要写回上下文。"""
    items = hooks.drain()
    if not items:
        return

    logger.info(f"[Behavior] flushing pending summaries: {len(items)} items")
    summary = None
    if hooks.summarize is not None:
        try:
            summary = hooks.summarize(items)
        except Exception:
            logger.warning("[Behavior] LLM summarization failed, using fallback")

    if not summary:
        summary = hooks.fallback(items)

    if summary:
        hooks.add_context(role="system", content=f"[历史摘要] {summary}", is_summary=True)
        logger.info(f"[Behavior] flushed pending summaries: {len(items)} items → {summary[:50]}...")


def summarize_with_llm(messages: list, item_count: int, call_llm: Callable[..., object]) -> Optional[str]:
    """拿一条独立请求把历史条目压成一句摘要；空响应按不可用处理。

    call_llm(messages, max_tokens) 由编排方提供（含重试与备选方案切换）。
    """
    resp = call_llm(messages, max_tokens=config.LLM_MAX_TOKENS_SUMMARY)
    raw = resp.choices[0].message.content
    result = (raw or "").strip()
    if not result:
        logger.warning(f"[Behavior] LLM summarize returned empty content, raw={raw!r}, "
                       f"finish_reason={resp.choices[0].finish_reason}")
        return None
    logger.info(f"[Behavior] LLM summarized {item_count} items → {result}")
    return result
