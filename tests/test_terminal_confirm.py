"""确认通道：sink 未注册立即拒绝、超时、中断与队列推进。"""

import ast
import dataclasses
import threading
import time
from pathlib import Path

import pytest

from pet.agent.pet_agent import PetAgent
from pet.terminal.confirm import ConfirmRequest
from pet.tools.context import TOOL_CTX

_CONTEXT_FILE = Path(__file__).resolve().parent.parent / "pet" / "tools" / "context.py"


@pytest.fixture(autouse=True)
def _clean_sink():
    TOOL_CTX.set_confirm_sink(None)
    yield
    TOOL_CTX.set_confirm_sink(None)


def _request(timeout_s: float = 5.0) -> ConfirmRequest:
    return ConfirmRequest(command="dir", argv=["dir"], routed=["dir"], timeout_s=timeout_s)


class TestConfirm:

    def test_no_sink_denies_immediately(self):
        started = time.monotonic()
        assert TOOL_CTX.confirm(_request()) is False
        assert time.monotonic() - started < 0.05

    def test_sink_approval(self):
        def _approve(request):
            request.shown.set()
            request.result = True
            request.event.set()

        TOOL_CTX.set_confirm_sink(_approve)
        assert TOOL_CTX.confirm(_request()) is True

    def test_sink_leaves_request_unanswered(self):
        TOOL_CTX.set_confirm_sink(lambda request: request.shown.set())
        started = time.monotonic()
        assert TOOL_CTX.confirm(_request(0.2)) is False
        assert time.monotonic() - started >= 0.15

    def test_timeout_withdraws_request(self):
        """倒计时走完仍无结论：撤回请求，卡片不要停在一条已经没人等的命令上。"""
        withdrawn = []
        request = _request(0.1)
        request.withdraw = lambda: withdrawn.append(request)
        TOOL_CTX.set_confirm_sink(lambda req: req.shown.set())

        assert TOOL_CTX.confirm(request) is False
        assert withdrawn == [request]

    def test_never_shown_request_is_withdrawn(self):
        """排队一直没轮上展示：按拒绝返回，并把自己的请求撤下来。"""
        withdrawn = []
        request = _request(0.1)
        request.withdraw = lambda: withdrawn.append(request)
        TOOL_CTX.set_confirm_sink(lambda req: None)

        assert TOOL_CTX.confirm(request) is False
        assert withdrawn == [request]

    def test_queue_grace_covers_waiting_for_the_card(self):
        """排队时间超过倒计时也不算超时：拿到展示机会后再按倒计时等结果。"""
        request = _request(0.1)
        request.queue_grace_s = 5.0

        def _show_later():
            time.sleep(0.3)
            request.shown.set()
            request.result = True
            request.event.set()

        TOOL_CTX.set_confirm_sink(
            lambda req: threading.Thread(target=_show_later, daemon=True).start())

        assert TOOL_CTX.confirm(request) is True

    def test_reject_pending_confirms_unblocks_waiter(self):
        """队列只由 agent 持有：批量拒绝也只走 agent 一处。"""
        agent = _AgentStub()
        TOOL_CTX.set_confirm_sink(agent.request_confirm)
        outcome = {}
        thread = threading.Thread(
            target=lambda: outcome.setdefault("allowed", TOOL_CTX.confirm(_request(30))))
        thread.start()
        for _ in range(100):
            if agent.confirm_requested.emitted:
                break
            time.sleep(0.01)
        assert agent.confirm_requested.emitted, "确认请求没有进入队列"
        assert agent.reject_pending_confirms("interrupt") == 1
        thread.join(2)
        assert thread.is_alive() is False
        assert outcome["allowed"] is False

    def test_context_holds_no_confirm_queue(self):
        source = _CONTEXT_FILE.read_text(encoding="utf-8-sig")
        assert "_pending_confirms" not in source
        assert "_confirm_lock" not in source
        assert "reject_pending_confirms" not in source

    def test_sink_exception_is_denial(self):
        def _boom(request):
            raise RuntimeError("ui gone")

        TOOL_CTX.set_confirm_sink(_boom)
        assert TOOL_CTX.confirm(_request()) is False

    def test_request_carries_every_field(self):
        names = {field.name for field in dataclasses.fields(ConfirmRequest)}
        assert {"argv", "builtin", "routed", "command", "cwd", "shell_mode",
                "timeout_s", "queue_grace_s", "source", "matched_patterns",
                "untrusted", "sequence", "total", "event", "shown", "withdraw",
                "result"} <= names

    def test_context_has_no_agent_dependency(self):
        tree = ast.parse(_CONTEXT_FILE.read_text(encoding="utf-8-sig"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not any(name.startswith("pet.agent") for name in imported)


class _FakeSignal:

    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class _AgentStub:
    """承载确认队列所需的 agent 最小面。"""

    def __init__(self):
        self._confirm_queue = []
        self._confirm_current = None
        self._confirm_lock = threading.Lock()
        self.confirm_requested = _FakeSignal()
        self.confirm_cleared = _FakeSignal()

    def request_confirm(self, request):
        PetAgent.request_confirm(self, request)

    def next_confirm(self, request=None):
        PetAgent.next_confirm(self, request)

    def withdraw_confirm(self, request):
        return PetAgent.withdraw_confirm(self, request)

    def reject_pending_confirms(self, reason=""):
        return PetAgent.reject_pending_confirms(self, reason)

    def _queue_index(self, request):
        return PetAgent._queue_index(self, request)

    def _renumber_confirms(self):
        PetAgent._renumber_confirms(self)

    def _emit_next_confirm(self):
        PetAgent._emit_next_confirm(self)


class TestAgentQueue:

    def test_single_card_and_sequence(self):
        agent = _AgentStub()
        first, second = _request(), _request()
        agent.request_confirm(first)
        agent.request_confirm(second)
        assert len(agent.confirm_requested.emitted) == 1
        assert (first.sequence, first.total) == (1, 2)
        assert (second.sequence, second.total) == (2, 2)

        agent.next_confirm()
        assert len(agent.confirm_requested.emitted) == 2
        assert agent.confirm_requested.emitted[1][0] is second

    def test_queue_drains_to_idle(self):
        agent = _AgentStub()
        agent.request_confirm(_request())
        agent.next_confirm()
        assert agent._confirm_queue == []
        assert agent._confirm_current is None

    def test_next_confirm_removes_named_request(self):
        """按对象身份移除：旧卡片的迟到回调不会把新请求弹掉。"""
        agent = _AgentStub()
        first, second = _request(), _request()
        agent.request_confirm(first)
        agent.request_confirm(second)

        agent.next_confirm(first)
        agent.next_confirm(first)

        assert agent._confirm_queue == [second]
        assert agent._confirm_current is second
        assert len(agent.confirm_requested.emitted) == 2

    def test_withdraw_promotes_next(self):
        agent = _AgentStub()
        first, second = _request(), _request()
        agent.request_confirm(first)
        agent.request_confirm(second)

        assert agent.withdraw_confirm(first) is True
        assert agent._confirm_queue == [second]
        assert agent.confirm_requested.emitted[-1][0] is second

    def test_withdraw_unknown_request_is_noop(self):
        agent = _AgentStub()
        only = _request()
        agent.request_confirm(only)
        agent.next_confirm(only)

        assert agent.withdraw_confirm(only) is False
        assert len(agent.confirm_cleared.emitted) == 1

    def test_withdraw_last_request_hides_card(self):
        agent = _AgentStub()
        only = _request()
        agent.request_confirm(only)

        agent.withdraw_confirm(only)

        assert agent._confirm_queue == []
        assert agent._confirm_current is None
        assert len(agent.confirm_cleared.emitted) == 1

    def test_no_cleared_while_queue_has_requests(self):
        """队列非空时不发清空信号，否则刚弹出的卡片会被收掉。"""
        agent = _AgentStub()
        agent.request_confirm(_request())
        agent.request_confirm(_request())
        assert agent.confirm_cleared.emitted == []

    def test_request_confirm_wires_withdraw(self):
        """等待方靠 request.withdraw 撤回请求，这条线必须由入队时挂上。"""
        agent = _AgentStub()
        request = _request()
        agent.request_confirm(request)
        assert callable(request.withdraw)
        assert request.withdraw() is True
        assert agent._confirm_queue == []

    def test_reject_all_clears_queue(self):
        agent = _AgentStub()
        first, second = _request(), _request()
        agent.request_confirm(first)
        agent.request_confirm(second)
        agent.reject_pending_confirms("exit")
        assert first.result is False and second.result is False
        assert first.event.is_set() and second.event.is_set()
        assert first.shown.is_set() and second.shown.is_set()
        assert agent._confirm_queue == []
        assert len(agent.confirm_cleared.emitted) == 1

    def test_reject_releases_queued_request(self):
        """没轮到展示就被拒绝：shown 也要置位，等待方不必干等排队宽限。"""
        agent = _AgentStub()
        agent.request_confirm(_request())
        queued = _request()
        agent.request_confirm(queued)
        assert queued.shown.is_set() is False

        agent.reject_pending_confirms("interrupt")

        assert queued.shown.is_set() is True

    def test_reject_all_with_empty_queue_keeps_card_hidden(self):
        agent = _AgentStub()
        assert agent.reject_pending_confirms("exit") == 0
        assert agent.confirm_cleared.emitted == []

    def test_end_to_end_approval(self):
        agent = _AgentStub()
        TOOL_CTX.set_confirm_sink(agent.request_confirm)
        outcome = {}
        request = _request(30)
        thread = threading.Thread(
            target=lambda: outcome.setdefault("allowed", TOOL_CTX.confirm(request)))
        thread.start()
        for _ in range(100):
            if agent.confirm_requested.emitted:
                break
            time.sleep(0.01)
        assert agent.confirm_requested.emitted, "确认请求没有进入队列"
        shown = agent.confirm_requested.emitted[0][0]
        shown.result = True
        shown.event.set()
        thread.join(2)
        assert outcome["allowed"] is True
