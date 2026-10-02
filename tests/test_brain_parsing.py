"""LLM 输出解析测试：文本行 → 行为结构。

解析器是「模型意图 → 实际行为」的唯一通道，回归会导致桌宠发呆或做错事，
因此覆盖各字段的解析分支、容错与默认回落；流式与非流式两条路径共用
`pet/brain/parsing.py` 的同一套规则，末尾用对照用例守住它们不漂移。
"""

from dataclasses import dataclass
from typing import Optional

import pytest

from pet.brain.parsing import (
    BehaviorParser,
    LineTagger,
    parse_action_line,
    parse_behavior,
    parse_mood_line,
    parse_stream_chunks,
    parse_vitals_line,
)


class _Sink:
    """只记录进展上报次数的最小 sink。"""

    def __init__(self):
        self.progress = 0

    def note_progress(self):
        self.progress += 1


@dataclass
class _ToolFunction:
    name: Optional[str] = None
    arguments: Optional[str] = None


@dataclass
class _ToolCallDelta:
    index: int
    id: Optional[str] = None
    name: Optional[str] = None
    arguments: Optional[str] = None

    @property
    def function(self):
        return _ToolFunction(name=self.name, arguments=self.arguments)


@dataclass
class _Delta:
    content: Optional[str] = None
    tool_calls: Optional[list] = None


@dataclass
class _Choice:
    delta: _Delta
    finish_reason: Optional[str] = None


@dataclass
class _Chunk:
    choices: list
    usage: object = None


def _chunks(*pieces: str) -> list:
    """把文本切片喂成 content chunk，模拟流式返回。"""
    return [_Chunk([_Choice(_Delta(content=p))]) for p in pieces if p]


def _stream_text(text: str, size: int = 1) -> list:
    return _chunks(*[text[i:i + size] for i in range(0, len(text), size)])


def _collect(text: str, size: int = 1, sink=None, stream=None):
    """跑一遍流式解析，返回 (原始文本, tool_calls, 收到的话音, 分行次数)。"""
    chunks, ends = [], []
    raw, tool_calls = parse_stream_chunks(
        stream if stream is not None else _stream_text(text, size),
        total_timeout=5.0, sink=sink or _Sink(), tag="test",
        on_chunk=chunks.append, on_stream_end=lambda: ends.append(True),
    )
    return raw, tool_calls, chunks, ends


class TestParseBehavior:
    def test_full_output_fields(self):
        out = parse_behavior(
            "Summary: 用户在写代码\n"
            "Emotion: happy\n"
            "Speech: 又在写代码呀...\n"
            "Action: walk right 300\n"
            "Memory: [偏好] 用户喜欢深夜写码\n"
            "Mood: affection+5 joy+3 sanity-2\n"
            "Vitals: satiety-2 energy-1\n"
        )
        assert out.summary == "用户在写代码"
        assert out.emotion == "happy"
        assert out.speech == "又在写代码呀..."
        assert out.actions[0].name == "walk"
        assert out.memory_line == "[偏好] 用户喜欢深夜写码"
        assert out.mood_deltas == {"affection": 5.0, "joy": 3.0, "sanity": -2.0}
        assert out.vitals_deltas == {"satiety": -2.0, "energy": -1.0}

    def test_multiple_speech_lines_joined_in_order(self):
        out = parse_behavior("Speech: 第一句\nSpeech: 第二句\n")
        assert out.speech_parts == ["第一句", "第二句"]
        assert out.speech == "第一句 第二句"

    @pytest.mark.parametrize("raw", ["none", "None", "null", "", "无"])
    def test_silence_markers_produce_no_speech(self, raw):
        out = parse_behavior(f"Speech: {raw}\nAction: sit 5\n")
        assert out.speech_parts == []
        assert out.speech is None

    def test_missing_action_falls_back_to_sit(self):
        out = parse_behavior("Summary: 发呆\n")
        assert len(out.actions) == 1
        assert out.actions[0].name == "sit"
        assert out.actions[0].kwargs == {"duration": 5}

    def test_all_actions_unknown_falls_back_to_sit(self):
        out = parse_behavior("Action: fly_to_moon 3\n")
        assert [a.name for a in out.actions] == ["sit"]

    def test_field_names_are_case_insensitive(self):
        out = parse_behavior("SUMMARY: 大写字段\nACTION: sit 5\n")
        assert out.summary == "大写字段"
        assert out.actions[0].name == "sit"

    def test_colon_in_value_preserved(self):
        out = parse_behavior("Speech: 现在是 10:30\n")
        assert out.speech == "现在是 10:30"

    def test_only_first_memory_mood_vitals_kept(self):
        out = parse_behavior(
            "Memory: 第一条\nMemory: 第二条\n"
            "Mood: joy+1\nMood: joy+9\n"
        )
        assert out.memory_line == "第一条"
        assert out.mood_deltas == {"joy": 1.0}

    def test_empty_content_returns_default_sit(self):
        out = parse_behavior("")
        assert [a.name for a in out.actions] == ["sit"]

    def test_indented_lines_are_recognized(self):
        out = parse_behavior("  Speech: 缩进行\n  Action: sit 3\n")
        assert out.speech == "缩进行"
        assert out.actions[0].name == "sit"

    def test_compact_tags_without_space_are_recognized(self):
        out = parse_behavior(
            "Speech:你好\nAction:sit 3\nEmotion:happy\nMood:joy+3\nVitals:satiety-2\n"
        )
        assert out.speech == "你好"
        assert out.actions[0].name == "sit"
        assert out.emotion == "happy"
        assert out.mood_deltas == {"joy": 3.0}
        assert out.vitals_deltas == {"satiety": -2.0}

    def test_extra_spaces_after_tag_survive(self):
        out = parse_behavior("Speech:  两个空格\n")
        assert out.speech == " 两个空格"

    def test_action_fallback_can_be_disabled(self):
        out = parse_behavior("Summary: 只有摘要\n", action_fallback=False)
        assert out.actions == []
        assert out.summary == "只有摘要"


class TestParseActionLine:
    def test_positional_and_keyword_args(self):
        step = parse_action_line("walk right 300 speed=2")
        assert step.name == "walk"
        assert step.args == ("right", 300)
        assert step.kwargs == {"speed": 2}

    def test_unknown_action_returns_none(self):
        assert parse_action_line("teleport") is None

    def test_empty_returns_none(self):
        assert parse_action_line("   ") is None

    def test_name_is_lowercased(self):
        step = parse_action_line("SIT 3")
        assert step.name == "sit"

    def test_custom_whitelist_decides(self):
        assert parse_action_line("teleport", resolve=lambda _: True).name == "teleport"


class TestParseMoodVitals:
    def test_mood_handles_spaces_and_case(self):
        assert parse_mood_line("AFFECTION + 5, joy -2") == {
            "affection": 5.0, "joy": -2.0
        }

    def test_mood_unknown_key_ignored(self):
        assert parse_mood_line("hunger+5") is None

    def test_mood_without_delta_returns_none(self):
        assert parse_mood_line("affection") is None

    def test_vitals_supported_keys_only(self):
        assert parse_vitals_line("satiety+15 energy-3") == {
            "satiety": 15.0, "energy": -3.0
        }
        assert parse_vitals_line("affection+5") is None

    def test_mood_line_ignores_vitals_keys(self):
        assert parse_mood_line("satiety+5") is None


class TestLineTagger:
    def test_unknown_line_marked_other(self):
        tagger = LineTagger()
        for char in "这是一句普通正文，没有标签":
            tagger.feed(char)
        assert tagger.tag == "other"

    def test_short_prefix_not_marked_other(self):
        tagger = LineTagger()
        for char in "Speech:":
            tagger.feed(char)
        assert tagger.tag == "speech"

    def test_finish_clears_previous_tag(self):
        tagger = LineTagger()
        tagger.finish("Speech: 你好")
        assert tagger.finish("没有标签的一行") is None
        assert tagger.raw_value("没有标签的一行") == ""

    def test_feed_returns_text_only_for_speech(self):
        tagger = LineTagger()
        pieces = [tagger.feed(ch) for ch in "Summary: 摘要\n"]
        assert "".join(pieces) == ""

    def test_feed_splits_separator_space(self):
        tagger = LineTagger()
        pieces = [tagger.feed(ch) for ch in "Speech: 你好"]
        assert "".join(pieces) == "你好"


class TestStreamPath:
    CONTENT = (
        "Summary: 观察\n"
        "Emotion: happy\n"
        "Speech: 你好\n"
        "Action: sit 5\n"
        "Memory: [偏好] 咖啡\n"
        "Mood: joy+2\n"
    )

    def test_progress_reported_per_chunk(self):
        sink = _Sink()
        _collect("Speech: 你好\n", sink=sink)
        assert sink.progress > 0

    def test_speech_streamed_to_on_chunk(self):
        _, _, chunks, _ = _collect("Speech: 你好呀\n")
        assert "".join(chunks) == "你好呀"

    def test_chunks_are_emitted_at_chunk_boundaries(self):
        # 整行在一个 chunk 内到达时只回调一次，不逐字符放大
        _, _, chunks, _ = _collect("Speech: 你好呀\n", stream=_chunks("Speech: 你好呀\n"))
        assert chunks == ["你好呀"]

    def test_multiline_speech_keeps_order_across_chunks(self):
        stream = _chunks("Speech: 第一句\n", "Speech: 第二句\n")
        _, _, chunks, ends = _collect("", stream=stream)
        assert chunks == ["第一句", "第二句"]
        assert len(ends) == 1

    def test_compact_tags_are_recognized_char_by_char(self):
        parser = BehaviorParser(_Sink())
        raw, _ = parser.collect_stream(_stream_text("Speech:你好\nAction:sit 3\n"), 5.0, tag="test")
        out = parser.parse_behavior(raw)
        assert out.speech == "你好"
        assert out.actions[0].name == "sit"

    def test_action_fallback_disabled_leaves_actions_empty(self):
        parser = BehaviorParser(_Sink())
        raw, _ = parser.collect_stream(_stream_text("Summary: 只有摘要\n"), 5.0, tag="test",
                                       action_fallback=False)
        out = parser.parse_behavior(raw, action_fallback=False)
        assert out.actions == []
        assert out.summary == "只有摘要"

    def test_second_speech_line_ends_stream(self):
        _, _, chunks, ends = _collect("Speech: 第一句\nSpeech: 第二句\n")
        assert "".join(chunks) == "第一句第二句"
        assert len(ends) == 1

    def test_non_speech_lines_produce_no_chunks(self):
        _, _, chunks, _ = _collect("Summary: 只有摘要\nAction: sit 5\n")
        assert chunks == []

    def test_tool_calls_accumulated_across_chunks(self):
        stream = [
            _Chunk([_Choice(_Delta(tool_calls=[
                _ToolCallDelta(0, id="call_1", name="tool_search__search", arguments='{"key')]))]),
            _Chunk([_Choice(_Delta(tool_calls=[
                _ToolCallDelta(0, arguments='word": "todo"}')]))]),
        ]
        raw, tool_calls, _, _ = _collect("", stream=stream)
        assert raw == ""
        assert tool_calls[0] == {
            "id": "call_1", "name": "tool_search__search", "arguments": '{"keyword": "todo"}',
        }

    def test_tool_call_without_function_name_is_dropped(self):
        # 分片的 delta 里可能始终没有 name：这类调用执行不了，
        # 拼进 assistant.tool_calls 会被服务商以「missing a function name」拒绝
        stream = [
            _Chunk([_Choice(_Delta(tool_calls=[
                _ToolCallDelta(0, id="call_1", arguments='{"path": "~/Desktop"}')]))]),
        ]
        _, tool_calls, _, _ = _collect("", stream=stream)
        assert tool_calls == {}

    def test_named_call_survives_alongside_unnamed(self):
        stream = [
            _Chunk([_Choice(_Delta(tool_calls=[
                _ToolCallDelta(0, id="call_1", arguments="{}"),
                _ToolCallDelta(1, id="call_2", name="timer__set", arguments="{}")]))]),
        ]
        _, tool_calls, _, _ = _collect("", stream=stream)
        assert list(tool_calls) == [1]
        assert tool_calls[1]["name"] == "timer__set"


class TestStreamMatchesNonStreamPath:
    CONTENT = TestStreamPath.CONTENT

    def test_stream_and_non_stream_agree(self):
        parser = BehaviorParser(_Sink())
        raw, _ = parser.collect_stream(_stream_text(self.CONTENT), 5.0, tag="test")
        streamed = parser.parse_behavior(raw)
        parsed = parse_behavior(self.CONTENT)
        assert streamed.summary == parsed.summary
        assert streamed.emotion == parsed.emotion
        assert streamed.memory_line == parsed.memory_line
        assert streamed.mood_deltas == parsed.mood_deltas
        assert streamed.speech_parts == parsed.speech_parts
        assert [a.name for a in streamed.actions] == [a.name for a in parsed.actions]

    @pytest.mark.parametrize("size", [1, 3, 7, 64])
    def test_stream_agrees_for_every_split(self, size):
        parser = BehaviorParser(_Sink())
        raw, _ = parser.collect_stream(_stream_text(self.CONTENT, size=size), 5.0, tag="test")
        streamed = parser.parse_behavior(raw)
        parsed = parse_behavior(self.CONTENT)
        assert streamed.speech_parts == parsed.speech_parts
        assert [a.name for a in streamed.actions] == [a.name for a in parsed.actions]
        assert streamed.mood_deltas == parsed.mood_deltas
        assert streamed.vitals_deltas == parsed.vitals_deltas
