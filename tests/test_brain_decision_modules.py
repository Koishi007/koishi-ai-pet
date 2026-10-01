"""抽出的决策模块测试：工具轮次预算与取消、摘要流水线分支、空响应重试。

这三处原先内嵌在 `Behavior` 里，靠端到端跑桌宠间接覆盖；拆出来之后各自的
失败模式（轮次耗尽后丢产出、空响应不重试、摘要退回拼接）需要单独守住。
"""

import io
import logging

import pytest

from pet.brain.behavior import retry_if_empty
from pet.brain.output import ActionStep, BehaviorOutput, CancelledError
from pet.brain.summary import SummaryHooks, flush_summaries
from pet.brain.tool_loop import run_tool_loop
from pet.tools.executor import ToolResult


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
