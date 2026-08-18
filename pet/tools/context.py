"""工具上下文 — 暴露宠物能力供工具主动调用。"""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)


class ToolContext:
    """工具可调用的宠物能力接口（全局单例，启动时 bind）。"""

    def __init__(self):
        self._agent = None
        self._panels: dict[str, Callable] = {}
        self._pending_callbacks: list[Callable] = []
        self._model_speech_pending = 0
        self._speech_lock = threading.Lock()

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
        if not self._check_agent():
            return
        self._agent.speak_requested.emit(text, duration)
        # 写入对话历史（含 tool_call 自带 speech），失败不影响播出
        store = getattr(self._agent, "conversation_store", None)
        if store is not None:
            try:
                store.add("pet", text)
            except Exception:
                pass

    def speech_random(self, texts: list[str], duration: int = 3000):
        """随机选择一条台词发射；模型已在 tool_call 里带 speech 时跳过（避免两句）。"""
        if self.is_model_speech_pending():
            return
        import random
        self.speech(random.choice(texts), duration)

    def push_model_speech_pending(self):
        """计数 +1：标记当前工具调用已播出模型 speech，供兜底台词抑制。

        并行工具调用时每个带 speech 的调用各 push 一次，pop 配对称量，
        避免布尔标志在并发下互相覆盖。
        """
        with self._speech_lock:
            self._model_speech_pending += 1

    def pop_model_speech_pending(self):
        """计数 -1：与 push 配对，工具执行结束后调用。"""
        with self._speech_lock:
            if self._model_speech_pending > 0:
                self._model_speech_pending -= 1

    def is_model_speech_pending(self) -> bool:
        return self._model_speech_pending > 0

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
                       key: str | None = None) -> str | None:
        if self._check_agent():
            return self._agent.scheduler.schedule_at(timestamp_ms, callback, key=key)
        return None

    def unregister_alarm(self, key: str):
        """取消一个已注册的一次性闹钟（幂等）。"""
        if self._check_agent():
            self._agent.scheduler.cancel_alarm_by_key(key)

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

    def db_path(self) -> str:
        """返回数据库路径，供工具使用。"""
        from pet.db import get_db_path
        return get_db_path()


TOOL_CTX = ToolContext()
