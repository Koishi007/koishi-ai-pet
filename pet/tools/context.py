"""工具上下文 — 暴露宠物能力供工具主动调用。"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)


class ToolContext:
    """工具可调用的宠物能力接口（全局单例，启动时 bind）。"""

    def __init__(self):
        self._agent = None
        self._panels: dict[str, Callable] = {}
        self._pending_callbacks: list[Callable] = []

    def bind(self, agent):
        self._agent = agent
        logger.info("[ToolContext] Bound to agent")
        for cb in self._pending_callbacks:
            try:
                cb()
            except Exception:
                logger.exception("[ToolContext] post-bind callback error")
        self._pending_callbacks.clear()

    def _check_agent(self):
        if not self._agent:
            logger.warning("[ToolContext] No agent bound, skipped")
            return False
        return True

    def speech(self, text: str, duration: int = 5000):
        if self._check_agent():
            self._agent.speak_requested.emit(text, duration)

    def speech_random(self, texts: list[str], duration: int = 3000):
        """随机选择一条台词发射。"""
        import random
        self.speech(random.choice(texts), duration)

    def action(self, name: str, args: tuple = (), kwargs: dict = None):
        if self._check_agent():
            self._agent.action_requested.emit(name, args, kwargs or {})

    def add_context(self, text: str):
        if self._check_agent():
            self._agent.behavior.add_context(role="system", content=text)

    def request_interact(self, hint: str, delay_ms: int = 100,
                         cooldown_ms: int = 15000):
        if self._check_agent():
            self._agent.trigger("interact", hint=hint,
                                delay_ms=delay_ms, cooldown_ms=cooldown_ms)

    def notify(self, title: str, message: str, duration: int = 5000):
        if self._check_agent():
            self._agent.notify_requested.emit(title, message, duration)

    def register_tick(self, name: str, callback: Callable[[], None]):
        if self._check_agent():
            self._agent.scheduler.register(name, callback)

    def register_alarm(self, timestamp_ms: int, callback: Callable[[], None],
                       key: str | None = None):
        if self._check_agent():
            self._agent.scheduler.schedule_at(timestamp_ms, callback, key=key)

    def register_panel(self, tool_name: str,
                       factory: Callable[[], object]):
        self._panels[tool_name] = factory
        logger.info(f"[ToolContext] panel registered: {tool_name}")

    def get_panel_factory(self, tool_name: str) -> Callable | None:
        return self._panels.get(tool_name)

    def on_bind(self, callback: Callable[[], None]):
        if self._agent is not None:
            callback()
        else:
            self._pending_callbacks.append(callback)


TOOL_CTX = ToolContext()
