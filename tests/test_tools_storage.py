"""工具持久化测试：定时器重启恢复、待办 CRUD、对话历史清理。"""

import time
from datetime import datetime, timedelta

import pytest

from pet.brain.conversation_store import ConversationStore
from pet.tools import timer as timer_pkg  # noqa: F401  确保子模块已注册
from pet.tools.timer import core as timer_core
from pet.tools.timer.core import TimerTool
from pet.tools.timer.storage import TimerStorage
from pet.tools.todo.storage import TodoStorage


class _FakeContext:
    """记录 TOOL_CTX 调用，替代真实 agent 绑定。"""

    def __init__(self):
        self.speeches = []
        self.notices = []
        self.events = []
        self.alarms = []

    def speech(self, text, duration=0):
        self.speeches.append(text)

    def notify(self, title, body):
        self.notices.append((title, body))

    def note_event(self, kind, text=""):
        self.events.append((kind, text))

    def register_alarm(self, fire_at_ms, callback, key=""):
        self.alarms.append((fire_at_ms, callback, key))

    def unregister_alarm(self, key):
        return True


@pytest.fixture
def fake_ctx(monkeypatch) -> _FakeContext:
    ctx = _FakeContext()
    monkeypatch.setattr(timer_core, "TOOL_CTX", ctx)
    return ctx


@pytest.fixture
def timer_storage(case_db_path) -> TimerStorage:
    return TimerStorage(db_path=str(case_db_path))


class TestTimerStorage:
    def test_save_and_load_ordered_by_fire_at(self, timer_storage):
        timer_storage.save("b", "key_b", "后响", 60, 2000.0)
        timer_storage.save("a", "key_a", "先响", 30, 1000.0)
        rows = timer_storage.load_all()
        assert [r["id"] for r in rows] == ["a", "b"]

    def test_remove_reports_rowcount(self, timer_storage):
        timer_storage.save("a", "key_a", "标签", 30, 1000.0)
        assert timer_storage.remove("a") is True
        assert timer_storage.remove("a") is False
        assert timer_storage.load_all() == []

    def test_clear_all(self, timer_storage):
        timer_storage.save("a", "key_a", "标签", 30, 1000.0)
        timer_storage.clear_all()
        assert timer_storage.load_all() == []

    def test_shutdown_time_roundtrip(self, timer_storage):
        assert timer_storage.load_shutdown_time() is None
        timer_storage.save_shutdown_time()
        assert timer_storage.load_shutdown_time() is not None


class TestTimerRestore:
    def test_without_storage_is_noop(self, fake_ctx):
        TimerTool().restore_from_storage()
        assert fake_ctx.alarms == []

    def test_expired_timer_discarded(self, fake_ctx, timer_storage):
        timer_storage.save("old", "timer_old", "早就过了", 60, time.time() - 10)
        TimerTool(storage=timer_storage).restore_from_storage()
        assert timer_storage.load_all() == []
        assert fake_ctx.alarms == []

    def _mark_shutdown(self, timer_storage, hours_ago: float, right_now: float,
                       label: str = "吃药"):
        """写入"关机发生在 hours_ago 小时前"，并注册一个到点为 right_now 的 timer。"""
        shutdown = (datetime.now() - timedelta(hours=hours_ago)).isoformat()
        timer_storage.save("t1", "timer_t1", label, 60, right_now)
        timer_storage._conn.execute(
            "INSERT OR REPLACE INTO timer_meta (key, value) VALUES ('shutdown_time', ?)",
            (shutdown,),
        )
        timer_storage._conn.commit()

    def test_offline_elapsed_timer_fires_immediately(self, fake_ctx, timer_storage):
        # 关机发生在 1 小时前，timer 的到点时间落在离线区间内 → 恢复时补发提醒
        self._mark_shutdown(timer_storage, hours_ago=1, right_now=time.time() - 30)
        tool = TimerTool(storage=timer_storage)
        tool.restore_from_storage()

        assert fake_ctx.speeches and "吃药" in fake_ctx.speeches[0]
        assert fake_ctx.notices and fake_ctx.notices[0][1] == "吃药"
        assert fake_ctx.events[0][0] == "timer"
        assert fake_ctx.alarms == []
        assert timer_storage.load_all() == []
        assert tool.list_timers()["count"] == 0

    def test_offline_pending_timer_reregisters_alarm(self, fake_ctx, timer_storage):
        # 关机发生在 1 小时前，但 timer 尚未到点 → 仍应注册闹钟，不得立即触发
        self._mark_shutdown(timer_storage, hours_ago=1, right_now=time.time() + 300,
                            label="喝水")
        tool = TimerTool(storage=timer_storage)
        tool.restore_from_storage()

        assert fake_ctx.alarms, "未到点的 timer 不应被立即触发"
        assert fake_ctx.alarms[0][2] == "timer_t1"
        assert fake_ctx.speeches == []
        assert tool.list_timers()["count"] == 1

    def test_pending_timer_reregisters_alarm(self, fake_ctx, timer_storage):
        timer_storage.save("t2", "timer_t2", "喝水", 600, time.time() + 300)
        tool = TimerTool(storage=timer_storage)
        tool.restore_from_storage()
        assert len(fake_ctx.alarms) == 1
        assert fake_ctx.alarms[0][2] == "timer_t2"
        assert tool.list_timers()["count"] == 1

    def test_register_failure_cleans_up(self, fake_ctx, timer_storage, monkeypatch):
        def boom(*_args, **_kwargs):
            raise RuntimeError("scheduler unavailable")

        monkeypatch.setattr(fake_ctx, "register_alarm", boom)
        timer_storage.save("t3", "timer_t3", "失败", 600, time.time() + 300)
        tool = TimerTool(storage=timer_storage)
        tool.restore_from_storage()
        assert tool.list_timers()["count"] == 0
        assert timer_storage.load_all() == []


class TestSetTimer:
    @pytest.mark.parametrize("duration", [0, -5, 86401])
    def test_invalid_duration_rejected(self, fake_ctx, timer_storage, duration):
        result = TimerTool(storage=timer_storage).set_timer(duration, "无效")
        assert "error" in result
        assert timer_storage.load_all() == []

    def test_valid_timer_persisted_and_registered(self, fake_ctx, timer_storage):
        tool = TimerTool(storage=timer_storage)
        result = tool.set_timer(90, "泡茶")
        assert result["label"] == "泡茶"
        assert TimerTool._format_duration(90) in result["summary"]
        assert len(timer_storage.load_all()) == 1
        assert len(fake_ctx.alarms) == 1

    def test_register_failure_rolls_back(self, fake_ctx, timer_storage, monkeypatch):
        def boom(*_args, **_kwargs):
            raise RuntimeError("no scheduler")

        monkeypatch.setattr(fake_ctx, "register_alarm", boom)
        tool = TimerTool(storage=timer_storage)
        assert "error" in tool.set_timer(60, "回滚")
        assert tool.list_timers()["count"] == 0
        assert timer_storage.load_all() == []

    def test_cancel_removes_from_storage(self, fake_ctx, timer_storage):
        tool = TimerTool(storage=timer_storage)
        timer_id = tool.set_timer(60, "取消我")["id"]
        tool.cancel_timer(timer_id)
        assert timer_storage.load_all() == []

    def test_cancel_unknown_id_reports_error(self, fake_ctx, timer_storage):
        assert "error" in TimerTool(storage=timer_storage).cancel_timer("nope")


class TestTodoStorage:
    @pytest.fixture
    def storage(self, case_db_path) -> TodoStorage:
        return TodoStorage(db_path=str(case_db_path))

    def test_add_trims_title(self, storage):
        item = storage.add("  写测试  ")
        assert item["title"] == "写测试"
        assert item["status"] == "pending"

    def test_list_filters_by_status(self, storage):
        done_id = storage.add("已完成")["id"]
        storage.add("待办")
        storage.toggle(done_id)
        assert [t["title"] for t in storage.list("done")] == ["已完成"]
        assert [t["title"] for t in storage.list("pending")] == ["待办"]
        assert len(storage.list()) == 2

    def test_toggle_roundtrip(self, storage):
        todo_id = storage.add("切换")["id"]
        assert storage.toggle(todo_id)["status"] == "done"
        assert storage.toggle(todo_id)["status"] == "pending"

    def test_toggle_unknown_returns_none(self, storage):
        assert storage.toggle(9999) is None

    def test_update_rejects_blank_title(self, storage):
        todo_id = storage.add("原标题")["id"]
        assert storage.update(todo_id, "   ") is None
        assert storage.update(todo_id, "新标题")["title"] == "新标题"

    def test_delete_reports_success(self, storage):
        todo_id = storage.add("删除我")["id"]
        assert storage.delete(todo_id) is True
        assert storage.delete(todo_id) is False


class TestConversationStore:
    @pytest.fixture
    def store(self, case_db_path) -> ConversationStore:
        return ConversationStore(db_path=str(case_db_path))

    def test_blank_content_skipped(self, store):
        store.add("user", "")
        assert store.get_available_dates() == []

    def test_query_by_date_ascending(self, store):
        store.add("user", "第一句")
        store.add("assistant", "第二句")
        today = datetime.now().strftime("%Y-%m-%d")
        assert [r["content"] for r in store.query_by_date(today)] == ["第一句", "第二句"]

    def test_available_dates_descending(self, store):
        store.add("user", "今天")
        old_day = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        store._conn.execute(
            "INSERT INTO chat_history (role, content, created_at) VALUES (?,?,?)",
            ("user", "三天前", f"{old_day}T10:00:00"),
        )
        store._conn.commit()
        dates = store.get_available_dates()
        assert dates == sorted(dates, reverse=True)
        assert len(dates) == 2

    def test_cleanup_removes_records_older_than_window(self, store):
        old_day = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        store._conn.execute(
            "INSERT INTO chat_history (role, content, created_at) VALUES (?,?,?)",
            ("user", "十天前", f"{old_day}T10:00:00"),
        )
        store._conn.commit()
        store._cleanup_old(7)
        assert store.query_by_date(old_day) == []

    def test_cleanup_keeps_recent_records(self, store):
        store.add("user", "今天的")
        store._cleanup_old(7)
        today = datetime.now().strftime("%Y-%m-%d")
        assert len(store.query_by_date(today)) == 1
