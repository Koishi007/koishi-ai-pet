"""轻量状态机"""

from collections import deque
from dataclasses import dataclass
from enum import Enum
from time import monotonic, time

from PySide6.QtCore import QObject, Signal


class PetState(Enum):
    IDLE = "idle"
    AUTONOMOUS = "autonomous"
    INTERACTING = "interacting"


@dataclass(frozen=True)
class StateTransition:
    """一次状态流转的记录：墙钟供展示，单调钟算停留时长。"""

    wall_ts: float
    mono_ts: float
    from_state: str
    to_state: str
    forced: bool = False


class StateMachine(QObject):
    """简单状态机，维护当前状态并做合法性检查，记录流转历史供调试观测"""

    state_changed = Signal(str)

    _TRANSITIONS = {
        PetState.IDLE:        [PetState.AUTONOMOUS, PetState.INTERACTING],
        PetState.AUTONOMOUS:  [PetState.IDLE, PetState.INTERACTING],
        PetState.INTERACTING: [PetState.IDLE, PetState.AUTONOMOUS],
    }

    _HISTORY_MAX = 50  # 流转历史上限，防止随运行时长无限增长

    def __init__(self, initial: PetState = PetState.IDLE, parent=None):
        super().__init__(parent)
        self._state = initial
        self._entered_at = monotonic()
        self._history: deque[StateTransition] = deque(maxlen=self._HISTORY_MAX)

    @property
    def state(self) -> PetState:
        return self._state

    @property
    def state_since(self) -> float:
        """进入当前状态的单调时刻，供停留时长展示。"""
        return self._entered_at

    @property
    def history(self) -> tuple[StateTransition, ...]:
        """流转历史快照，按发生顺序排列。"""
        return tuple(self._history)

    @property
    def can_decide(self) -> bool:
        return self._state not in (PetState.AUTONOMOUS, PetState.INTERACTING)

    def transition(self, new_state: PetState) -> bool:
        allowed = self._TRANSITIONS.get(self._state, [])
        if new_state in allowed or new_state == self._state:
            if new_state != self._state:
                self._record(new_state, forced=False)
                self.state_changed.emit(new_state.value)
            return True
        return False

    def try_transition(self, new_state: PetState) -> bool:
        """原子化的 can_decide 检查 + 状态转移"""
        if not self.can_decide:
            return False
        return self.transition(new_state)

    def force(self, new_state: PetState):
        if new_state != self._state:
            self._record(new_state, forced=True)
            self.state_changed.emit(new_state.value)

    def _record(self, new_state: PetState, forced: bool):
        old_state = self._state
        self._state = new_state
        self._entered_at = monotonic()
        self._history.append(StateTransition(
            wall_ts=time(), mono_ts=self._entered_at,
            from_state=old_state.value, to_state=new_state.value, forced=forced))
