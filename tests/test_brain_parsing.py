"""LLM 输出解析测试：文本行 → 行为结构。

解析器是「模型意图 → 实际行为」的唯一通道，回归会导致桌宠发呆或做错事，
因此覆盖各字段的解析分支、容错与默认回落；流式与非流式两条路径共用
`pet/brain/parsing.py` 的同一套规则，末尾用对照用例守住它们不漂移。
"""

from dataclasses import dataclass
from typing import Optional

import logging

import pytest

from pet.brain.parsing import (
    BehaviorParser,
    InlineTagNormalizer,
    LineTagger,
    parse_action_line,
    parse_behavior,
    parse_mood_line,
    parse_stream_chunks,
    parse_vitals_line,
)


class _Sink:
    """只记录进展上报、未解析正文与结束原因的最小 sink。"""

    def __init__(self):
        self.progress = 0
        self.unparsed = []
        self.finish_reasons = []

    def note_progress(self):
        self.progress += 1

    def note_unparsed(self, text):
        self.unparsed.append(text)

    def note_finish_reason(self, reason):
        self.finish_reasons.append(reason)


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
    reasoning_content: Optional[str] = None


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

    def test_multiple_speech_lines_keep_segments(self):
        out = parse_behavior("Speech: 第一句\nSpeech: 第二句\n")
        assert out.speech_parts == ["第一句", "第二句"]
        assert out.speech == "第一句\n第二句"

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

    def test_summary_time_label_is_stripped(self):
        out = parse_behavior("Summary: 〔3 分钟前〕连输三局\nSpeech: 唔...\n")
        assert out.summary == "连输三局"

    def test_summary_clock_label_is_stripped(self):
        out = parse_behavior("Summary: 〔11:18〕看清单\n")
        assert out.summary == "看清单"

    def test_summary_double_label_is_stripped(self):
        out = parse_behavior("Summary: 〔3 分钟前〕〔11:18〕看清单\n")
        assert out.summary == "看清单"

    def test_non_time_bracket_prefix_survives(self):
        out = parse_behavior("Summary: 〔备注〕连输三局\n")
        assert out.summary == "〔备注〕连输三局"


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

    def test_punctuation_token_is_dropped(self):
        # 模型常把表格竖线一起吐出来，纯标点不能当作位置参数
        step = parse_action_line("walk left 100 |")
        assert step.args == ("left", 100)

    def test_junk_keyword_value_is_dropped(self):
        step = parse_action_line("sit duration=|")
        assert step.kwargs == {}

    def test_underscore_token_is_dropped(self):
        # 下划线不是字母数字，单独成串时不能当作位置参数
        step = parse_action_line("walk left __ _")
        assert step.args == ("left",)

    def test_underscore_keyword_value_is_dropped(self):
        step = parse_action_line("sit duration=__")
        assert step.kwargs == {}

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

    def test_finish_reason_reported_to_sink(self):
        """结束原因回传给编排方：length 是判断预算耗尽的唯一依据。"""
        sink = _Sink()
        stream = _chunks("Speech: 你好\n")
        stream.append(_Chunk([_Choice(_Delta(), finish_reason="length")]))
        _collect("", sink=sink, stream=stream)
        assert sink.finish_reasons == ["length"]

    def test_missing_finish_reason_stays_unreported(self):
        sink = _Sink()
        _collect("Speech: 你好\n", sink=sink)
        assert sink.finish_reasons == []

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


class TestFullWidthColon:
    """全角冒号的标签行与半角同义：中文上下文里模型偶尔输出「Summary：」。"""

    def test_stream_path(self):
        parser = BehaviorParser(_Sink())
        raw, _ = parser.collect_stream(_stream_text("Summary：只有摘要\nSpeech：你好\n"), 5.0, tag="test")
        out = parser.parse_behavior(raw)
        assert out.summary == "只有摘要"
        assert out.speech == "你好"

    def test_non_stream_path(self):
        out = parse_behavior("Summary：只有摘要\nSpeech：你好\n", action_fallback=False)
        assert out.summary == "只有摘要"
        assert out.speech == "你好"


class TestEmotionWhitelist:
    """Emotion 只保留白名单内的名字，拼错或自造的名字直接丢弃。"""

    def test_unknown_emotion_dropped(self):
        out = parse_behavior("Emotion: hungy\n", action_fallback=False)
        assert out.emotion is None

    def test_valid_emotions_kept_in_order(self):
        out = parse_behavior("Emotion: HAPPY, hungy\nEmotion: shy\n", action_fallback=False)
        assert out.emotion == "happy, shy"

    def test_stream_path_filters_unknown(self):
        parser = BehaviorParser(_Sink())
        raw, _ = parser.collect_stream(_stream_text("Emotion: happy, hungy\n"), 5.0, tag="test")
        out = parser.parse_behavior(raw, action_fallback=False)
        assert out.emotion == "happy"


class TestUntaggedContentDiag:
    """未带标签的内容按格式契约整行丢弃，日志记字符数与正文原文。"""

    def test_prose_only_logs_counts_with_body(self, caplog):
        raw_text = "恋恋没有开始游戏哦，要不现在来一局？\n"
        parser = BehaviorParser(_Sink())
        with caplog.at_level(logging.WARNING, logger="pet.brain.parsing"):
            raw, _ = parser.collect_stream(_stream_text(raw_text), 5.0, tag="test")
        assert raw == raw_text
        records = [r.getMessage() for r in caplog.records
                   if r.levelno >= logging.WARNING and r.name == "pet.brain.parsing"]
        assert records
        assert all(str(len(raw_text)) in m for m in records)
        assert all("恋恋没有开始游戏哦" in m for m in records)

    def test_unparsed_body_is_reported_to_sink(self):
        """未解析的正文要回传给编排方，供重试时作为样本回传模型。"""
        sink = _Sink()
        parser = BehaviorParser(sink)

        parser.collect_stream(_stream_text("[09:24] 好呀，那恋恋开一局"), 5.0, tag="test")

        assert sink.unparsed == ["[09:24] 好呀，那恋恋开一局"]

    def test_tool_round_reasoning_only_is_not_warned(self, caplog):
        """工具轮次只带 reasoning、不带正文：空 raw 是预期形态，不按异常上报。"""
        parser = BehaviorParser(_Sink())
        stream = [
            _Chunk([_Choice(
                _Delta(reasoning_content="先开一局再说",
                       tool_calls=[_ToolCallDelta(0, id="call_1", name="game__init",
                                                  arguments='{"game_name": "rps"}')]),
                finish_reason="tool_calls")]),
        ]
        with caplog.at_level(logging.INFO, logger="pet.brain.parsing"):
            raw, tool_calls = parser.collect_stream(stream, 5.0, tag="test")
        assert raw == "" and tool_calls
        warnings = [r for r in caplog.records
                    if r.levelno >= logging.WARNING and r.name == "pet.brain.parsing"]
        assert not warnings


class TestRawCapture:
    """输出原文用于事件日志：解析出字段才挂 raw，未解析的正文留给 failed_turn。"""

    CONTENT = ("Summary: 想了\nEmotion: happy\nSpeech: 嗯\n"
               "Action: sit 5\nMood: joy+1\n")

    def test_parsed_content_attaches_raw(self):
        out = parse_behavior(self.CONTENT, action_fallback=False)
        assert out.raw == self.CONTENT

    def test_prose_only_has_no_raw(self):
        out = parse_behavior("恋恋没有开始游戏哦", action_fallback=False)
        assert out.raw is None

    def test_stream_returns_exact_content(self):
        parser = BehaviorParser(_Sink())
        raw, _ = parser.collect_stream(_stream_text(self.CONTENT, size=3), 5.0, tag="test")
        assert raw == self.CONTENT


class TestInlineTags:
    """模型偶尔不换行，把后继字段续写在同一行。"""

    SAMPLE = (
        "Summary: 屏幕窗口堆满表格，恋恋看不明白，踱开发呆。"
        "Speech: 「再说吧」「先放着」。。全是字"
        "Speech: 恋恋一个都看不懂呀。。。"
        "Action: walk right 150"
        "Action: confuse"
        "Action: look_around"
        "Action: thinking 25"
        "Action: walk left 100"
        "Action: sit 50"
    )

    def test_inline_fields_parse(self):
        out = parse_behavior(self.SAMPLE)
        assert out.summary == "屏幕窗口堆满表格，恋恋看不明白，踱开发呆。"
        assert out.speech_parts == ["「再说吧」「先放着」。。全是字", "恋恋一个都看不懂呀。。。"]
        assert [a.name for a in out.actions] == [
            "walk", "confuse", "look_around", "thinking", "walk", "sit"]

    def test_inline_labels_split_in_stream(self):
        _, _, chunks, ends = _collect("", stream=_chunks(self.SAMPLE))
        assert chunks == ["「再说吧」「先放着」。。全是字", "恋恋一个都看不懂呀。。。"]
        assert len(ends) == 1  # 第二个 Speech 触发气泡分行

    def test_inline_labels_split_across_chunks(self):
        """标签恰好被 chunk 边界切开：hold 滞回的核心场景，逐字符流入。"""
        raw, _, chunks, ends = _collect("", stream=_stream_text(self.SAMPLE, 1))
        streamed = parse_behavior(raw)
        assert streamed.speech_parts == ["「再说吧」「先放着」。。全是字", "恋恋一个都看不懂呀。。。"]
        assert [a.name for a in streamed.actions] == [
            "walk", "confuse", "look_around", "thinking", "walk", "sit"]
        assert "".join(chunks) == "「再说吧」「先放着」。。全是字恋恋一个都看不懂呀。。。"
        assert len(ends) == 1

    def test_inline_emotion_and_mood_parse(self):
        """整段无换行，且 Emotion 值与下一个标签直接粘连。"""
        raw = (
            "Summary: 午后犯困，窗外的雨声很轻。"
            "Emotion: sleepySpeech: 好安静呀。。。再躺一会儿。"
            "Action: look_aroundAction: sit 30Mood: joy-1 sanity-1"
        )
        out = parse_behavior(raw)
        assert out.summary == "午后犯困，窗外的雨声很轻。"
        assert out.emotion == "sleepy"
        assert out.speech == "好安静呀。。。再躺一会儿。"
        assert [a.name for a in out.actions] == ["look_around", "sit"]
        assert out.mood_deltas == {"joy": -1.0, "sanity": -1.0}

    def test_untagged_prefix_before_first_label_is_dropped(self):
        out = parse_behavior("先随手记一笔 Speech: 你好\n")
        assert out.speech == "你好"

    def test_colon_less_word_is_not_split(self):
        out = parse_behavior("Speech: 我做了个action plan，还没写完\n")
        assert out.speech == "我做了个action plan，还没写完"
        assert [a.name for a in out.actions] == ["sit"]  # 无动作时的兜底

    def test_tag_prefix_is_held_until_disproved(self):
        normalizer = InlineTagNormalizer()
        assert normalizer.feed("Su") == ""            # 可能是 Summary 的开头，先攒着
        assert normalizer.feed("nny") == "Sunny"      # 证伪后整段放行

    def test_flush_releases_incomplete_prefix(self):
        normalizer = InlineTagNormalizer()
        assert normalizer.feed("Spee") == ""
        assert normalizer.flush() == "Spee"
