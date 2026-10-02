"""决策模块测试：工具轮次预算与取消、摘要流水线分支、空响应重试、流式入口接线。

这些失败模式（轮次耗尽后丢产出、空响应不重试、摘要退回拼接）不看界面也能判定，
因此单独守住，不依赖端到端跑桌宠。
"""

import logging
import threading
import time
from types import SimpleNamespace

import pytest

import pet.brain.behavior as behavior_module
from pet.brain.behavior import Behavior, retry_if_empty
from pet.brain.output import ActionStep, BehaviorOutput, CancelledError
from pet.brain.parsing import BehaviorParser
from pet.brain.summary import SummaryHooks, flush_summaries
from pet.brain.tool_loop import run_tool_loop
from pet.config import config


def _empty() -> BehaviorOutput:
    return BehaviorOutput()


def _with_speech(text: str = "你好") -> BehaviorOutput:
    return BehaviorOutput(speech=text)


class TestRetryIfEmpty:
    def test_retries_once_when_both_empty(self):
        calls = []

        def pipeline():
            calls.append(1)
            return _empty() if len(calls) == 1 else _with_speech()

        assert retry_if_empty(pipeline, "t").speech == "你好"
        assert len(calls) == 2

    def test_returns_first_result_when_speech_present(self):
        calls = []

        def pipeline():
            calls.append(1)
            return _with_speech()

        assert retry_if_empty(pipeline, "t").speech == "你好"
        assert len(calls) == 1

    def test_returns_first_result_when_actions_present(self):
        calls = []

        def pipeline():
            calls.append(1)
            return BehaviorOutput(actions=[ActionStep("sit")])

        retry_if_empty(pipeline, "t")
        assert len(calls) == 1

    def test_still_empty_after_retry_is_returned(self):
        calls = []

        def pipeline():
            calls.append(1)
            return _empty()

        assert retry_if_empty(pipeline, "t").speech is None
        assert len(calls) == 2


class _FakeResult:
    def __init__(self, name="tool", success=True, data=None, brief=""):
        self.name = name
        self.success = success
        self.data = data if data is not None else {"summary": brief}
        self.error = "" if success else "boom"
        self.context_brief = ""
        self.image_b64 = None


class _FakeExecutor:
    """记录每次执行的工具名，返回固定结果。"""

    def __init__(self):
        self.calls = []

    def execute_one(self, call):
        self.calls.append(call.name)
        return _FakeResult(name=call.name)

    def normalize(self, data):
        return str(data)


class _FakeSession:
    """最小会话：轮次策略用实现同款常量，每次 LLM 调用返回预置的 (content, tool_calls)。"""

    meta_tool_names = frozenset({"recall__search", "tool_search__search"})
    meta_tool_max_rounds = 99
    recall_tool_names = frozenset({"recall__search"})

    def __init__(self, rounds, raise_on_stream=None):
        self.rounds = list(rounds)
        self.raise_on_stream = raise_on_stream
        self.streams = 0
        self.progress = 0
        self.contexts = []
        self.executor_obj = _FakeExecutor()
        self.activated = []

    def tools_param(self, enable_tools):
        return None

    def activate_groups(self, name, result, arguments):
        self.activated.append(name)

    def note_progress(self):
        self.progress += 1

    def speak_aside(self, text):
        pass

    def end_aside(self):
        pass

    def add_context(self, role, content, is_summary=False):
        self.contexts.append((role, content))

    def executor(self):
        return self.executor_obj

    def stream(self, messages, max_tokens, tools, thinking, *, tag, on_chunk=None, on_stream_end=None):
        self.streams += 1
        if self.raise_on_stream is not None:
            raise self.raise_on_stream
        return self.rounds.pop(0)


def _tool_calls(name: str, arguments: str = "{}"):
    return {0: {"id": f"call_{name}", "name": name, "arguments": arguments}}


class TestToolLoopRounds:
    def test_stops_when_model_returns_no_tool_calls(self):
        session = _FakeSession([("Speech: 好\n", {})])
        out = run_tool_loop([], _tool_calls("timer__set"), "", session, max_rounds=3)
        assert out.speech == "好"
        assert session.executor_obj.calls == ["timer__set"]

    def test_real_round_budget_exhausted_returns_parsed_content(self):
        # 每轮都请求普通工具：预算耗尽后应拿最后一轮内容解析，而不是丢产出
        session = _FakeSession([("Speech: 第 %d 轮\n" % i, _tool_calls("timer__set")) for i in range(1, 4)])
        out = run_tool_loop([], _tool_calls("timer__set"), "Speech: 首轮\n", session, max_rounds=1)
        assert session.streams == 1
        assert out.speech == "第 1 轮"
        assert len(session.executor_obj.calls) == 1

    def test_meta_tool_rounds_do_not_consume_budget(self):
        # 元工具轮次不计数：连续 5 轮 recall 后仍能继续请求普通工具
        rounds = [("", _tool_calls("recall__search")) for _ in range(5)]
        rounds.append(("Speech: 想起来了\n", {}))
        session = _FakeSession(rounds)
        out = run_tool_loop([], _tool_calls("recall__search"), "", session, max_rounds=1)
        assert out.speech == "想起来了"
        assert session.streams == 6

    def test_meta_round_safety_cap_terminates(self):
        # 安全上限按总轮次计：命中即跳出，不再发起 LLM 调用（否则元工具会无限循环）
        session = _FakeSession([("", _tool_calls("recall__search")) for _ in range(5)])
        session.meta_tool_max_rounds = 2
        out = run_tool_loop([], _tool_calls("recall__search"), "Speech: 兜底\n", session, max_rounds=3)
        assert session.streams <= session.meta_tool_max_rounds
        assert out.actions[0].name == "sit"  # 最后一轮无内容 → 兜底动作


class TestToolLoopCancellation:
    def test_cancelled_error_propagates(self):
        session = _FakeSession([], raise_on_stream=CancelledError("被新请求取消"))
        with pytest.raises(CancelledError):
            run_tool_loop([], _tool_calls("timer__set"), "", session, max_rounds=3)


class _Recorder:
    def __init__(self, items, summary=None, raises=None):
        self.items = list(items)
        self.summary = summary
        self.raises = raises
        self.added = []
        self.summarize_calls = 0

    def drain(self):
        items, self.items = self.items, []
        return items

    def summarize_fn(self, items):
        self.summarize_calls += 1
        if self.raises:
            raise self.raises
        return self.summary

    def add_context(self, role, content, is_summary=False):
        self.added.append((role, content, is_summary))


class TestFlushSummaries:
    def _hooks(self, rec, summarize=True):
        return SummaryHooks(
            drain=rec.drain,
            summarize=rec.summarize_fn if summarize else None,
            add_context=rec.add_context,
            fallback=lambda items: "拼接摘要",
        )

    def test_empty_queue_does_nothing(self):
        rec = _Recorder([])
        flush_summaries(self._hooks(rec))
        assert rec.summarize_calls == 0 and rec.added == []

    def test_uses_llm_summary(self):
        rec = _Recorder(["[user] 甲"], summary="LLM 摘要")
        flush_summaries(self._hooks(rec))
        assert rec.added == [("system", "[历史摘要] LLM 摘要", True)]

    def test_falls_back_without_llm(self, caplog):
        rec = _Recorder(["[user] 甲"])
        with caplog.at_level(logging.WARNING):
            flush_summaries(self._hooks(rec, summarize=False))
        assert rec.added == [("system", "[历史摘要] 拼接摘要", True)]
        assert rec.summarize_calls == 0
        assert "LLM summarization failed" not in caplog.text

    def test_falls_back_on_exception(self, caplog):
        rec = _Recorder(["[user] 甲"], raises=RuntimeError("boom"))
        with caplog.at_level(logging.WARNING):
            flush_summaries(self._hooks(rec))
        assert rec.added == [("system", "[历史摘要] 拼接摘要", True)]
        assert "LLM summarization failed" in caplog.text

    def test_falls_back_on_empty_summary(self):
        rec = _Recorder(["[user] 甲"], summary=None)
        flush_summaries(self._hooks(rec))
        assert rec.added == [("system", "[历史摘要] 拼接摘要", True)]


class _Usage:
    prompt_tokens = 1
    completion_tokens = 1
    total_tokens = 2


class _StreamStub:
    """消费侧只用到 choices / delta.content / usage / finish_reason。"""

    def __init__(self, text, with_usage=True):
        delta = SimpleNamespace(content=text, tool_calls=None)
        choice = SimpleNamespace(delta=delta, finish_reason=None)
        self._chunks = [SimpleNamespace(choices=[choice], usage=None)]
        if with_usage:
            self._chunks.append(SimpleNamespace(choices=[], usage=_Usage()))

    def __iter__(self):
        return iter(self._chunks)


class _FakeStreamLlm:
    """流式建流：按调用次数返回预置文本，记录每次的 tag。"""

    def __init__(self, texts):
        self.texts = list(texts)
        self.tags = []
        self.model = "fake-model"
        self.client = None  # stream_chat 被替换，不经过真实 client

    def __bool__(self):
        return True

    def reset_effective(self):
        pass


def _stream_behavior(monkeypatch, texts, stream_factory=None):
    """装配一个只依赖假 LLM 的 Behavior：修改流式出口复用它。"""
    monkeypatch.setattr(config, "BRAIN", "api")
    monkeypatch.setattr(config, "LLM_KEY", "k")
    monkeypatch.setattr(config, "LLM_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setattr(config, "LLM_MODEL", "m")
    behavior = Behavior()
    fake = _FakeStreamLlm(texts)
    behavior._llm = fake
    behavior.gateway._llm = fake

    def fake_stream_chat(messages, max_tokens=4000, tools=None, thinking=None):
        fake.tags.append("stream")
        return (stream_factory or _StreamStub)(fake.texts.pop(0))

    behavior.gateway.stream_chat = fake_stream_chat
    return behavior, fake


class TestEntrypointRetry:
    """决策入口的空响应重试：整轮既无动作也无台词时再跑一次。"""

    def test_only_summary_response_is_retried(self, monkeypatch):
        # 只回一行 Summary：没有动作也没有台词，属于空响应
        behavior, fake = _stream_behavior(monkeypatch, ["Summary: 发呆\n", "Speech: 回来了\n"])
        out = behavior.chat_decide_stream("在吗", "")
        assert len(fake.tags) == 2
        assert out.speech == "回来了"

    def test_speech_response_is_not_retried(self, monkeypatch):
        behavior, fake = _stream_behavior(monkeypatch, ["Speech: 一次就好\n"])
        out = behavior.chat_decide_stream("在吗", "")
        assert len(fake.tags) == 1
        assert out.speech == "一次就好"

    def test_still_empty_after_retry_is_returned(self, monkeypatch):
        behavior, fake = _stream_behavior(monkeypatch, ["Summary: 一\n", "Summary: 二\n"])
        out = behavior.chat_decide_stream("在吗", "")
        assert len(fake.tags) == 2
        assert out.actions == [] and out.speech is None


class _ToolCallStream:
    """首轮流只产出一条 tool_calls，把编排推进工具轮次。"""

    def __init__(self, _text=None):
        function = SimpleNamespace(name="timer__set", arguments="{}")
        delta = SimpleNamespace(content=None,
                                tool_calls=[SimpleNamespace(index=0, id="call_1", function=function)])
        self._chunks = [SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)],
                                        usage=None)]

    def __iter__(self):
        return iter(self._chunks)


class _HangingStream:
    """close() 之前一直吊着不产数据，模拟服务端占着连接不发内容。"""

    def __init__(self):
        self.released = threading.Event()
        self.closed = False

    def __iter__(self):
        self.released.wait(30)
        return iter(())

    def close(self):
        self.closed = True
        self.released.set()


class _ProgressSink:
    """解析器只依赖进展上报。"""

    def note_progress(self):
        pass


def _iter_thread_alive() -> bool:
    return any(t.name == "stream-iter" for t in threading.enumerate())


class TestStreamCancellation:
    """协作式取消：作废的流要被关闭，线程与决策锁要能归还。"""

    def test_cancel_closes_stream_and_reclaims_iter_thread(self):
        stream = _HangingStream()

        with pytest.raises(CancelledError):
            BehaviorParser(_ProgressSink()).collect_stream(
                stream, 60.0, tag="t", cancel_check=lambda: True)

        assert stream.closed  # 关闭连接才能打断挂住的 HTTP 读
        deadline = time.monotonic() + 5
        while _iter_thread_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _iter_thread_alive()

    def test_tool_round_stream_receives_cancel_check(self, monkeypatch):
        # 工具轮次内的流也要能被抢占，取消检查必须在这一跳上传递到位
        behavior, _fake = _stream_behavior(monkeypatch, ["Summary: 占位\n"])
        seen = {}

        def fake_parse(stream, timeout, sink, tag="", cancel_check=None, on_chunk=None,
                       on_stream_end=None, action_fallback=True):
            seen["cancel_check"] = cancel_check
            return "", {}

        monkeypatch.setattr(behavior_module, "parse_stream_chunks", fake_parse)

        def cancel_check():
            return False

        behavior.tool_session(cancel_check).stream([], 100, None, None, tag="t_round_1")
        assert seen["cancel_check"] is cancel_check

    def test_cancel_inside_tool_round_releases_lock(self, monkeypatch):
        behavior, _fake = _stream_behavior(monkeypatch, [""], stream_factory=_ToolCallStream)
        session = _FakeSession([], raise_on_stream=CancelledError("被新请求取消"))
        monkeypatch.setattr(behavior, "tool_session", lambda cancel_check=None: session)

        with pytest.raises(CancelledError):
            behavior.chat_decide_stream("在吗", "")

        assert behavior._lock.acquire(timeout=0)  # 取消不能把决策锁带走
        behavior._lock.release()
