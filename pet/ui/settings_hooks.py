"""设置页可选能力的注入点：装配层在启动时绑定真实实现"""

from __future__ import annotations

from typing import Callable

_embedding_factory: Callable[..., object] | None = None
_invalidate_action_section: Callable[[], None] | None = None


def bind(embedding_factory: Callable[..., object],
         invalidate_action_section: Callable[[], None]) -> None:
    global _embedding_factory, _invalidate_action_section
    _embedding_factory = embedding_factory
    _invalidate_action_section = invalidate_action_section


def make_embedding_client(url, key, model, dim):
    return _embedding_factory(url, key, model, dim)


def invalidate_action_section() -> None:
    _invalidate_action_section()
