"""事件日志接入上下文层：旧表零读写、工具组投影、会话切换。"""

import sqlite3

from pet.brain.base import BrainMixin
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

    def test_user_and_assistant_roundtrip(self, tmp_path):
        brain = _brain(tmp_path)
        brain.add_context("user", "在吗")
        brain.add_context("assistant", "在的")
        messages = brain.get_multi_turn_messages()
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert "在吗" in messages[0]["content"]

    def test_clear_context_switches_session_without_deleting(self, tmp_path):
        brain = _brain(tmp_path)
        brain.add_context("user", "旧会话")
        old_session = brain._events.current_session_id()
        brain.clear_context()
        assert brain.get_multi_turn_messages() == []
        assert brain._events.count(session_id=old_session) >= 1
        brain.add_context("user", "新会话")
        assert "新会话" in brain.get_multi_turn_messages()[0]["content"]

    def test_skip_last_excludes_current_user_message(self, tmp_path):
        brain = _brain(tmp_path)
        brain.add_context("user", "上一句")
        brain.add_context("user", "这一句")
        messages = brain.get_multi_turn_messages(skip_last=1)
        assert len(messages) == 1
        assert "上一句" in messages[0]["content"]


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
