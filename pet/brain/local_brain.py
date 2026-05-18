from pet.brain.base import BaseBrain


class LocalBrain(BaseBrain):
    def __init__(self):
        super().__init__()
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
        self._index = 0

    def think(self, prompt: str) -> str:
        if "greet" in prompt.lower():
            return self._rotate_response("greet")
        return self._rotate_response("idle")

    def greet(self) -> str:
        return self._rotate_response("greet")

    def _rotate_response(self, key: str) -> str:
        responses = self._responses.get(key, ["Hmm..."])
        resp = responses[self._index % len(responses)]
        self._index += 1
        return resp
