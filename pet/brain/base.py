class BrainMixin:
    """轻量级上下文管理 mixin — 按需混入具体 brain 类。"""

    def __init__(self):
        self._context: list[str] = []

    def add_context(self, message: str):
        self._context.append(message)

    def clear_context(self):
        self._context.clear()
