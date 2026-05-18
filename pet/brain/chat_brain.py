"""对话 brain —— 主交互和聊天。"""

from openai import OpenAI
from pet.brain.base import BrainMixin
from config import config


class ChatBrain(BrainMixin):
    """对话 brain。

    BRAIN=llm 调用 API，BRAIN=local 用内置回复库。
    """

    def __init__(self):
        super().__init__()
        brain = config.CHAT_BRAIN or "local"
        if brain == "llm" and config.CHAT_MODEL_KEY:
            self._client = OpenAI(
                api_key=config.CHAT_MODEL_KEY,
                base_url=config.CHAT_MODEL_URL or "",
            )
            self._model = config.CHAT_MODEL or "deepseek-v4-pro"
        else:
            self._client = None
            self._responses = {
                "greet": [
                    "Hello! Nice to see you!",
                    "Hi there! How are you?",
                    "Hey! Good to be here!",
                ],
                "idle": [
                    "Just hanging out...",
                    "What a nice day!",
                    "Watching you work is fun!",
                ],
            }
            self._idx = 0


    def think(self, prompt: str) -> str:
        """思考并返回回复。"""
        if self._client:
            return self._think_remote(prompt)
        return self._think_local(prompt)

    def greet(self) -> str:
        """返回友好问候。"""
        return self.think(config.CHAT_PROMPT_GREET)


    def _think_remote(self, prompt: str) -> str:
        messages = [
            {"role": "system", "content": config.CHAT_PROMPT_SYSTEM}
        ]
        for ctx in self._context:
            messages.append({"role": "user", "content": ctx})
        messages.append({"role": "user", "content": prompt})

        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=100,
        )
        return response.choices[0].message.content or ""

    def _think_local(self, prompt: str) -> str:
        if "greet" in prompt.lower():
            return self._rotate("greet")
        return self._rotate("idle")

    def _rotate(self, key: str) -> str:
        responses = self._responses.get(key, ["Hmm..."])
        resp = responses[self._idx % len(responses)]
        self._idx += 1
        return resp
