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


class TestArtifacts:
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
