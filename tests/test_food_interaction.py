"""觅食交互的触发参数：进食不受通道冷却影响。"""

from pet.food.food import FOOD


class _AgentStub:
    def __init__(self):
        self.calls = []

    def trigger(self, intent, **kwargs):
        self.calls.append((intent, kwargs))


def test_self_fed_disables_cooldown(monkeypatch):
    agent = _AgentStub()
    monkeypatch.setattr(FOOD, "_agent", agent)

    FOOD._trigger_self_fed("蛋糕")

    intent, kwargs = agent.calls[0]
    assert intent == "interact"
    assert kwargs["cooldown_ms"] == 0
