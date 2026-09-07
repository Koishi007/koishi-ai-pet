"""LLM 调用重试与异常分类。"""

import logging
import queue
import threading
from functools import wraps
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception, before_sleep_log, RetryError,
)
from openai import (
    APIConnectionError, APITimeoutError, RateLimitError,
    InternalServerError, AuthenticationError, BadRequestError, NotFoundError,
)
import httpx

from pet.config import config

logger = logging.getLogger(__name__)

RETRYABLE_EXCEPTIONS = (
    APIConnectionError, APITimeoutError, RateLimitError,
    InternalServerError, ConnectionError, TimeoutError, OSError,
    httpx.RemoteProtocolError,  # 服务端非正常断开连接，重试通常有效
    httpx.ReadTimeout,          # 流式读取超时
)
NON_RETRYABLE_EXCEPTIONS = (
    AuthenticationError, BadRequestError, NotFoundError,
)


def is_retryable(exception: BaseException) -> bool:
    """判断异常是否可重试。"""
    if isinstance(exception, NON_RETRYABLE_EXCEPTIONS):
        return False
    if isinstance(exception, RETRYABLE_EXCEPTIONS):
        return True
    return False


def llm_retry(tag: str = "LLM"):
    """非流式 LLM 调用的重试装饰器。"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            retryer = retry(
                stop=stop_after_attempt(config.LLM_MAX_RETRIES),
                wait=wait_exponential(
                    multiplier=config.LLM_RETRY_DELAY,
                    max=config.LLM_RETRY_MAX_DELAY,
                ),
                retry=retry_if_exception(is_retryable),
                before_sleep=before_sleep_log(logger, logging.WARNING),
                reraise=True,
            )
            try:
                return retryer(func)(*args, **kwargs)
            except RetryError as e:
                logger.error(
                    f"[{tag}] retries exhausted: "
                    f"{type(e.last_attempt.exception()).__name__}: "
                    f"{e.last_attempt.exception()}"
                )
                raise e.last_attempt.exception() from e
        return wrapper
    return decorator


class CreateStreamTimeout(TimeoutError):
    """建流阶段（create() 等待响应头）看门狗超时。"""


def _safe_close(stream) -> None:
    """尽力关闭被放弃的流式响应，避免连接不归还连接池。"""
    close = getattr(stream, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception as e:
        logger.debug(f"[LLM] close abandoned stream failed: {type(e).__name__}: {e}")


def _create_with_watchdog(create_stream_fn, timeout: float):
    """在后台线程执行 create()，等待响应头超过 timeout 即放弃"""
    result_q: queue.Queue = queue.Queue()
    abandoned = threading.Event()

    def _run():
        try:
            stream = create_stream_fn()
        except BaseException as e:  # 原样回传给调用方处理
            result_q.put(("err", e))
            return
        if abandoned.is_set():
            _safe_close(stream)
            return
        result_q.put(("ok", stream))

    t = threading.Thread(target=_run, daemon=True, name="llm-create-watchdog")
    t.start()
    try:
        kind, payload = result_q.get(timeout=timeout)
    except queue.Empty:
        abandoned.set()
        # 竞态：create() 可能在 abandoned 置位前刚入队，补收后关闭
        try:
            kind, payload = result_q.get_nowait()
        except queue.Empty:
            pass
        else:
            if kind == "ok":
                _safe_close(payload)
        raise CreateStreamTimeout(
            f"建流超时：create() 等待响应头超过 {timeout:.0f}s（服务端无响应或慢滴）"
        ) from None
    if kind == "err":
        raise payload
    return payload


def llm_stream_with_retry(create_stream_fn, tag: str = "LLM",
                          create_timeout: float | None = None):
    """流式调用的重试包装"""
    last_exception = None
    for attempt in range(config.LLM_MAX_RETRIES):
        try:
            if create_timeout and create_timeout > 0:
                stream = _create_with_watchdog(create_stream_fn, create_timeout)
            else:
                stream = create_stream_fn()
            return stream
        except CreateStreamTimeout:
            logger.error(
                f"[{tag}] stream create watchdog timeout "
                f"({create_timeout:.0f}s), giving up"
            )
            raise
        except Exception as e:
            last_exception = e
            if not is_retryable(e):
                raise
            delay = min(
                config.LLM_RETRY_DELAY * (2 ** attempt),
                config.LLM_RETRY_MAX_DELAY,
            )
            logger.warning(
                f"[{tag}] stream connect failed (attempt {attempt+1}/"
                f"{config.LLM_MAX_RETRIES}): {type(e).__name__}: {e}, "
                f"retrying in {delay:.1f}s"
            )
            import time
            time.sleep(delay)

    raise last_exception
