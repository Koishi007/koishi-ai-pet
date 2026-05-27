"""Pet 状态枚举 + 轻量状态机。"""

from datetime import datetime
from enum import Enum


class PetState(Enum):
    IDLE = "idle"
    SLEEPING = "sleeping"
    TALKING = "talking"
    INTERACTING = "interacting"


class StateMachine:
    """简单状态机，维护当前状态并做合法性检查。"""

    _TRANSITIONS = {
        PetState.IDLE:        [PetState.SLEEPING, PetState.TALKING, PetState.INTERACTING],
        PetState.SLEEPING:    [PetState.IDLE],
        PetState.TALKING:     [PetState.IDLE, PetState.INTERACTING],
        PetState.INTERACTING: [PetState.IDLE, PetState.TALKING],
    }

    def __init__(self, initial: PetState = PetState.IDLE):
        self._state = initial

    @property
    def state(self) -> PetState:
        return self._state

    @property
    def can_decide(self) -> bool:
        return self._state not in (PetState.SLEEPING, PetState.TALKING, PetState.INTERACTING)

    def transition(self, new_state: PetState) -> bool:
        allowed = self._TRANSITIONS.get(self._state, [])
        if new_state in allowed or new_state == self._state:
            self._state = new_state
            return True
        return False

    def force(self, new_state: PetState):
        self._state = new_state
