"""定时器核心 — 基于 Scheduler 的倒计时提醒。"""

import logging
import time
import uuid

from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)


class TimerTool:
    """倒计时定时器，到时间后主动说话 + 弹通知。"""

    def __init__(self):
        self._timers: dict[str, dict] = {}  # timer_id → {key, label, duration_s, fire_at}

    # ── 公开方法 ──

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
            msg = f"叮叮！「{label}」"
            TOOL_CTX.speech(msg, duration=4000)
            TOOL_CTX.notify("⏰ 定时器", label)
            self._timers.pop(timer_id, None)
            logger.info(f"[Timer] fired: {timer_id} '{label}'")

        TOOL_CTX.register_alarm(int(fire_at * 1000), _on_fire, key=key)

        self._timers[timer_id] = {
            "key": key, "label": label, "duration_s": duration, "fire_at": fire_at,
        }
        logger.info(f"[Timer] set: {timer_id} '{label}' {duration}s")
        return {
            "id": timer_id,
            "label": label,
            "duration": duration,
            "summary": f"已设定「{label}」，{duration} 秒后提醒",
        }

    def list_timers(self) -> dict:
        """列出所有活跃定时器。"""
        if not self._timers:
            return {"summary": "当前没有活跃的定时器", "timers": [], "count": 0}

        now_s = time.time()
        items = []
        for tid, t in self._timers.items():
            remain = max(0, int(t["fire_at"] - now_s))
            items.append({"id": tid, "label": t["label"], "remaining_s": remain})

        lines = [f"共 {len(items)} 个活跃定时器:"]
        for item in items:
            lines.append(f"  [{item['id']}] {item['label']} — 剩余 {item['remaining_s']} 秒")
        return {"summary": "\n".join(lines), "timers": items, "count": len(items)}

    def cancel_timer(self, timer_id: str) -> dict:
        """取消指定定时器。"""
        timer = self._timers.pop(timer_id, None)
        if timer is None:
            return {"error": f"未找到定时器 {timer_id}"}

        try:
            agent = getattr(TOOL_CTX, '_agent', None)
            if agent and hasattr(agent, 'scheduler'):
                agent.scheduler._cleanup_alarm_timer(timer["key"])
        except Exception as e:
            logger.warning(f"[Timer] cleanup failed for {timer_id}: {e}")

        logger.info(f"[Timer] cancelled: {timer_id} '{timer['label']}'")
        return {"cancelled": timer_id, "label": timer["label"],
                "summary": f"已取消定时器「{timer['label']}」"}

    def close(self):
        for tid in list(self._timers.keys()):
            self.cancel_timer(tid)
