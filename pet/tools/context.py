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
        self._pending_callbacks: list[Callable] = []
        self._model_aside_pending = 0
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
        # 写入对话历史（含 tool_call 自带 aside），失败不影响播出
        store = getattr(self._agent, "conversation_store", None)
        if store is not None:
            try:
                store.add("pet", text)
            except Exception:
                pass

    def speech_random(self, texts: list[str], duration: int = 3000):
        """随机选择一条台词发射；模型已在 tool_call 里带 aside 时跳过（避免两句）。"""
        if self.is_model_aside_pending():
            return
        import random
        self.speech(random.choice(texts), duration)

    def push_model_aside_pending(self):
        """计数 +1：标记当前工具调用已播出模型 aside，供兜底台词抑制。

        并行工具调用时每个带 aside 的调用各 push 一次，pop 配对称量，
        避免布尔标志在并发下互相覆盖。
        """
        with self._speech_lock:
            self._model_aside_pending += 1

    def pop_model_aside_pending(self):
        """计数 -1：与 push 配对，工具执行结束后调用。"""
        with self._speech_lock:
            if self._model_aside_pending > 0:
                self._model_aside_pending -= 1

    def is_model_aside_pending(self) -> bool:
        return self._model_aside_pending > 0

    def action(self, name: str, args: tuple = (), kwargs: dict = None):
        if self._check_agent():
            self._agent.action_requested.emit(name, args, kwargs or {})

    def add_context(self, text: str):
        if self._check_agent():
            self._agent.behavior.add_context(role="system", content=text)

    def note_event(self, kind: str, text: str = ""):
        """工具侧上报事件，进入桌宠的「最近发生了什么」章节。

        kind: 事件类型（同类型只保留最近一次，建议按工具命名，如 "timer"、"game"）；
        text: 展示文案，可带动态参数（如「你设的「吃药」定时器响了」）。
        """
        if self._check_agent():
            self._agent.note_event(kind, text)

    def request_interact(self, hint: str, delay_ms: int = 100,
                         cooldown_ms: int = 15000,
                         thinking: bool | None = None,
                         enable_tools: bool | None = None):
        """请求一次即时交互；thinking / enable_tools 为 None 时沿用管家默认。

        可从任意线程调用：经信号转回主线程执行，QTimer 依赖主线程事件循环。
        """
        if self._check_agent():
            self._agent.tool_interact_requested.emit({
                "hint": hint,
                "delay_ms": delay_ms,
                "cooldown_ms": cooldown_ms,
                "thinking": thinking,
                "enable_tools": enable_tools,
            })

    def notify(self, title: str, message: str, duration: int = 5000):
        if self._check_agent():
            self._agent.notify_requested.emit(title, message, duration)

    def game_board(self, game_name: str, payload: dict):
        """通知 UI 渲染游戏棋盘（Qt 队列连接，跨线程安全）。"""
        if self._check_agent():
            self._agent.game_board_requested.emit(game_name, payload)

    def hide_game_board(self, game_name: str):
        """通知 UI 隐藏游戏棋盘面板。"""
        if self._check_agent():
            self._agent.game_board_requested.emit(game_name, {"action": "close"})

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
