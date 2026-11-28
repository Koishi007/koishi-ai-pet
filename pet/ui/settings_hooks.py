"""设置页可选能力的注入点：装配层在启动时绑定真实实现"""

from __future__ import annotations

from typing import Callable

_embedding_factory: Callable[..., object] | None = None
_invalidate_action_section: Callable[[], None] | None = None
_reload_hotkey: Callable[[], None] | None = None


def bind(embedding_factory: Callable[..., object],
         invalidate_action_section: Callable[[], None],
         reload_hotkey: Callable[[], None] | None = None) -> None:
    global _embedding_factory, _invalidate_action_section, _reload_hotkey
    _embedding_factory = embedding_factory
    _invalidate_action_section = invalidate_action_section
    _reload_hotkey = reload_hotkey


def make_embedding_client(url, key, model, dim):
    """构建嵌入客户端。"""
    return _embedding_factory(url, key, model, dim)


def invalidate_action_section() -> None:
    _invalidate_action_section()


def reload_hotkey() -> None:
    """热键或触发方式变更后让监听器重新读取配置；未启用语音输入时为空操作。"""
    if _reload_hotkey is not None:
        _reload_hotkey()
