"""定时任务：无意识化的触发与复位、整点记忆汇总的调度。"""

import time
from datetime import datetime, timedelta
from types import SimpleNamespace

from pet.agent import scheduled_tasks
from pet.agent.scheduled_tasks import ScheduledTasks
from pet.config import config


class _Queue:
    action: str | None = None

    def current_action_name(self):
        return self.action


class _Gravity:
    falling = False


class _Actions:
    idle_animation = "idle"
    grim_animation = "grim"

    def __init__(self):
        self.gravity = _Gravity()
        self.played: list[str] = []

    def unconsciousness(self):
        self.played.append("unconsciousness")


class _Anim:
    current_action = "idle"


class _Win:
    is_docked = False

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


class _FakeStore:
    def __init__(self, marker: str | None, pending: int = 0):
        self._marker = marker
        self._pending = pending

    def latest_event_marker(self):
        return self._marker

    def pending_events(self, marker):
        return self._pending, self._marker


class _FakeScheduler:
    def __init__(self):
        self.calls: list[tuple] = []
        self.registered: list[str] = []

    def register(self, name, callback):
        self.registered.append(name)

    def schedule_at(self, timestamp_ms, callback, key=None):
        self.calls.append((timestamp_ms, callback, key))


class TestMemoryMergeTick:
    @staticmethod
    def _tasks(marker, pending: int = 0):
        tasks = ScheduledTasks(SimpleNamespace(memory_store=_FakeStore(marker, pending)))
        tasks._scheduler = _FakeScheduler()
        return tasks

    def test_schedules_next_hour(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_EVENT_MERGE_ENABLED", True)
        tasks = self._tasks(None)
        tasks._memory_event_merge()
        assert len(tasks._scheduler.calls) == 1
        timestamp_ms, callback, key = tasks._scheduler.calls[0]
        assert key == "memory_event_merge"
        assert callback == tasks._memory_event_merge
        at = datetime.fromtimestamp(timestamp_ms / 1000)
        assert (at.minute, at.second, at.microsecond) == (0, 0, 0)
        # 下界留 1 秒：注册恰好跨整点时，断言时刻可能已越过目标整点
        assert datetime.now() - timedelta(seconds=1) < at <= datetime.now() + timedelta(hours=1)

    def test_skips_below_threshold(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_EVENT_MERGE_ENABLED", True)
        tasks = self._tasks("m2", pending=5)
        tasks._last_merge_at = "m1"
        started = []
        monkeypatch.setattr(
            scheduled_tasks.threading, "Thread",
            lambda target, daemon: SimpleNamespace(start=lambda: started.append(target)))
        tasks._memory_event_merge()
        assert started == []
        assert tasks._last_merge_at == "m1"

    def test_starts_worker_on_new_event(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_EVENT_MERGE_ENABLED", True)
        tasks = self._tasks("m2", pending=6)
        tasks._last_merge_at = "m1"
        started = []
        monkeypatch.setattr(
            scheduled_tasks.threading, "Thread",
            lambda target, daemon, args=():
                SimpleNamespace(start=lambda: started.append((target, args))))
        tasks._memory_event_merge()
        assert started == [(tasks._run_merge, ("m2",))]
        # 游标等后台汇总成功后再推进
        assert tasks._last_merge_at == "m1"

    def test_worker_advances_cursor_on_success(self, monkeypatch):
        monkeypatch.setattr(scheduled_tasks, "run_merge", lambda store: 1)
        tasks = self._tasks("m2")
        tasks._last_merge_at = "m1"
        tasks._run_merge("m2")
        assert tasks._last_merge_at == "m2"

    def test_worker_keeps_cursor_on_failure(self, monkeypatch):
        def _boom(store):
            raise RuntimeError("boom")

        monkeypatch.setattr(scheduled_tasks, "run_merge", _boom)
        tasks = self._tasks("m2")
        tasks._last_merge_at = "m1"
        tasks._run_merge("m2")
        assert tasks._last_merge_at == "m1"

    def test_disabled_skips(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_EVENT_MERGE_ENABLED", False)
        tasks = self._tasks("m2")
        tasks._last_merge_at = "m1"
        started = []
        monkeypatch.setattr(
            scheduled_tasks.threading, "Thread",
            lambda target, daemon, args=():
                SimpleNamespace(start=lambda: started.append((target, args))))
        tasks._memory_event_merge()
        assert started == []
        assert tasks._last_merge_at == "m1"

    def test_register_all_seeds_cursor_from_store(self, monkeypatch):
        tasks = self._tasks("2026-10-20T10:00:00#00000009")
        tasks.register_all(_FakeScheduler())
        assert tasks._last_merge_at == "2026-10-20T10:00:00#00000009"

    def test_register_all_keeps_empty_cursor_without_events(self):
        tasks = self._tasks(None)
        tasks.register_all(_FakeScheduler())
        assert tasks._last_merge_at == ""


class TestMergeMarkerRefresh:
    @staticmethod
    def _tasks(marker):
        tasks = ScheduledTasks(SimpleNamespace(memory_store=_FakeStore(marker)))
        tasks._scheduler = _FakeScheduler()
        return tasks

    def test_refreshes_when_created_at_unchanged(self, monkeypatch):
        monkeypatch.setattr(scheduled_tasks, "run_merge", lambda store: 1)
        tasks = self._tasks("2026-10-20T10:00:00#00000009")
        tasks._last_merge_at = "2026-10-20T10:00:00#00000003"
        tasks._run_merge()
        assert tasks._last_merge_at == "2026-10-20T10:00:00#00000009"

    def test_keeps_marker_when_new_event_appeared(self, monkeypatch):
        monkeypatch.setattr(scheduled_tasks, "run_merge", lambda store: 1)
        tasks = self._tasks("2026-10-20T11:00:00#00000009")
        tasks._last_merge_at = "2026-10-20T10:00:00#00000003"
        tasks._run_merge()
        assert tasks._last_merge_at == "2026-10-20T10:00:00#00000003"

    def test_keeps_marker_when_store_empty(self, monkeypatch):
        monkeypatch.setattr(scheduled_tasks, "run_merge", lambda store: 0)
        tasks = self._tasks(None)
        tasks._last_merge_at = "2026-10-20T10:00:00#00000003"
        tasks._run_merge()
        assert tasks._last_merge_at == "2026-10-20T10:00:00#00000003"
