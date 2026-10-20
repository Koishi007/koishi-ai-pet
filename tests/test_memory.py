"""记忆系统测试：行解析、衰减公式、去重与关键词工具。"""

import time
from datetime import datetime, timedelta

import pytest

from pet.brain.memory import (
    LightweightDeduplicator, MemoryStore, _day_ceil, _day_floor, _drop_subsumed,
    _escape_like, _MemoryRetriever,
)


class TestLightweightDeduplicator:
    def test_identical_text(self):
        dedup = LightweightDeduplicator()
        assert dedup.compute_similarity("用户喜欢喝咖啡", "用户喜欢喝咖啡") == pytest.approx(1.0)

    def test_similar_text_above_dissimilar(self):
        dedup = LightweightDeduplicator()
        similar = dedup.compute_similarity("用户喜欢喝咖啡", "用户喜欢喝拿铁")
        different = dedup.compute_similarity("用户喜欢喝咖啡", "明天下午三点开会")
        assert similar > different

    def test_empty_text_scores_zero(self):
        assert LightweightDeduplicator().compute_similarity("", "内容") == 0.0

    def test_find_duplicates_respects_threshold_and_order(self):
        dedup = LightweightDeduplicator(sim_threshold=0.5)
        existing = ["用户喜欢喝咖啡", "明天下午三点开会", "用户喜欢喝咖啡和茶"]
        found = dedup.find_duplicates("用户喜欢喝咖啡", existing)
        assert found, "应至少命中一条"
        assert found[0][1] >= found[-1][1], "结果按相似度降序"
        assert all(idx != 1 for idx, _ in found)


class TestTextHelpers:
    def test_escape_like_wildcards(self):
        assert _escape_like("50%_off\\") == "50\\%\\_off\\\\"

    def test_drop_subsumed_keeps_longest(self):
        assert _drop_subsumed(["咖啡", "喝咖啡", "茶"]) == ["喝咖啡", "茶"]

    def test_drop_subsumed_dedups_case_insensitively(self):
        assert _drop_subsumed(["Coffee", "coffee"]) == ["Coffee"]

    def test_day_bounds_form_half_open_range(self):
        assert _day_floor(" 2026-09-27 ") == "2026-09-27"
        assert _day_ceil("2026-09-27") == "2026-09-28"
        assert _day_ceil("2026-12-31") == "2027-01-01"

    def test_invalid_date_raises(self):
        with pytest.raises(ValueError):
            _day_floor("2026/09/27")


class TestDecayAndLevels:
    @pytest.fixture
    def retriever(self, case_db_path) -> _MemoryRetriever:
        return MemoryStore(db_path=str(case_db_path))._retriever

    def test_half_life_table(self, retriever):
        assert retriever._half_life({"level": "L1", "importance": 5}) == float("inf")
        assert retriever._half_life({"level": "L3", "importance": 5}) == 3
        assert retriever._half_life({"level": "L2", "importance": 4}) == 30
        # 未知等级回退 L2 表，未知重要性回退 45 天
        assert retriever._half_life({"level": "L9", "importance": 4}) == 30
        assert retriever._half_life({"level": "L2", "importance": 9}) == 45

    def test_merge_level_takes_higher(self, retriever):
        assert retriever._merge_level("L2", "L1") == "L1"
        assert retriever._merge_level("L3", "L2") == "L2"
        assert retriever._merge_level("L1", "L3") == "L1"

    def test_permanent_memory_does_not_decay(self, retriever):
        old = (datetime.now() - timedelta(days=365)).isoformat()
        row = {"level": "L1", "importance": 5, "created_at": old}
        assert retriever._effective_importance(row) == pytest.approx(5.0)

    def test_temporary_memory_decays_with_age(self, retriever):
        fresh = {"level": "L2", "importance": 3, "created_at": datetime.now().isoformat()}
        stale = {"level": "L2", "importance": 3,
                 "created_at": (datetime.now() - timedelta(days=28)).isoformat()}
        assert retriever._effective_importance(stale) < retriever._effective_importance(fresh)

    def test_missing_created_at_returns_base(self, retriever):
        assert retriever._effective_importance({"level": "L2", "importance": 4}) == 4

    def test_recall_bonus_only_after_access(self, retriever):
        assert retriever._recency_factor({}) == 1.0
        just_now = {"last_accessed_at": datetime.now().isoformat()}
        assert retriever._recency_factor(just_now) > 1.0

    def test_effective_importance_capped_at_five(self, retriever):
        row = {"level": "L1", "importance": 5,
               "last_accessed_at": datetime.now().isoformat()}
        assert retriever._effective_importance(row) <= 5.0


class TestSaveFromLine:
    @pytest.fixture
    def retriever(self, case_db_path, monkeypatch) -> _MemoryRetriever:
        retriever = MemoryStore(db_path=str(case_db_path))._retriever
        monkeypatch.setattr(retriever, "_extract_keywords", lambda _text: ["自动关键词"])
        return retriever

    def _capture(self, retriever, monkeypatch) -> dict:
        captured = {}

        def fake_save(category, content, keywords, importance, level):
            captured.update(category=category, content=content, keywords=keywords,
                            importance=importance, level=level)

        monkeypatch.setattr(retriever, "save", fake_save)
        return captured

    def test_full_line_parsed(self, retriever, monkeypatch):
        captured = self._capture(retriever, monkeypatch)
        retriever.save_from_line(
            "[偏好] 用户喜欢深夜写码 | keywords: 深夜, 代码 | importance: 5 | level: L1")
        assert captured == {
            "category": "偏好", "content": "用户喜欢深夜写码",
            "keywords": ["深夜", "代码"], "importance": 5, "level": "L1",
        }

    def test_keywords_fall_back_to_extraction(self, retriever, monkeypatch):
        captured = self._capture(retriever, monkeypatch)
        retriever.save_from_line("[事件] 今天下了大雨")
        assert captured["keywords"] == ["自动关键词"]
        assert captured["category"] == "事件"
        assert captured["content"] == "今天下了大雨"

    def test_importance_clamped_to_range(self, retriever, monkeypatch):
        captured = self._capture(retriever, monkeypatch)
        retriever.save_from_line("[偏好] 阈值上限 | importance: 9 | level: L2")
        assert captured["importance"] == 5
        retriever.save_from_line("[偏好] 阈值下限 | importance: 0 | level: L2")
        assert captured["importance"] == 1

    def test_l1_raises_low_importance(self, retriever, monkeypatch):
        captured = self._capture(retriever, monkeypatch)
        retriever.save_from_line("[核心] 重要事实 | importance: 1 | level: L1")
        assert captured["importance"] == 3
        assert captured["level"] == "L1"

    def test_l3_caps_high_importance(self, retriever, monkeypatch):
        captured = self._capture(retriever, monkeypatch)
        retriever.save_from_line("[临时] 随口一句 | importance: 5 | level: L3")
        assert captured["importance"] == 4
        assert captured["level"] == "L3"

    def test_low_importance_demoted_to_l3(self, retriever, monkeypatch):
        captured = self._capture(retriever, monkeypatch)
        retriever.save_from_line("[事件] 路过 | importance: 2 | level: L2")
        assert captured["level"] == "L3"

    def test_invalid_level_falls_back_to_l2(self, retriever, monkeypatch):
        captured = self._capture(retriever, monkeypatch)
        retriever.save_from_line("[偏好] 无等级标记 | level: L9")
        assert captured["level"] == "L2"
        assert captured["importance"] == 3

    def test_unparsable_line_is_ignored(self, retriever, monkeypatch):
        calls = []
        monkeypatch.setattr(retriever, "save", lambda *a: calls.append(a))
        retriever.save_from_line("")
        assert calls == []


class TestRandomEvents:
    @pytest.fixture
    def store(self, case_db_path) -> MemoryStore:
        store = MemoryStore(db_path=str(case_db_path))
        store.save("event", "用户今天加班到很晚", ["加班"], 3, "L2")
        store.save("event", "用户上周带猫去看了医生", ["猫"], 3, "L2")
        store.save("user_fact", "用户住在杭州", ["杭州"], 5, "L1")
        return store

    def test_only_event_category_returned(self, store):
        contents = [r["content"] for r in store.random_events(5)]
        assert len(contents) == 2
        assert "用户住在杭州" not in contents

    def test_exclude_ids_filters_candidates(self, store):
        ids = {r["id"] for r in store.random_events(5)}
        assert store.random_events(5, exclude_ids=ids) == []

    def test_zero_limit_returns_empty(self, store):
        assert store.random_events(0) == []

    def test_same_topic_returns_latest(self, store):
        store.save("event", "准备拍视频，镜头需重拍", ["视频拍摄"], 3, "L2")
        store.save("event", "视频已拍完，素材不用补拍", ["视频拍摄"], 3, "L2")
        contents = [r["content"] for r in store.random_events(10)]
        assert "视频已拍完，素材不用补拍" in contents
        assert "准备拍视频，镜头需重拍" not in contents

    def test_no_shared_keyword_returns_seed(self, store):
        contents = [r["content"] for r in store.random_events(10)]
        assert "用户今天加班到很晚" in contents
        assert "用户上周带猫去看了医生" in contents

    def test_marker_orders_by_id_on_same_timestamp(self, store):
        store.save("event", "甲事件", ["甲"], 3, "L2")
        store.save("event", "乙事件", ["乙"], 3, "L2")
        with store._retriever._lock:
            conn = store._retriever._conn
            conn.execute("UPDATE memories SET created_at = '2026-10-19T10:00:00'")
            conn.commit()
            newest_id = conn.execute(
                "SELECT MAX(id) FROM memories WHERE category = 'event'").fetchone()[0]
        assert store.latest_event_marker() == f"2026-10-19T10:00:00#{newest_id:08d}"

    def test_pending_events_without_marker_counts_all(self, store):
        count, latest = store.pending_events(None)
        assert count == 2
        assert latest == store.latest_event_marker()

    def test_pending_events_counts_after_marker(self, store):
        oldest = store.list_event_candidates()[-1]
        marker = f"{oldest['created_at']}#{oldest['id']:08d}"
        before = store.pending_events(marker)[0]
        store.save("event", "甲事件", ["甲"], 3, "L2")
        count, latest = store.pending_events(marker)
        assert count == before + 1
        assert latest == store.latest_event_marker()

    def test_pending_events_after_latest_is_zero(self, store):
        assert store.pending_events(store.latest_event_marker())[0] == 0

    def test_injection_does_not_touch_access_stats(self, store):
        store.random_events(5)
        rows, _ = store.list_memories()
        assert all(r["access_count"] == 0 for r in rows)

    def test_format_memory_time_delegates(self, store):
        assert store.format_memory_time("") == ""
