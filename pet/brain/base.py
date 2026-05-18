from abc import ABC, abstractmethod


class BaseBrain(ABC):
    def __init__(self):
        self._context = []

    @abstractmethod
    def think(self, prompt: str) -> str:
        pass

    @abstractmethod
    def greet(self) -> str:
        pass

    def add_context(self, message: str):
        self._context.append(message)

    def clear_context(self):
        self._context.clear()
