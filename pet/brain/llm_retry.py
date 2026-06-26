"""LLM 调用重试与异常分类。"""

import logging
from functools import wraps
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception, before_sleep_log, RetryError,
)
from openai import (
    APIConnectionError, APITimeoutError, RateLimitError,
    InternalServerError, AuthenticationError, BadRequestError, NotFoundError,
)
from pet.config import config

logger = logging.getLogger(__name__)

RETRYABLE_EXCEPTIONS = (
    APIConnectionError, APITimeoutError, RateLimitError,
    InternalServerError, ConnectionError, TimeoutError, OSError,
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


def llm_stream_with_retry(create_stream_fn, tag: str = "LLM"):
    """流式调用的重试包装"""
    last_exception = None
    for attempt in range(config.LLM_MAX_RETRIES):
        try:
            stream = create_stream_fn()
            return stream
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
