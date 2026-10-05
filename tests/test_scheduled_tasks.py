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


class _PositionedWin:
    """带坐标的桌宠窗口。"""

    def __init__(self, x, y):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y


class _StateMachine:
    def try_transition(self, state):
        return True


class _MidAgent:
    """mid 档回调所需的 agent 最小面。"""

    SNAPSHOT = (0x1234, 1.5, 900)

    def __init__(self, win):
        self._pet_window = win
        self._thread = None
        self.state_machine = _StateMachine()
        self.calls: list[tuple] = []

    def window_snapshot(self):
        return self.SNAPSHOT

    def _autonomous_pipeline(self, pet_x, pet_y, snap):
        return None

    def _async_brain(self, fn, *args):
        self.calls.append((fn, args))


class TestAutonomousTick:
    def test_passes_coords_and_window_snapshot(self):
        agent = _MidAgent(_PositionedWin(320, 480))
        ScheduledTasks(agent)._autonomous()
        assert len(agent.calls) == 1
        fn, args = agent.calls[0]
        assert fn == agent._autonomous_pipeline
        assert args == (320, 480, _MidAgent.SNAPSHOT)

    def test_passes_snapshot_without_window(self):
        agent = _MidAgent(None)
        ScheduledTasks(agent)._autonomous()
        _fn, args = agent.calls[0]
        assert args == (0, 0, _MidAgent.SNAPSHOT)


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
