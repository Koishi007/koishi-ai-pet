"""行为决策 brain —— 决定宠物的动作（动画、空闲行为）。"""

from openai import OpenAI
from config import config


class ActionBrain:
    """行为决策 brain。

    BRAIN=local 用随机本地规则，BRAIN=llm 调用 API 决策。
    """

    def __init__(self):
        brain = config.ACTION_BRAIN or "local"
        if brain == "llm" and config.ACTION_MODEL_KEY:
            self._client = OpenAI(
                api_key=config.ACTION_MODEL_KEY,
                base_url=config.ACTION_MODEL_URL or "",
            )
            self._model = config.ACTION_MODEL or config.CHAT_MODEL
        else:
            self._client = None

        self._actions = [
            "idle", "bounce", "walk", "look_around",
            "stretch", "sit", "sleep", "greet_user",
        ]

    def decide_action(self, context: str = "") -> str:
        """根据上下文决策下一个动作，返回动作名称。"""
        if self._client:
            return self._decide_remote(context)
        return self._decide_local()

    def _decide_remote(self, context: str) -> str:
        try:
            prompt = config.ACTION_PROMPT_DECIDE.format(
                actions=", ".join(self._actions),
                context=context or "no context",
            )
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=10,
            )
            action = resp.choices[0].message.content.strip().lower()
            if action in self._actions:
                return action
            return "idle"
        except Exception:
            return self._decide_local()

    def _decide_local(self) -> str:
        import random
        return random.choice(self._actions)
