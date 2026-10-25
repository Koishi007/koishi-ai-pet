"""事件日志接入上下文层：旧表零读写、工具组投影、会话切换。"""

import sqlite3

from pet.brain.base import BrainMixin
from pet.brain.events import ASSISTANT_TEXT_LIMIT
from pet.brain.tool_channel import reset_cache


def _brain(tmp_path) -> BrainMixin:
    reset_cache()
    return BrainMixin(str(tmp_path / "pet.db"))


class TestLegacyTables:
    def test_writes_only_go_to_session_events(self, tmp_path):
        db = tmp_path / "pet.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE context_entries (id INTEGER PRIMARY KEY, role TEXT, "
                     "content TEXT, timestamp REAL, is_summary INTEGER)")
        conn.execute("INSERT INTO context_entries (role, content, timestamp) "
                     "VALUES ('user', '升级前的旧历史', 1)")
        conn.execute("CREATE TABLE context_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        conn.close()

        brain = BrainMixin(str(db))
        brain.add_context("user", "新消息")

        conn = sqlite3.connect(db)
        assert conn.execute("SELECT COUNT(*) FROM context_entries").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM session_events").fetchone()[0] >= 1
        texts = [r[0] for r in conn.execute(
            "SELECT payload FROM session_events").fetchall()]
        assert any("新消息" in t for t in texts)
        conn.close()

    def test_old_history_not_projected(self, tmp_path):
        db = tmp_path / "pet.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE context_entries (id INTEGER PRIMARY KEY, role TEXT, "
                     "content TEXT, timestamp REAL, is_summary INTEGER)")
        conn.execute("INSERT INTO context_entries (role, content, timestamp) "
                     "VALUES ('user', '旧历史', 1)")
        conn.commit()
        conn.close()

        brain = BrainMixin(str(db))
        assert brain.get_multi_turn_messages() == []


class TestToolEventRecording:
    def test_written_events_report_success(self, tmp_path):
        brain = _brain(tmp_path)
        ok = brain.record_tool_events(
            "g1",
            [{"id": "c1", "name": "timer__set"}],
            [{"tool_call_id": "c1", "ok": True, "brief": "已设置"}],
        )
        assert ok is True

    def test_missing_event_store_reports_failure(self):
        assert BrainMixin().record_tool_events("g1", [], []) is False


class TestSummaryCovers:
    def test_drained_covers_attach_to_summary_event(self, tmp_path):
        brain = _brain(tmp_path)
        brain._pending_summary_queue.extend(
            [(1, 1, "[user] 甲"), (2, 2, "[assistant] 乙")])
        texts, covers = brain.drain_pending_summaries()
        assert texts == ["[user] 甲", "[assistant] 乙"]
        assert covers == (1, 2)

        brain.add_context("system", "[历史摘要] 甲乙", is_summary=True, covers=covers)
        summary = [e for e in brain.dump_events() if e["type"] == "summary"][0]
        assert summary["refs"]["covers"] == [1, 2]

    def test_each_drain_returns_its_own_covers(self, tmp_path):
        brain = _brain(tmp_path)
        brain._pending_summary_queue.append((1, 3, "[user] 甲"))
        _, first = brain.drain_pending_summaries()
        brain._pending_summary_queue.append((5, 6, "[user] 乙"))
        _, second = brain.drain_pending_summaries()
        assert (first, second) == ((1, 3), (5, 6))


class TestProjectionEntry:
    def test_tool_group_renders_native_when_channel_allows(self, tmp_path):
        brain = _brain(tmp_path)
        brain.record_tool_events(
            "g1",
            [{"id": "c1", "name": "bilibili__like", "args_digest": "bvid=BV1"}],
            [{"tool_call_id": "c1", "ok": True, "brief": "点赞成功"}],
        )
        messages = brain.get_multi_turn_messages()
        assert messages[0]["tool_calls"][0]["function"]["name"] == "bilibili__like"
        assert messages[1]["role"] == "tool"

    def test_user_roundtrip(self, tmp_path):
        brain = _brain(tmp_path)
        brain.add_context("user", "在吗")
        messages = brain.get_multi_turn_messages()
        assert [m["role"] for m in messages] == ["user"]
        assert "在吗" in messages[0]["content"]

    def test_structured_assistant_roundtrip(self, tmp_path):
        brain = _brain(tmp_path)
        fields = {"summary": "想了",
                  "actions": [{"name": "sit", "kwargs": {"duration": 5}}]}
        raw = "Summary: 想了\nAction: sit 5"
        brain.add_assistant_turn(fields, raw)

        event = [e for e in brain.dump_events() if e["type"] == "assistant_turn"][0]
        assert event["payload"]["v"] == 1
        assert event["payload"]["text"] == raw
        assert brain.get_multi_turn_messages() == [
            {"role": "assistant",
             "content": "Summary: 想了\nAction: sit duration=5"}]

    def test_long_text_is_truncated_but_fields_survive(self, tmp_path):
        brain = _brain(tmp_path)
        brain.add_assistant_turn({"summary": "长了"}, "字" * 2000)

        payload = [e for e in brain.dump_events()
                   if e["type"] == "assistant_turn"][0]["payload"]
        assert payload["text_truncated"] is True
        assert len(payload["text"].encode("utf-8")) <= ASSISTANT_TEXT_LIMIT
        assert brain.get_multi_turn_messages() == [
            {"role": "assistant", "content": "Summary: 长了"}]

    def test_invalid_emotion_dropped_before_store(self, tmp_path):
        brain = _brain(tmp_path)
        raw = "Emotion: hungy\nSummary: 想"
        brain.add_assistant_turn({"emotion": "hungy", "summary": "想"}, raw)

        payload = [e for e in brain.dump_events()
                   if e["type"] == "assistant_turn"][0]["payload"]
        assert "emotion" not in payload
        assert payload["text"] == raw
        assert brain.get_multi_turn_messages() == [
            {"role": "assistant", "content": "Summary: 想"}]

    def test_clear_context_deletes_history(self, tmp_path):
        brain = _brain(tmp_path)
        brain.add_context("user", "旧会话")
        old_session = brain._events.current_session_id()

        brain.clear_context()

        assert brain.get_multi_turn_messages() == []
        assert brain._events.count(session_id=old_session) == 0
        brain.add_context("user", "新会话")
        assert "新会话" in brain.get_multi_turn_messages()[0]["content"]

    def test_skip_last_excludes_current_user_message(self, tmp_path):
        brain = _brain(tmp_path)
        brain.add_context("user", "上一句")
        brain.add_context("user", "这一句")
        messages = brain.get_multi_turn_messages(skip_last=1)
        assert len(messages) == 1
        assert "上一句" in messages[0]["content"]


class TestSnapshotRestore:
    def test_context_snapshot_rebuilt_on_restart(self, tmp_path):
        db = str(tmp_path / "pet.db")
        first = _brain(tmp_path)
        first.add_context("user", "重启前的话")
        first.add_assistant_turn({"summary": "记住了"}, "Summary: 记住了")
        first.close_events()

        reset_cache()
        second = BrainMixin(db)
        entries = second.context_entries()
        assert [e.role for e in entries] == ["user", "assistant"]
        assert entries[0].content == "重启前的话"
        assert entries[1].content == "Summary: 记住了"
        assert entries[0].seq > 0

    def test_summary_restored_flag(self, tmp_path):
        db = str(tmp_path / "pet.db")
        first = _brain(tmp_path)
        first.add_context("system", "[历史摘要] 之前聊过天气", is_summary=True)
        first.close_events()

        reset_cache()
        second = BrainMixin(db)
        entries = second.context_entries()
        assert len(entries) == 1
        assert entries[0].is_summary is True

    def test_restart_does_not_duplicate_events(self, tmp_path):
        db = str(tmp_path / "pet.db")
        first = _brain(tmp_path)
        first.add_context("user", "一次")
        count = first._events.count()
        first.close_events()

        reset_cache()
        second = BrainMixin(db)
        assert second._events.count() == count


def test_duplicate_summary_supersedes_and_merges_covers(tmp_path):
    """相似摘要命中时写事件而不是只改快照：旧事件被替换，覆盖区间合并。"""
    brain = _brain(tmp_path)
    brain.add_context("user", "第一句")
    brain.add_context("user", "第二句")
    brain._drained_covers = [(1, 2)]
    brain.add_context("system", "[历史摘要] 之前聊过天气", is_summary=True)
    brain.add_context("user", "第三句")
    brain._drained_covers = [(4, 4)]
    brain.add_context("system", "[历史摘要] 之前聊过天气的事", is_summary=True)

    summaries = [e for e in brain.dump_events() if e["type"] == "summary"]
    assert len(summaries) == 2
    assert summaries[1]["refs"]["supersedes"] == summaries[0]["seq"]
    assert summaries[1]["refs"]["covers"] == [1, 4]

    projected = [m for m in brain.get_multi_turn_messages()
                 if "历史摘要" in (m.get("content") or "")]
    assert len(projected) == 1


class TestArtifactProducer:
    def test_long_raw_result_stored_as_artifact(self, tmp_path):
        brain = _brain(tmp_path)
        raw = "长" * 3000
        brain.record_tool_events(
            "g1",
            [{"id": "c1", "name": "x__y", "args_digest": "-", "side_effect": True}],
            [{"tool_call_id": "c1", "ok": True, "brief": "完成", "raw": raw}],
        )
        result = [e for e in brain.dump_events() if e["type"] == "tool_result"][0]
        artifact_id = result["payload"]["artifact_id"]
        assert artifact_id
        assert brain._events.get_artifact(artifact_id) == raw

    def test_short_raw_result_keeps_no_artifact(self, tmp_path):
        brain = _brain(tmp_path)
        brain.record_tool_events(
            "g1",
            [{"id": "c1", "name": "x__y", "args_digest": "-", "side_effect": True}],
            [{"tool_call_id": "c1", "ok": True, "brief": "完成", "raw": "短"}],
        )
        result = [e for e in brain.dump_events() if e["type"] == "tool_result"][0]
        assert result["payload"]["artifact_id"] is None

    def test_side_effect_flag_comes_from_call(self, tmp_path):
        brain = _brain(tmp_path)
        brain.record_tool_events(
            "g1",
            [{"id": "c1", "name": "reader__read", "args_digest": "-", "side_effect": False}],
            [{"tool_call_id": "c1", "ok": True, "brief": "读了"}],
        )
        call = [e for e in brain.dump_events() if e["type"] == "tool_call"][0]
        assert call["payload"]["side_effect"] is False


class TestMaintenance:
    def test_unclosed_group_reported(self, tmp_path):
        brain = _brain(tmp_path)
        brain.record_tool_events(
            "g-open",
            [{"id": "c1", "name": "x__y", "args_digest": "-"}],
            [],
        )
        groups = brain._events.unclosed_groups()
        assert groups and groups[0]["group_id"] == "g-open"

    def test_maintenance_runs_without_events(self, tmp_path):
        brain = BrainMixin(None)
        brain.maintenance()
        assert brain.dump_events() == []
