"""用户中断当前调用的编排测试：只验证判定、取消与收尾顺序，不建真实脑线程。"""

from pet.agent.pet_agent import PetAgent
from pet.agent.state import PetState


class _FakeThread:
    def __init__(self, running: bool):
        self.running = running

    def isRunning(self) -> bool:
        return self.running


class _Behavior:
    def __init__(self):
        self.groups_reset = 0

    def reset_active_tool_groups(self):
        self.groups_reset += 1


class _StateMachine:
    def __init__(self):
        self.forced = None

    def force(self, state):
        self.forced = state


class _AgentStub:
    """承载 can_interrupt / interrupt_current_call 所需的 agent 最小面。"""

    def __init__(self, running: bool):
        self._active_stream_id = 3
        self._thread = _FakeThread(running)
        self._worker = object()
        self._retired: list = []  # 退役线程不参与中断判定
        self.request_cancel_calls = 0
        self.waiting_cancel_calls = 0
        self.retired = []
        self.loading_calls: list[bool] = []
        self.state_machine = _StateMachine()
        self.behavior = _Behavior()

    def can_interrupt(self) -> bool:
        return PetAgent.can_interrupt(self)  # 走真实谓词

    def _request_cancel(self):
        self.request_cancel_calls += 1
        return self._thread

    def _cancel_running_thread(self, ts: str = ""):
        self.waiting_cancel_calls += 1

    def _retire(self, thread, worker):
        self.retired.append((thread, worker))

    def _set_llm_loading(self, loading: bool):
        self.loading_calls.append(loading)


def test_can_interrupt_only_looks_at_current_thread():
    assert PetAgent.can_interrupt(_AgentStub(running=True)) is True
    assert PetAgent.can_interrupt(_AgentStub(running=False)) is False

    retired_only = _AgentStub(running=False)
    retired_only._retired = [(object(), object())]
    assert PetAgent.can_interrupt(retired_only) is False


def test_interrupt_without_current_thread_is_noop():
    stub = _AgentStub(running=False)
    stub._retired = [(object(), object())]  # 只剩退役线程：无可中断对象

    assert PetAgent.interrupt_current_call(stub) is False
    assert stub.request_cancel_calls == 0
    assert stub.state_machine.forced is None
    assert stub._active_stream_id == 3


def test_interrupt_cancels_retires_and_returns_to_idle():
    stub = _AgentStub(running=True)
    thread, worker = stub._thread, stub._worker

    assert PetAgent.interrupt_current_call(stub) is True
    assert stub.request_cancel_calls == 1
    assert stub.waiting_cancel_calls == 0  # 中断不等线程退出
    assert stub.retired == [(thread, worker)]
    assert stub._thread is None and stub._worker is None
    assert stub.state_machine.forced is PetState.IDLE
    assert stub.loading_calls == [False]
    assert stub.behavior.groups_reset == 1
    assert stub._active_stream_id == 4  # 世代失效，旧管线在下一个轮询点退出


def test_repeated_interrupt_is_noop_after_first():
    stub = _AgentStub(running=True)

    assert PetAgent.interrupt_current_call(stub) is True
    assert PetAgent.interrupt_current_call(stub) is False
    assert stub.request_cancel_calls == 1
    assert stub._active_stream_id == 4
