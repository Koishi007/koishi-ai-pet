"""会话投影测试：纯函数、摘要覆盖、工具组原子裁剪、四档通道与伪调用清洗。"""

import re
import time

import pytest

from pet.brain.events import Event, args_digest
from pet.brain.projection import (Budget, Profile, project, replay_args,
                                  sanitize_assistant_text)

T0 = time.time() - 600


def ev(seq, type_, payload=None, *, group=None, refs=None, ts=None):
    return Event(seq, "s1", T0 + (ts if ts is not None else seq), type_,
                 group, payload or {}, refs or {})


def dialogue(text, seq, role="user"):
    return ev(seq, "user_msg" if role == "user" else "assistant_turn",
              {"text": text}, group=f"g{seq}" if role == "assistant" else None)


def tool_group(seq, name="bilibili__like", digest="bvid=BV1aPJR6oExx",
               brief="点赞成功", text="", results=True):
    gid = f"g{seq}"
    events = [
        ev(seq, "assistant_turn", {"text": text}, group=gid, refs={"tool_call_id": f"c{seq}"}),
        ev(seq + 1, "tool_call", {"name": name, "args_digest": digest, "side_effect": True},
           group=gid, refs={"tool_call_id": f"c{seq}"}),
    ]
    if results:
        events.append(ev(seq + 2, "tool_result", {"ok": True, "brief": brief},
                         group=gid, refs={"tool_call_id": f"c{seq}", "parent_seq": seq + 1}))
    return events


class TestPurity:
    def test_same_input_same_output(self):
        events = [dialogue("你好", 1)] + tool_group(2)
        budget = Budget(now=T0 + 1000)
        first = project(events, Profile(), budget)
        second = project(events, Profile(), budget)
        assert first == second

    def test_does_not_consume_context_or_events(self):
        events = [dialogue("你好", 1)]
        before = list(events)
        project(events)
        assert events == before


class TestSummaryCoverage:
    def test_covered_events_hidden_but_still_present(self):
        events = [
            dialogue("旧事一", 1),
            dialogue("旧事二", 2),
            ev(3, "summary", {"text": "旧事摘要"}, refs={"covers": [1, 2]}),
            dialogue("新事", 4),
        ]
        messages = project(events, Profile(), Budget(now=T0 + 1000))
        joined = " ".join(m["content"] for m in messages)
        assert "旧事一" not in joined
        assert "旧事摘要" in joined
        assert "新事" in joined
        assert len(events) == 4

    def test_superseded_event_not_projected(self):
        events = [
            dialogue("要笑的", 1),
            ev(2, "user_msg", {"text": "被替换"}, refs={"supersedes": 1}),
        ]
        joined = " ".join(m["content"] for m in project(events, budget=Budget(now=T0 + 1000)))
        assert "要笑的" not in joined

    def test_snapshot_hides_earlier_events(self):
        events = [
            dialogue("远古", 1),
            ev(2, "snapshot", {"v": 1, "covered_until": 1,
                               "summaries": [{"text": "早前摘要", "covers": [1, 1]}],
                               "tail": []}),
            dialogue("近事", 3),
        ]
        messages = project(events, Profile(), Budget(now=T0 + 1000))
        joined = " ".join(m["content"] for m in messages)
        assert "远古" not in joined
        assert "早前摘要" in joined
        assert "近事" in joined


class TestToolGroup:
    def test_native_emits_tool_calls_and_tool_messages(self):
        messages = project(tool_group(1), Profile(), Budget(now=T0 + 1000))
        assert messages[0]["role"] == "assistant"
        assert messages[0]["tool_calls"][0]["function"]["name"] == "bilibili__like"
        assert messages[1] == {"role": "tool", "tool_call_id": "c1", "content": "点赞成功"}

    def test_incomplete_group_degrades_and_never_emits_tool_role(self):
        events = tool_group(1, results=False)
        messages = project(events, Profile(), Budget(now=T0 + 1000))
        assert all(m["role"] != "tool" for m in messages)
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"].startswith("（环境记录）")

    def test_user_note_has_no_tool_calls(self):
        messages = project(tool_group(1), Profile(tool_channel="user-note"), Budget(now=T0 + 1000))
        assert all("tool_calls" not in m for m in messages)
        assert messages[-1]["role"] == "user"

    def test_system_note_uses_system_role(self):
        messages = project(tool_group(1), Profile(tool_channel="system-note"), Budget(now=T0 + 1000))
        assert messages[-1]["role"] == "system"

    def test_none_channel_drops_tool_records(self):
        messages = project(tool_group(1, text="我在看视频"), Profile(tool_channel="none"),
                           Budget(now=T0 + 1000))
        assert len(messages) == 1
        assert "tool_calls" not in messages[0]
        assert "点赞成功" not in messages[0]["content"]

    def test_multiple_tools_keep_order_and_ids(self):
        gid = "g9"
        events = [
            ev(9, "assistant_turn", {"text": "一起做"}, group=gid),
            ev(10, "tool_call", {"name": "a__x", "args_digest": "k=1"}, group=gid,
               refs={"tool_call_id": "c10"}),
            ev(11, "tool_call", {"name": "b__y", "args_digest": "k=2"}, group=gid,
               refs={"tool_call_id": "c11"}),
            ev(12, "tool_result", {"brief": "甲好了"}, group=gid, refs={"tool_call_id": "c10"}),
            ev(13, "tool_result", {"brief": "乙好了"}, group=gid, refs={"tool_call_id": "c11"}),
        ]
        messages = project(events, Profile(), Budget(now=T0 + 1000))
        assert [m["tool_call_id"] for m in messages if m["role"] == "tool"] == ["c10", "c11"]

    def test_empty_content_becomes_null(self):
        messages = project(tool_group(1), Profile(), Budget(now=T0 + 1000))
        assert messages[0]["content"] is None

    def test_aside_before_call_stays_in_group(self):
        # 事件顺序是 speech → tool_call → tool_result，单遍扫描会把台词落成孤立消息
        gid = "g5"
        events = [
            ev(5, "assistant_speech", {"text": "查一下天气"}, group=gid,
               refs={"tool_call_id": "c6"}),
            ev(6, "tool_call", {"name": "weather__get_current", "args_digest": "city=深圳"},
               group=gid, refs={"tool_call_id": "c6"}),
            ev(7, "tool_result", {"ok": True, "brief": "深圳晴"}, group=gid,
               refs={"tool_call_id": "c6"}),
        ]
        messages = project(events, Profile(), Budget(now=T0 + 1000))
        assert len(messages) == 2
        assert messages[0]["content"] == "Speech: 查一下天气"
        assert messages[1]["role"] == "tool"

    def test_aside_after_turn_keeps_speech_prefix(self):
        gid = "g9"
        events = [
            ev(9, "assistant_turn", {"text": "我查查"}, group=gid),
            ev(10, "assistant_speech", {"text": "查一下天气"}, group=gid,
               refs={"tool_call_id": "c11"}),
            ev(11, "tool_call", {"name": "weather__get_current", "args_digest": "-"},
               group=gid, refs={"tool_call_id": "c11"}),
            ev(12, "tool_result", {"brief": "深圳晴"}, group=gid,
               refs={"tool_call_id": "c11"}),
        ]
        messages = project(events, Profile(), Budget(now=T0 + 1000))
        assert messages[0]["content"] == "我查查\nSpeech: 查一下天气"

    def test_dropped_group_takes_its_aside_with_it(self):
        events = []
        seq = 1
        for i in range(3):
            gid = f"g{seq}"
            events += [
                ev(seq, "assistant_speech", {"text": f"第{i}句"}, group=gid),
                ev(seq + 1, "tool_call", {"name": "a__x", "args_digest": "-"},
                   group=gid, refs={"tool_call_id": f"c{seq}"}),
                ev(seq + 2, "tool_result", {"brief": "好"}, group=gid,
                   refs={"tool_call_id": f"c{seq}"}),
            ]
            seq += 3
        messages = project(events, Profile(), Budget(max_groups=1, now=T0 + 1000))
        assert len(messages) == 2
        assert "第2句" in messages[0]["content"]
        assert "第0句" not in messages[0]["content"]

    def test_groups_do_not_consume_dialogue_quota(self):
        events = []
        seq = 1
        for i in range(3):
            events += tool_group(seq, brief=f"第{i}次")
            seq += 3
        events.append(dialogue("要紧的话", seq, role="assistant"))
        messages = project(events, Profile(),
                           Budget(max_entries=1, max_groups=8, now=T0 + 1000))
        contents = [m.get("content") or "" for m in messages]
        assert any("要紧的话" in c for c in contents)
        assert sum(1 for m in messages if m["role"] == "tool") == 3

    def test_group_quota_follows_entries_ratio(self):
        events = []
        seq = 1
        for i in range(6):
            events += tool_group(seq, brief=f"第{i}次")
            seq += 3
        # max_entries=5 → 0.4 比例得 2，下限 3
        messages = project(events, Profile(), Budget(max_entries=5, now=T0 + 1000))
        assert sum(1 for m in messages if m["role"] == "tool") == 3

    def test_max_groups_keeps_newest(self):
        events = []
        seq = 1
        for i in range(3):
            events += tool_group(seq, brief=f"第{i}次")
            seq += 3
        messages = project(events, Profile(), Budget(max_groups=1, now=T0 + 1000))
        assert sum(1 for m in messages if m["role"] == "tool") == 1
        assert "第2次" in messages[-1]["content"]

    def test_expired_groups_filtered(self):
        events = tool_group(1)
        budget = Budget(evict_before_ts=T0 + 100, now=T0 + 1000)
        messages = project(events, Profile(), budget)
        assert all(m["role"] != "tool" for m in messages)


class TestSanitize:
    def test_fake_tool_line_replaced(self):
        clean, hits = sanitize_assistant_text("好呀\n[工具调用] bilibili__like → {...}\n完结")
        assert hits == 1
        assert "[工具调用]" not in clean
        assert "[已省略]" in clean
        assert "好呀" in clean

    def test_plain_discussion_untouched(self):
        text = "用户问我 [工具调用] 是什么，我解释了一下"
        clean, hits = sanitize_assistant_text(text)
        assert hits == 0
        assert clean == text

    def test_projection_sanitizes_but_event_keeps_original(self):
        event = dialogue("[工具调用] a__b → {\"x\": 1}", 1, role="assistant")
        messages = project([event], Profile(), Budget(now=T0 + 1000))
        assert "[已省略]" in messages[0]["content"]
        assert event.payload["text"].startswith("[工具调用]")


class TestFailedTurn:
    def test_failed_turn_never_projected(self):
        events = [
            ev(1, "failed_turn", {"text": "[工具调用] a__b → {}", "finish_reason": "stop"}),
            dialogue("正常", 2),
        ]
        joined = " ".join(m["content"] for m in project(events, budget=Budget(now=T0 + 1000)))
        assert "工具调用" not in joined
        assert "正常" in joined


class TestReplayArgs:
    def test_rebuilds_top_level_scalars(self):
        call = ev(1, "tool_call", {"name": "x", "args_digest": "bvid=BV1aP, count=3"})
        assert replay_args(call) == '{"bvid": "BV1aP", "count": 3}'

    def test_nested_fields_omitted(self):
        call = ev(1, "tool_call", {"name": "x", "args_digest": "meta={…}, keyword=小狗"})
        assert replay_args(call) == '{"keyword": "小狗"}'

    def test_empty_mode(self):
        call = ev(1, "tool_call", {"name": "x", "args_digest": "a=1"})
        assert replay_args(call, mode="empty") == "{}"

    def test_empty_digest(self):
        assert replay_args(ev(1, "tool_call", {"name": "x", "args_digest": "-"})) == "{}"


class TestArgsDigest:
    def test_limits_keys_and_truncates(self):
        digest = args_digest({"a": "x" * 60, "b": 1, "c": 2, "d": 3})
        assert "a=" in digest and "x" * 60 not in digest
        assert "d=" not in digest

    def test_nested_written_as_ellipsis(self):
        assert args_digest({"meta": {"k": "v"}}) == "meta={…}"

    def test_empty(self):
        assert args_digest(None) == "-"


class TestChannelText:
    def test_degraded_text_has_no_internal_names(self):
        events = tool_group(1, digest="bvid=BV1aP", brief="点赞成功")
        for channel in ("user-note", "system-note", "none"):
            messages = project(events, Profile(tool_channel=channel), Budget(now=T0 + 1000))
            for msg in messages:
                assert "bilibili__like" not in (msg.get("content") or "")
                assert "→" not in (msg.get("content") or "")
                assert "[工具调用]" not in (msg.get("content") or "")

    def test_unknown_channel_rejected(self):
        with pytest.raises(ValueError):
            Profile(tool_channel="degraded-system")


class TestTimePrefix:
    def test_prefix_uses_event_timestamp(self):
        messages = project([dialogue("你好", 1)], Profile(), Budget(now=T0 + 5))
        assert messages[0]["content"].startswith("[")
        assert "你好" in messages[0]["content"]

    def test_skip_last_excludes_tail(self):
        events = [dialogue("保留", 1), dialogue("丢弃", 2)]
        messages = project(events, Profile(), Budget(skip_last=1, now=T0 + 1000))
        assert len(messages) == 1
        assert "保留" in messages[0]["content"]


class TestHistoryFormat:
    """历史形态：assistant 行首是协议标签，时间只挂在 Summary 行与用户行上。

    时间进不了 Speech 行，模型照抄也不会把时间念出来；assistant 行照抄后仍是合法字段行。
    """

    @staticmethod
    def _speech(seq, text):
        return ev(seq, "assistant_turn", {"text": text, "kind": "speech"})

    @staticmethod
    def _summary(seq, text):
        return ev(seq, "assistant_turn", {"text": text, "kind": "summary"})

    def test_speech_line_is_protocol_only(self):
        messages = project([self._speech(1, "你好")], Profile(), Budget(now=T0 + 600))
        assert messages == [{"role": "assistant", "content": "Speech: 你好"}]

    def test_summary_line_carries_relative_label(self):
        messages = project([self._summary(1, "发呆了一轮")], Profile(),
                           Budget(now=T0 + 600))
        assert re.fullmatch(r"Summary: 〔\d+ 分钟前〕发呆了一轮", messages[0]["content"])

    def test_user_line_carries_relative_label(self):
        messages = project([dialogue("在吗", 1)], Profile(), Budget(now=T0 + 600))
        assert re.fullmatch(r"\[\d+ 分钟前\] 在吗", messages[0]["content"])

    def test_just_now_label_for_recent_event(self):
        messages = project([dialogue("在吗", 1)], Profile(), Budget(now=T0 + 5))
        assert messages[0]["content"].startswith("[刚刚]")

    def test_legacy_assistant_event_keeps_clock_shape(self):
        messages = project([dialogue("旧台词", 1, role="assistant")], Profile(),
                           Budget(now=T0 + 600))
        assert messages[0]["content"].startswith("[")
        assert "旧台词" in messages[0]["content"]

    def test_turn_id_merges_summary_and_speech(self):
        events = [
            ev(1, "assistant_turn",
               {"text": "发呆了一轮", "kind": "summary", "turn_id": "t1"}),
            ev(2, "assistant_turn",
               {"text": "唔…", "kind": "speech", "turn_id": "t1"}),
        ]
        messages = project(events, Profile(), Budget(now=T0 + 600))
        assert len(messages) == 1
        assert re.fullmatch(r"Summary: 〔\d+ 分钟前〕发呆了一轮\nSpeech: 唔…",
                            messages[0]["content"])

    def test_turn_block_counts_as_one_entry(self):
        events = [
            ev(1, "assistant_turn",
               {"text": "发呆了一轮", "kind": "summary", "turn_id": "t1"}),
            ev(2, "assistant_turn",
               {"text": "唔…", "kind": "speech", "turn_id": "t1"}),
        ]
        messages = project(events, Profile(), Budget(max_entries=1, now=T0 + 600))
        assert len(messages) == 1
        assert "Speech: 唔…" in messages[0]["content"]
