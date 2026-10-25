"""事件存储测试：只追加、会话隔离、artifact 阈值与清理。"""

import time

from pet.brain.events import ARTIFACT_INLINE_LIMIT, ARTIFACT_MAX_BYTES, EventStore


def store(tmp_path) -> EventStore:
    return EventStore(str(tmp_path / "pet.db"))


class TestAppend:
    def test_seq_monotonic_and_ordered(self, tmp_path):
        s = store(tmp_path)
        first = s.append("user_msg", {"text": "一"})
        second = s.append("user_msg", {"text": "二"})
        assert second.seq > first.seq
        assert [e.payload["text"] for e in s.load()] == ["一", "二"]

    def test_only_append_api(self, tmp_path):
        s = store(tmp_path)
        for name in ("update", "delete", "remove", "rewrite"):
            assert not hasattr(s, name)

    def test_unknown_type_rejected(self, tmp_path):
        s = store(tmp_path)
        try:
            s.append("guess", {})
        except ValueError:
            return
        raise AssertionError("unknown type should raise")

    def test_payload_roundtrip_keeps_unicode(self, tmp_path):
        s = store(tmp_path)
        s.append("user_msg", {"text": "中文内容"}, refs={"tool_call_id": "c1"})
        event = s.load()[0]
        assert event.payload["text"] == "中文内容"
        assert event.refs["tool_call_id"] == "c1"

    def test_count_and_last_seq(self, tmp_path):
        s = store(tmp_path)
        assert s.count() == 0
        s.append("system_note", {"text": "备注"})
        assert s.count() == 1
        assert s.last_seq() == s.load()[0].seq


class TestSession:
    def test_new_session_hides_old_events(self, tmp_path):
        s = store(tmp_path)
        s.append("user_msg", {"text": "旧会话"})
        s.new_session()
        s.append("user_msg", {"text": "新会话"})
        assert [e.payload["text"] for e in s.load()] == ["新会话"]

    def test_old_events_not_deleted(self, tmp_path):
        s = store(tmp_path)
        old_id = s.current_session_id()
        s.append("user_msg", {"text": "旧会话"})
        s.new_session()
        assert len(s.load(session_id=old_id)) == 1

    def test_current_session_stable_across_instances(self, tmp_path):
        s = store(tmp_path)
        sid = s.current_session_id()
        other = EventStore(str(tmp_path / "pet.db"))
        assert other.current_session_id() == sid


class TestCompact:
    def test_below_threshold_skips(self, tmp_path):
        s = store(tmp_path)
        for i in range(5):
            s.append("user_msg", {"text": f"m{i}"})
        assert s.compact(min_events=10, keep_tail=2) == 0
        assert s.count() == 5

    def test_folds_prefix_into_snapshot(self, tmp_path):
        s = store(tmp_path)
        for i in range(12):
            s.append("user_msg", {"text": f"m{i}"})

        assert s.compact(min_events=10, keep_tail=3) == 9

        events = s.load()
        assert [e.type for e in events] == ["user_msg"] * 3 + ["snapshot"]
        snapshot = events[-1]
        assert snapshot.payload["covered_until"] == 9
        assert "m0" in snapshot.payload["summaries"][0]["text"]
        assert [e.payload["text"] for e in events[:3]] == ["m9", "m10", "m11"]

    def test_does_not_cut_tool_group(self, tmp_path):
        s = store(tmp_path)
        for i in range(7):
            s.append("user_msg", {"text": f"m{i}"})
        s.append("tool_call", {"name": "x__y"}, group_id="g1",
                 refs={"tool_call_id": "c1"})
        s.append("tool_result", {"ok": True, "brief": "好"}, group_id="g1",
                 refs={"tool_call_id": "c1"})
        s.append("user_msg", {"text": "tail"})

        assert s.compact(min_events=8, keep_tail=2) == 7

        events = s.load()
        assert events[-1].payload["covered_until"] == 7
        assert [e.type for e in events[:3]] == ["tool_call", "tool_result", "user_msg"]

    def test_folds_summary_text(self, tmp_path):
        s = store(tmp_path)
        for i in range(6):
            s.append("user_msg", {"text": f"m{i}"})
        s.append("summary", {"text": "前六条摘要"}, refs={"covers": [1, 6]})
        for i in range(6):
            s.append("user_msg", {"text": f"n{i}"})

        assert s.compact(min_events=8, keep_tail=3) == 10

        events = s.load()
        texts = [item["text"] for item in events[-1].payload["summaries"]]
        assert "前六条摘要" in texts
        assert all(e.type != "summary" for e in events)


    def test_keeps_previous_snapshot_summaries(self, tmp_path):
        s = store(tmp_path)
        for i in range(5):
            s.append("user_msg", {"text": f"a{i}"})
        s.append("summary", {"text": "早期摘要"}, refs={"covers": [1, 9]})
        for i in range(5):
            s.append("user_msg", {"text": f"b{i}"})

        assert s.compact(min_events=8, keep_tail=2, max_summaries=3) > 0
        first = s.load()[-1]
        assert "早期摘要" in [item["text"] for item in first.payload["summaries"]]

        for i in range(10):
            s.append("user_msg", {"text": f"c{i}"})

        assert s.compact(min_events=8, keep_tail=2, max_summaries=3) > 0
        second = s.load()[-1]
        assert "早期摘要" in [item["text"] for item in second.payload["summaries"]]


class TestPurgeSessions:
    def test_removes_events_of_closed_sessions(self, tmp_path):
        s = store(tmp_path)
        s.append("user_msg", {"text": "上一会话"})
        s.new_session()
        current = s.current_session_id()
        s.append("user_msg", {"text": "当前会话"})

        removed = s.purge_sessions(current)

        assert removed == 1
        assert [e.payload["text"] for e in s.load()] == ["当前会话"]

    def test_keeps_artifacts_referenced_by_current_session(self, tmp_path):
        s = store(tmp_path)
        old_artifact = s.put_artifact("旧" * (ARTIFACT_INLINE_LIMIT + 10))
        s.append("tool_result", {"ok": True, "brief": "旧", "artifact_id": old_artifact})
        s.new_session()
        current = s.current_session_id()
        kept_artifact = s.put_artifact("新" * (ARTIFACT_INLINE_LIMIT + 10))
        s.append("tool_result", {"ok": True, "brief": "新", "artifact_id": kept_artifact})

        s.purge_sessions(current)

        assert s.get_artifact(old_artifact) is None
        assert s.get_artifact(kept_artifact)

    def test_drops_all_artifacts_when_current_session_has_none(self, tmp_path):
        s = store(tmp_path)
        old_artifact = s.put_artifact("旧" * (ARTIFACT_INLINE_LIMIT + 10))
        s.new_session()

        s.purge_sessions(s.current_session_id())

        assert s.get_artifact(old_artifact) is None


class TestArtifacts:
    def test_size_recorded_in_bytes(self, tmp_path):
        s = store(tmp_path)
        body = "中" * (ARTIFACT_INLINE_LIMIT + 10)
        artifact_id = s.put_artifact(body)
        size = s._conn.execute(
            "SELECT size FROM session_artifacts WHERE artifact_id=?",
            (artifact_id,)).fetchone()[0]
        assert size == len(s.get_artifact(artifact_id).encode("utf-8"))

    def test_short_body_not_stored(self, tmp_path):
        s = store(tmp_path)
        assert s.put_artifact("x" * 10) is None

    def test_long_body_stored_and_truncated(self, tmp_path):
        s = store(tmp_path)
        body = "字" * (ARTIFACT_INLINE_LIMIT + 100)
        artifact_id = s.put_artifact(body)
        assert artifact_id
        assert len(s.get_artifact(artifact_id)) == len(body)

    def test_oversize_truncated_to_limit(self, tmp_path):
        s = store(tmp_path)
        body = "x" * (ARTIFACT_MAX_BYTES + 500)
        artifact_id = s.put_artifact(body)
        assert len(s.get_artifact(artifact_id)) == ARTIFACT_MAX_BYTES

    def test_cleanup_by_ttl(self, tmp_path):
        s = store(tmp_path)
        artifact_id = s.put_artifact("y" * (ARTIFACT_INLINE_LIMIT + 10))
        assert s.get_artifact(artifact_id)
        removed = s.cleanup_artifacts(now=time.time() + 10 * 86400)
        assert removed == 1
        assert s.get_artifact(artifact_id) is None
