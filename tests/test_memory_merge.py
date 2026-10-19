"""事件记忆汇总：解析、连接回退与写回。"""

import pytest

from pet.brain import memory_merge
from pet.brain.memory import MemoryStore
from pet.config import config


class TestParseGroups:
    def test_basic(self):
        text = "Group: #1,#2 | 拍视频，镜头重拍后补拍完成\n"
        assert memory_merge.parse_groups(text, {1, 2}) == [
            ([1, 2], "拍视频，镜头重拍后补拍完成")]

    def test_fullwidth_and_code_fence(self):
        text = "```\nGroup：＃1，＃2 ｜ 全角写法\n```\n"
        assert memory_merge.parse_groups(text, {1, 2}) == [([1, 2], "全角写法")]

    def test_unknown_id_dropped(self):
        assert memory_merge.parse_groups("Group: #1,#99 | 只保留存在的\n", {1, 2}) == []

    def test_single_member_ignored(self):
        assert memory_merge.parse_groups("Group: #1 | 单条\n", {1}) == []

    def test_duplicate_id_stays_in_first_group(self):
        text = "Group: #1,#2 | 第一组\nGroup: #2,#3 | 第二组\n"
        assert memory_merge.parse_groups(text, {1, 2, 3}) == [([1, 2], "第一组")]

    def test_empty_summary_ignored(self):
        assert memory_merge.parse_groups("Group: #1,#2 |   \n", {1, 2}) == []

    def test_summary_truncated(self):
        groups = memory_merge.parse_groups(f"Group: #1,#2 | {'字' * 500}\n", {1, 2})
        assert len(groups[0][1]) == memory_merge._MAX_SUMMARY_CHARS


class TestConnection:
    def test_independent_when_all_set(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_MERGE_URL", "https://merge.example/v1")
        monkeypatch.setattr(config, "MEMORY_MERGE_KEY", "merge-key")
        monkeypatch.setattr(config, "MEMORY_MERGE_MODEL", "merge-model")
        assert memory_merge._resolve_connection() == (
            "merge-key", "https://merge.example/v1", "merge-model")

    def test_partial_falls_back(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_MERGE_URL", "https://merge.example/v1")
        monkeypatch.setattr(config, "MEMORY_MERGE_KEY", "")
        monkeypatch.setattr(config, "MEMORY_MERGE_MODEL", "merge-model")
        monkeypatch.setattr(config, "LLM_URL", "https://primary.example/v1")
        monkeypatch.setattr(config, "LLM_KEY", "primary-key")
        monkeypatch.setattr(config, "LLM_MODEL", "primary-model")
        monkeypatch.setattr(config, "LLM_ACTIVE_PROFILE", "primary")
        assert memory_merge._resolve_connection() == (
            "primary-key", "https://primary.example/v1", "primary-model")


class TestFormatRecords:
    def test_strips_year_and_seconds(self):
        rows = [{"id": 12, "created_at": "2026-10-17T10:02:07.454812",
                 "content": "准备拍视频"}]
        assert memory_merge._format_records(rows) == "#12 10-17 10:02 准备拍视频"


class TestRunMerge:
    @pytest.fixture
    def store(self, tmp_path) -> MemoryStore:
        return MemoryStore(db_path=str(tmp_path / "merge.db"))

    def test_applies_group_and_deletes_members(self, store, monkeypatch):
        store.save("event", "准备拍视频", ["拍摄"], 3, "L2")
        store.save("event", "镜头已补拍完成", ["补拍"], 3, "L2")
        monkeypatch.setattr(memory_merge, "_call_llm",
                            lambda messages: "Group: #1,#2 | 拍视频，镜头已补拍完成")
        assert memory_merge.run_merge(store) == 1
        assert [r["content"] for r in store.list_event_candidates()] == [
            "拍视频，镜头已补拍完成"]

    def test_untouched_rows_stay(self, store, monkeypatch):
        store.save("event", "准备拍视频", ["拍摄"], 3, "L2")
        store.save("event", "镜头已补拍完成", ["补拍"], 3, "L2")
        store.save("event", "开发贴边功能", ["贴边"], 2, "L3")
        monkeypatch.setattr(memory_merge, "_call_llm",
                            lambda messages: "Group: #1,#2 | 拍视频，镜头已补拍完成")
        memory_merge.run_merge(store)
        contents = [r["content"] for r in store.list_event_candidates()]
        assert "开发贴边功能" in contents

    def test_empty_output_keeps_rows(self, store, monkeypatch):
        store.save("event", "准备拍视频", ["拍摄"], 3, "L2")
        store.save("event", "镜头已补拍完成", ["补拍"], 3, "L2")
        monkeypatch.setattr(memory_merge, "_call_llm", lambda messages: "")
        assert memory_merge.run_merge(store) == 0
        assert len(store.list_event_candidates()) == 2

    def test_merged_created_at_uses_newest(self, store, monkeypatch):
        store.save("event", "准备拍视频", ["拍摄"], 3, "L2")
        store.save("event", "镜头已补拍完成", ["补拍"], 3, "L2")
        rows = store.list_event_candidates()
        newest = max(r["created_at"] for r in rows)
        ids = ",".join(str(r["id"]) for r in rows)
        monkeypatch.setattr(memory_merge, "_call_llm",
                            lambda messages: f"Group: {ids} | 拍视频汇总")
        assert memory_merge.run_merge(store) == 1
        assert store.list_event_candidates()[0]["created_at"] == newest

    def test_rollback_on_write_failure(self, store):
        store.save("event", "准备拍视频", ["拍摄"], 3, "L2")
        store.save("event", "镜头已补拍完成", ["补拍"], 3, "L2")
        rows = store.list_event_candidates()
        ids = [int(r["id"]) for r in rows]
        # 汇总内容传 None 触发 NOT NULL 约束，验证插入与删除一起回滚
        with pytest.raises(Exception):
            store.apply_event_merges([(ids, None)], rows)  # type: ignore[list-item]
        assert len(store.list_event_candidates()) == 2

    def test_merged_row_gets_embedding(self, store, monkeypatch):
        store.save("event", "准备拍视频", ["拍摄"], 3, "L2")
        store.save("event", "镜头已补拍完成", ["补拍"], 3, "L2")
        rows = store.list_event_candidates()
        ids = ",".join(str(r["id"]) for r in rows)
        embedded = []
        monkeypatch.setattr(
            store._retriever, "embed_memories",
            lambda items: embedded.extend(items) or len(items))
        monkeypatch.setattr(memory_merge, "_call_llm",
                            lambda messages: f"Group: {ids} | 拍视频汇总")
        assert memory_merge.run_merge(store) == 1
        assert len(embedded) == 1
        assert embedded[0][1] == "拍视频汇总"
