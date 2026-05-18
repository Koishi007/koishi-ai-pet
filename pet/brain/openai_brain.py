from openai import OpenAI
from pet.brain.base import BaseBrain
from config import config


class OpenAIBrain(BaseBrain):
    def __init__(self):
        super().__init__()
        kwargs = {"api_key": config.OPENAI_API_KEY}
        if config.OPENAI_BASE_URL:
            kwargs["base_url"] = config.OPENAI_BASE_URL
        self._client = OpenAI(**kwargs)
        self._model = config.OPENAI_MODEL

    def think(self, prompt: str) -> str:
        messages = [
            {"role": "system", "content": "You are a cute desktop pet. Keep responses brief and playful."}
        ]
        for ctx in self._context:
            messages.append({"role": "user", "content": ctx})
        messages.append({"role": "user", "content": prompt})

        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=100,
        )
        return response.choices[0].message.content

    def greet(self) -> str:
        return self.think("Say a short, friendly greeting!")
