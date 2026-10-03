"""定时任务：无意识化的触发与复位。"""

import time

from pet.agent.scheduled_tasks import ScheduledTasks
from pet.config import config


class _Queue:
    action: str | None = None

    def current_action_name(self):
        return self.action


class _Gravity:
    falling = False


class _Actions:
    def __init__(self):
        self.gravity = _Gravity()
        self.played: list[str] = []

    def unconsciousness(self):
        self.played.append("unconsciousness")


class _Anim:
    current_action = "idle"


class _Win:
    def __init__(self):
        self.action_queue = _Queue()
        self.pet_actions = _Actions()
        self.pet_anim = _Anim()


class _Agent:
    is_llm_loading = False

    def __init__(self, win):
        self._pet_window = win


def _make_tasks():
    win = _Win()
    return ScheduledTasks(_Agent(win)), win


class TestUnconsciousness:
    def test_not_triggered_before_threshold(self):
        tasks, win = _make_tasks()
        tasks._unconsciousness()
        assert win.pet_actions.played == []

    def test_triggered_after_threshold(self):
        tasks, win = _make_tasks()
        tasks._unconsciousness()
        tasks._idle_since = time.monotonic() - config.UNCONSCIOUS_IDLE_SECONDS - 1
        tasks._unconsciousness()
        assert win.pet_actions.played == ["unconsciousness"]
        assert tasks._idle_since is None

    def test_timer_resets_on_other_action(self):
        tasks, win = _make_tasks()
        tasks._unconsciousness()
        win.pet_anim.current_action = "sleep"
        tasks._unconsciousness()
        assert tasks._idle_since is None

    def test_timer_resets_on_queued_action(self):
        tasks, win = _make_tasks()
        tasks._unconsciousness()
        win.action_queue.action = "walk"
        tasks._unconsciousness()
        assert tasks._idle_since is None

    def test_timer_resets_while_falling(self):
        tasks, win = _make_tasks()
        tasks._unconsciousness()
        win.pet_actions.gravity.falling = True
        tasks._unconsciousness()
        assert tasks._idle_since is None

    def test_timer_resets_during_llm_call(self):
        tasks, win = _make_tasks()
        tasks._unconsciousness()
        tasks._agent.is_llm_loading = True
        tasks._unconsciousness()
        assert tasks._idle_since is None

    def test_no_retrigger_while_unconscious(self):
        tasks, win = _make_tasks()
        win.pet_anim.current_action = "unconsciousness"
        tasks._idle_since = time.monotonic() - config.UNCONSCIOUS_IDLE_SECONDS - 1
        tasks._unconsciousness()
        assert win.pet_actions.played == []
