"""技能上下文 — 暴露宠物能力供插件主动调用。"""

import logging

logger = logging.getLogger(__name__)


class SkillContext:
    """插件可调用的宠物能力接口（全局单例，启动时 bind）。"""

    def __init__(self):
        self._agent = None

    def bind(self, agent):
        self._agent = agent
        logger.info("[SkillContext] Bound to agent")

    def _check_agent(self):
        if not self._agent:
            logger.warning("[SkillContext] No agent bound, skipped")
            return False
        return True

    def speech(self, text: str, duration: int = 5000):
        if self._check_agent():
            self._agent.speak_requested.emit(text, duration)

    def action(self, name: str, args: tuple = (), kwargs: dict = None):
        if self._check_agent():
            self._agent.action_requested.emit(name, args, kwargs or {})

    def add_context(self, text: str):
        if self._check_agent():
            self._agent.behavior.add_context(role="system", content=text)


SKILL_CTX = SkillContext()
