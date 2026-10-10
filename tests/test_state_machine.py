"""状态机测试：流转合法性、强制转移与流转历史记录。"""

from pet.agent.state import PetState, StateMachine


class TestTransition:
    def test_legal_transition_moves_state(self):
        sm = StateMachine()
        assert sm.transition(PetState.AUTONOMOUS) is True
        assert sm.state is PetState.AUTONOMOUS

    def test_same_state_transition_returns_true(self):
        sm = StateMachine()
        assert sm.transition(PetState.IDLE) is True
        assert sm.state is PetState.IDLE

    def test_signal_emitted_only_on_actual_change(self):
        received = []
        sm = StateMachine()
        sm.state_changed.connect(received.append)
        sm.transition(PetState.AUTONOMOUS)
        sm.transition(PetState.AUTONOMOUS)  # 同状态：合法但不发射
        sm.force(PetState.IDLE)
        sm.force(PetState.IDLE)  # 同状态：不发射
        assert received == ["autonomous", "idle"]


class TestTryTransition:
    def test_busy_state_blocks_try_transition(self):
        sm = StateMachine(PetState.AUTONOMOUS)
        assert sm.try_transition(PetState.IDLE) is False
        assert sm.state is PetState.AUTONOMOUS

    def test_idle_allows_try_transition(self):
        sm = StateMachine()
        assert sm.try_transition(PetState.INTERACTING) is True
        assert sm.state is PetState.INTERACTING


class TestHistory:
    def test_initial_state_has_empty_history(self):
        assert StateMachine().history == ()

    def test_legal_transition_recorded(self):
        sm = StateMachine()
        sm.transition(PetState.AUTONOMOUS)
        (t,) = sm.history
        assert (t.from_state, t.to_state, t.forced) == ("idle", "autonomous", False)
        assert t.wall_ts > 0 and t.mono_ts > 0

    def test_same_state_transition_not_recorded(self):
        sm = StateMachine()
        sm.transition(PetState.IDLE)
        assert sm.history == ()

    def test_force_recorded_as_forced(self):
        sm = StateMachine(PetState.AUTONOMOUS)
        sm.force(PetState.IDLE)
        (t,) = sm.history
        assert (t.from_state, t.to_state, t.forced) == ("autonomous", "idle", True)

    def test_force_to_same_state_not_recorded(self):
        sm = StateMachine(PetState.IDLE)
        sm.force(PetState.IDLE)
        assert sm.history == ()

    def test_history_keeps_latest_records(self):
        sm = StateMachine()
        for _ in range(StateMachine._HISTORY_MAX + 10):
            sm.force(PetState.AUTONOMOUS if sm.state is PetState.IDLE else PetState.IDLE)
        assert len(sm.history) == StateMachine._HISTORY_MAX
        assert sm.history[-1].to_state == "idle"


class TestStateSince:
    def test_state_since_advances_on_change(self):
        sm = StateMachine()
        before = sm.state_since
        sm.transition(PetState.AUTONOMOUS)
        assert sm.state_since >= before
