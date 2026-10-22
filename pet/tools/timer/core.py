"""定时器核心 — 基于 Scheduler 的倒计时提醒，重启后可恢复。"""

import logging
import threading
import time
import uuid

from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)


class TimerTool:
    """倒计时定时器，到时间后主动说话 + 弹通知。支持持久化，重启后自动恢复。"""

    def __init__(self, storage=None):
        self._timers: dict[str, dict] = {}  # timer_id → {key, label, duration_s, fire_at}
        self._lock = threading.Lock()
        self._storage = storage


    def restore_from_storage(self):
        """启动时从数据库恢复未完成的定时器，重新注册闹钟。"""
        if not self._storage:
            return

        rows = self._storage.load_all()
        now_s = time.time()

        # 读取上次关机时间，计算离线时长
        shutdown_time = self._storage.load_shutdown_time()
        offline_seconds = 0.0
        if shutdown_time:
            from datetime import datetime
            try:
                offline_seconds = (datetime.now() - datetime.fromisoformat(shutdown_time)).total_seconds()
            except Exception:
                pass

        offline_fires = []
        with self._lock:
            for row in rows:
                fire_at = row["fire_at"]
                timer_id = row["id"]

                def _on_fire(tid=timer_id):
                    with self._lock:
                        timer = self._timers.pop(tid, None)
                    if timer:
                        if self._storage:
                            self._storage.remove(tid)
                        label = timer["label"]
                        msg = f"叮叮！「{label}」"
                        TOOL_CTX.speech(msg, duration=4000)
                        TOOL_CTX.notify("⏰ 定时器", label)
                        TOOL_CTX.note_event("timer", f"你设的「{label}」定时器响了")
                        logger.info(f"[Timer] fired (restored): {tid} '{label}'")

                self._timers[timer_id] = {
                    "key": row["key"], "label": row["label"],
                    "duration_s": row["duration_s"], "fire_at": fire_at,
                }

                if fire_at <= now_s:
                    if offline_seconds > 0:
                        # 到点时间落在离线区间内：锁内登记、锁外补发，
                        # 避免 _on_fire 重入 _lock 死锁
                        offline_fires.append(_on_fire)
                    else:
                        # 没有关机记录却已过期（数据陈旧）：丢弃，不打扰用户
                        logger.info(f"[Timer] expired while offline: {timer_id} '{row['label']}'")
                        self._timers.pop(timer_id, None)
                        self._storage.remove(timer_id)
                    continue

                try:
                    TOOL_CTX.register_alarm(round(fire_at * 1000), _on_fire, key=row["key"])
                except Exception:
                    logger.warning(f"[Timer] restore register failed: {row['id']}")
                    self._timers.pop(timer_id, None)
                    self._storage.remove(row["id"])

        for fire in offline_fires:
            fire()

        count = sum(1 for t in self._timers.values())
        if count > 0:
            logger.info(f"[Timer] restored {count} timer(s) from storage")


    def set_timer(self, duration: int, label: str = "时间到") -> dict:
        """设定一个倒计时定时器。"""
        if duration <= 0:
            return {"error": "时长必须大于 0 秒"}
        if duration > 86400:
            return {"error": "定时器上限 24 小时"}

        timer_id = uuid.uuid4().hex[:8]
        key = f"timer_{timer_id}"
        now_s = time.time()
        fire_at = now_s + duration

        def _on_fire():
            with self._lock:
                timer = self._timers.pop(timer_id, None)
            if timer:
                if self._storage:
                    self._storage.remove(timer_id)
                label_val = timer["label"]
                msg = f"叮叮！「{label_val}」"
                TOOL_CTX.speech(msg, duration=4000)
                TOOL_CTX.notify("⏰ 定时器", label_val)
                TOOL_CTX.note_event("timer", f"你设的「{label_val}」定时器响了")
                logger.info(f"[Timer] fired: {timer_id} '{label_val}'")

        with self._lock:
            self._timers[timer_id] = {
                "key": key, "label": label, "duration_s": duration, "fire_at": fire_at,
            }
        if self._storage:
            self._storage.save(timer_id, key, label, duration, fire_at)
        try:
            TOOL_CTX.register_alarm(round(fire_at * 1000), _on_fire, key=key)
        except Exception:
            with self._lock:
                self._timers.pop(timer_id, None)
            if self._storage:
                self._storage.remove(timer_id)
            return {"error": "定时器注册失败"}
        logger.info(f"[Timer] set: {timer_id} '{label}' {duration}s")
        return {
            "headline": f"已设定「{label}」，{self._format_duration(duration)}后提醒",
            "data": {"id": timer_id, "duration": duration},
        }

    def list_timers(self) -> dict:
        """列出所有活跃定时器。"""
        with self._lock:
            snapshot = list(self._timers.items())
        if not snapshot:
            return {"headline": "当前没有活跃的定时器"}

        now_s = time.time()
        items = []
        for tid, t in snapshot:
            remain = max(0, int(t["fire_at"] - now_s))
            items.append({
                "id": tid, "label": t["label"],
                "remaining_s": remain,
                "remaining_str": self._format_duration(remain),
            })

        lines = [
            f"[{item['id']}] {item['label']}，剩余 {item['remaining_str']}"
            for item in items
        ]
        return {
            "headline": f"共 {len(items)} 个活跃定时器",
            "body": "\n".join(lines),
        }

    def cancel_timer(self, timer_id: str) -> dict:
        """取消指定定时器。"""
        with self._lock:
            timer = self._timers.pop(timer_id, None)

        if timer is None:
            return {"error": f"未找到定时器 {timer_id}"}

        if self._storage:
            self._storage.remove(timer_id)

        cleaned = True
        try:
            TOOL_CTX.unregister_alarm(timer["key"])
        except Exception as e:
            logger.warning(f"[Timer] unregister failed for {timer_id}: {e}")
            cleaned = False

        logger.info(f"[Timer] cancelled: {timer_id} '{timer['label']}'")
        return {
            "headline": f"已取消定时器「{timer['label']}」"
            + ("" if cleaned else "（但提醒可能仍会触发）"),
            "data": {"id": timer_id, "alarm_cleared": cleaned},
        }

    def cancel_all(self) -> dict:
        """取消所有活跃定时器。"""
        with self._lock:
            ids = list(self._timers.keys())
        cancelled = 0
        for tid in ids:
            if self.cancel_timer(tid).get("headline"):
                cancelled += 1
        return {"headline": f"已取消 {cancelled}/{len(ids)} 个定时器"}

    def close(self):
        """关闭时保存关机时间，清除所有闹钟，关闭存储连接。"""
        if self._storage:
            self._storage.save_shutdown_time()
        with self._lock:
            ids = list(self._timers.keys())
        for tid in ids:
            try:
                TOOL_CTX.unregister_alarm(self._timers[tid]["key"])
            except Exception as e:
                logger.error(f"[Timer] close unregister failed for {tid}: {e}")
        if self._storage:
            self._storage.close()


    @staticmethod
    def _format_duration(seconds: int) -> str:
        if seconds < 60:
            return f"{seconds}秒"
        if seconds < 3600:
            m, s = divmod(seconds, 60)
            return f"{m}分{s}秒" if s else f"{m}分钟"
        h, r = divmod(seconds, 3600)
        m, s = divmod(r, 60)
        parts = [f"{h}小时"]
        if m:
            parts.append(f"{m}分")
        if s:
            parts.append(f"{s}秒")
        return "".join(parts)
