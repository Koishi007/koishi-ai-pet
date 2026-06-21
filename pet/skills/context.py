"""技能上下文 — 暴露宠物能力供插件主动调用。"""

import logging
from typing import Callable

logger = logging.getLogger(__name__)


class SkillContext:
    """插件可调用的宠物能力接口（全局单例，启动时 bind）。"""

    def __init__(self):
        self._agent = None
        self._panels: dict[str, Callable] = {}

    def bind(self, agent):
        self._agent = agent
        logger.info("[SkillContext] Bound to agent")

    def _check_agent(self):
        if not self._agent:
            logger.warning("[SkillContext] No agent bound, skipped")
            return False
        return True

    # ── 现有方法 ──

    def speech(self, text: str, duration: int = 5000):
        if self._check_agent():
            self._agent.speak_requested.emit(text, duration)

    def action(self, name: str, args: tuple = (), kwargs: dict = None):
        if self._check_agent():
            self._agent.action_requested.emit(name, args, kwargs or {})

    def add_context(self, text: str):
        if self._check_agent():
            self._agent.behavior.add_context(role="system", content=text)

    # ── 新增方法 ──

    def request_interact(self, hint: str, delay_ms: int = 100,
                         cooldown_ms: int = 15000):
        """让插件请求一次 LLM 交互。LLM 收到 hint 后自行决策是否/如何回应。"""
        if self._check_agent():
            self._agent.trigger("interact", hint=hint,
                                delay_ms=delay_ms, cooldown_ms=cooldown_ms)

    def register_tick(self, name: str, callback: Callable[[], None]):
        """注册定时回调到 fast / mid / slow tick，内部转发给 Scheduler。"""
        if self._check_agent():
            self._agent.scheduler.register(name, callback)

    def register_alarm(self, timestamp_ms: int, callback: Callable[[], None]):
        """注册精确时刻一次性回调，内部转发给 Scheduler.schedule_at。"""
        if self._check_agent():
            self._agent.scheduler.schedule_at(timestamp_ms, callback)

    def register_panel(self, skill_name: str,
                       factory: Callable[[], object]):
        """注册独立面板工厂。点击右键菜单项时调用 factory() 弹出窗口。"""
        self._panels[skill_name] = factory
        logger.info(f"[SkillContext] panel registered: {skill_name}")

    def get_panel_factory(self, skill_name: str) -> Callable | None:
        """获取指定技能的 panel 工厂。"""
        return self._panels.get(skill_name)


SKILL_CTX = SkillContext()
