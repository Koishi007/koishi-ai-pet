"""用户中断当前调用的编排测试：只验证判定、取消与收尾顺序，不建真实脑线程。"""

import logging

from pet.agent.pet_agent import BrainWorker, PetAgent
from pet.agent.state import PetState
from pet.brain.output import CancelledError


class _FakeThread:
    def __init__(self, running: bool):
        self.running = running

    def isRunning(self) -> bool:
        return self.running


class _Signal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


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
        self._call_token = object()
        self._thread = _FakeThread(running)
        self._worker = object()
        self._retired: list = []  # 退役线程不参与中断判定
        self.request_cancel_calls = 0
        self.waiting_cancel_calls = 0
        self.retired = []
        self.loading_calls: list[bool] = []
        self.state_machine = _StateMachine()
        self.behavior = _Behavior()
        self.notify_requested = _Signal()

    def can_interrupt(self) -> bool:
        return PetAgent.can_interrupt(self)  # 走真实谓词

    def _request_cancel(self):
        self.request_cancel_calls += 1
        self._call_token = None  # 与真实实现一致：取消即作废令牌
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
    assert stub._call_token is not None


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
    assert stub._call_token is None  # 令牌失效，旧管线在下一个轮询点退出


def test_repeated_interrupt_is_noop_after_first():
    stub = _AgentStub(running=True)

    assert PetAgent.interrupt_current_call(stub) is True
    assert PetAgent.interrupt_current_call(stub) is False
    assert stub.request_cancel_calls == 1
    assert stub._call_token is None


def test_token_of_interrupted_call_stays_stale_after_restart():
    """中断后立即发起新调用：旧令牌不复活，旧管线取不到新调用的令牌。"""
    stub = _AgentStub(running=True)
    stale = stub._call_token
    assert PetAgent.interrupt_current_call(stub) is True

    fresh = object()
    stub._call_token = fresh  # 模拟 _async_brain 派发新调用

    assert PetAgent.is_current_call(stub, stale) is False
    assert PetAgent.is_current_call(stub, fresh) is True


def test_pipeline_token_is_injected_at_dispatch():
    """令牌由派发方作为首个参数注入管线，管线不在执行体内自行取号。"""
    seen = []
    worker = BrainWorker(lambda token, value: seen.append((token, value)), "T", 7)
    worker.run()
    assert seen == [("T", 7)]


class _StreamAgent:
    """承载 _interact_pipeline 所需的 agent 最小面：假行为直接回放一段流式输出。"""

    class _Behavior:
        @staticmethod
        def interact_decide_stream(hint, *, on_chunk, on_stream_end, **_kw):
            on_chunk("你好")
            on_stream_end()

    def __init__(self, token):
        self._call_token = token
        self.speak_stream_start = _Signal()
        self.speak_stream_chunk = _Signal()
        self.speak_stream_end = _Signal()
        self.behavior = self._Behavior()

    def is_current_call(self, token):
        return PetAgent.is_current_call(self, token)  # 走真实判定


def test_stream_signals_carry_call_token():
    token = object()
    stub = _StreamAgent(token)

    PetAgent._interact_pipeline(stub, token, "hint")

    assert stub.speak_stream_start.emitted == [(token,)]
    assert stub.speak_stream_chunk.emitted == [(token, "你好")]
    assert stub.speak_stream_end.emitted == [(token, 4000)]


def test_stale_stream_signals_are_not_emitted():
    stale = object()
    stub = _StreamAgent(stale)
    stub._call_token = object()  # 新调用顶替旧令牌

    PetAgent._interact_pipeline(stub, stale, "hint")

    assert stub.speak_stream_start.emitted == []
    assert stub.speak_stream_chunk.emitted == []
    assert stub.speak_stream_end.emitted == []


def test_recover_stuck_brain_resets_groups_and_notifies():
    stub = _AgentStub(running=True)

    PetAgent.recover_stuck_brain(stub)
    assert stub.waiting_cancel_calls == 1
    assert stub.state_machine.forced is PetState.IDLE
    assert stub.loading_calls == [False]
    assert stub.behavior.groups_reset == 1
    assert stub.notify_requested.emitted


class _Deletable:
    def __init__(self):
        self.deleted = False

    def deleteLater(self):
        self.deleted = True


class _CleanupStub:
    """承载 _cleanup_thread 所需的 agent 最小面。"""

    def __init__(self, thread, worker):
        self._thread = thread
        self._worker = worker
        self.sender_obj = thread

    def sender(self):
        return self.sender_obj


def test_cleanup_retires_current_thread_and_worker():
    thread, worker = _Deletable(), _Deletable()
    stub = _CleanupStub(thread, worker)

    PetAgent._cleanup_thread(stub)
    assert thread.deleted and worker.deleted
    assert stub._thread is None and stub._worker is None


def test_cleanup_keeps_current_worker_on_old_thread_signal():
    # 旧线程的 finished 后到时，当前 worker 不能被顺带销毁：它的回调还要用 sender
    old_thread, thread, worker = _Deletable(), _Deletable(), _Deletable()
    stub = _CleanupStub(thread, worker)
    stub.sender_obj = old_thread

    PetAgent._cleanup_thread(stub)
    assert not thread.deleted and not worker.deleted
    assert stub._thread is thread and stub._worker is worker


class _DispatchStub:
    """承载 _dispatch_result / _dispatch_error 所需的 agent 最小面。"""

    def __init__(self, token):
        self._call_token = token
        self.sender_obj = None

    def sender(self):
        return self.sender_obj

    def is_current_call(self, token):
        return PetAgent.is_current_call(self, token)  # 走真实判定


class _WorkerStub:
    def __init__(self, token, calls):
        self.call_token = token
        self.result_handler = calls.append
        self.error_handler = calls.append


def test_result_of_current_call_is_dispatched():
    calls, token = [], object()
    stub = _DispatchStub(token)
    stub.sender_obj = _WorkerStub(token, calls)

    PetAgent._dispatch_result(stub, "ok")
    PetAgent._dispatch_error(stub, "boom")
    assert calls == ["ok", "boom"]


def test_result_of_stale_call_is_dropped():
    calls = []
    stub = _DispatchStub(object())  # 当前调用已换令牌
    stub.sender_obj = _WorkerStub(object(), calls)

    PetAgent._dispatch_result(stub, "late")
    PetAgent._dispatch_error(stub, "late")
    assert calls == []


def test_dispatch_without_sender_is_noop():
    stub = _DispatchStub(object())
    stub.sender_obj = None

    PetAgent._dispatch_result(stub, "orphan")
    PetAgent._dispatch_error(stub, "orphan")


def _raise_cancelled():
    raise CancelledError("流式调用被新请求取消")


def _raise_boom():
    raise RuntimeError("boom")


class TestBrainWorkerCancellation:
    """取消是预期结果：不发结果也不报错，收尾由发起取消的一方负责。"""

    @staticmethod
    def _worker(fn):
        worker = BrainWorker(fn)
        results, errors = [], []
        worker.finished.connect(results.append)
        worker.error.connect(errors.append)
        return worker, results, errors

    def test_cancel_logs_info_only(self, caplog):
        worker, results, errors = self._worker(_raise_cancelled)
        with caplog.at_level(logging.INFO, logger="pet.agent.pet_agent"):
            worker.run()
        assert results == [] and errors == []
        assert any("cancelled" in r.getMessage() for r in caplog.records)
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    def test_real_failure_still_emits_error(self, caplog):
        worker, results, errors = self._worker(_raise_boom)
        with caplog.at_level(logging.ERROR, logger="pet.agent.pet_agent"):
            worker.run()
        assert errors == ["boom"]
        assert results == []

    def test_result_is_emitted(self):
        worker, results, errors = self._worker(lambda: "ok")
        worker.run()
        assert results == ["ok"] and errors == []
