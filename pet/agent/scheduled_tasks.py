"""定时任务注册与回调"""

import logging
import threading
import time
from datetime import datetime, timedelta

from pet.agent.state import PetState
from pet.brain.memory_merge import run_merge
from pet.config import config

logger = logging.getLogger(__name__)

# 增量触发阈值：新增 event 记忆达到该条数才发起整点汇总
_MERGE_MIN_NEW_EVENTS = 6

# 动作名称 → (粒子名, 触发间隔：fast tick 数)
_ACTION_PARTICLES: dict[str, tuple[str, int]] = {
    "shake_arms":   ("stars", 2),
    "rotate":       ("stars", 2),
    "finger_heart": ("hearts", 2),
    "shy":          ("hearts", 2),
    "hug":          ("hearts", 2),
    "confuse":      ("question_marks", 2),
    "calling":      ("notes", 2),
    "sleep":        ("zzz", 3),
    "bathing":      ("bubbles", 3),
    "dejected":     ("spiral", 2),
}


class ScheduledTasks:
    """管理 Scheduler 上的定时任务注册与回调实现。"""

    def __init__(self, agent):
        self._agent = agent
        self._dark_heart_tick: int = 0
        self._particle_ticks: dict[str, int] = {}  # 粒子名 → 累计 tick
        self._idle_since: float | None = None      # 连续 idle 的起点（monotonic），非 idle 时置 None
        self._scheduler = None
        self._last_merge_at: str = ""              # 上次汇总后的 event marker（created_at#id）


    def register_all(self, scheduler):
        self._scheduler = scheduler
        self._init_merge_marker()
        scheduler.register("mid", self._autonomous)
        scheduler.register("fast", self._brain_watchdog)
        scheduler.register("fast", self._vitals_tick)
        scheduler.register("fast", self._update_idle_anim)
        scheduler.register("fast", self._unconsciousness)
        scheduler.register("fast", self._spawn_particles)
        scheduler.register("slow", self._vitals_save)
        scheduler.register("slow", self._vitals_check)
        scheduler.register("slow", self._mood_save)
        scheduler.register("slow", self._mood_check)
        scheduler.register("slow", self._memory_maintenance)
        scheduler.register("slow", self._conversation_cleanup)
        self._schedule_next_merge()


    def _autonomous(self):
        ts = datetime.now().strftime("%H:%M:%S")
        if not self._agent.state_machine.try_transition(PetState.AUTONOMOUS):
            logger.info(f"[{ts}] [PetAgent] [mid_tick] skipped (state={self._agent.state_machine.state.value})")
            return
        # 上个脑线程仍未退出时不再叠加新线程（挂死由看门狗处理），避免线程堆积
        if self._agent._thread is not None and self._agent._thread.isRunning():
            logger.info(f"[{ts}] [PetAgent] [mid_tick] skipped (brain thread still running)")
            self._agent.state_machine.transition(PetState.IDLE)
            return

        pet_x, pet_y = 0, 0
        win = self._agent._pet_window
        if win:
            pet_x = win.x()
            pet_y = win.y()
        snap = self._agent.window_snapshot()  # 回调由 QTimer 在主线程触发，在此取快照
        self._agent._async_brain(self._agent._autonomous_pipeline, pet_x, pet_y, snap)

    def _brain_watchdog(self):
        """检测脑线程占用状态挂死（autonomous/interacting 无进展超时）并强制恢复"""
        agent = self._agent
        state = agent.state_machine.state
        if state not in (PetState.AUTONOMOUS, PetState.INTERACTING):
            return
        if agent.is_game_active():
            agent.note_brain_progress()
            return
        idle = agent.brain_idle_seconds()
        if idle is None:
            return
        threshold = config.BRAIN_STUCK_TIMEOUT
        if idle > threshold:
            ts = datetime.now().strftime("%H:%M:%S")
            logger.warning(
                f"[{ts}] [PetAgent] [watchdog] state '{state.value}' no progress for {idle:.0f}s "
                f"(> {threshold}s), forcing back to IDLE"
            )
            agent.recover_stuck_brain()


    def _vitals_tick(self):
        """每秒将当前动作名传给 vitals 做数值调整。"""
        win = self._agent._pet_window
        if not win:
            return
        cur = win.action_queue.current_action_name()
        if cur is not None:
            self._agent.vitals.apply_action_delta(cur)

    def _update_idle_anim(self):
        """理智 < 20 → grim，否则 → idle，仅在无队列动作且不处于下落时切换。"""
        win = self._agent._pet_window
        if not win:
            return
        if win.action_queue.current_action_name() is not None:
            return
        if win.pet_actions.gravity.falling:
            return
        ms = self._agent.mood.numeric_summary()
        sanity = ms.get("sanity", 100)
        idle_anim = win.pet_actions.idle_animation
        grim_anim = win.pet_actions.grim_animation
        cur = win.pet_anim.current_action
        if sanity < config.SANITY_CRITICAL_THRESHOLD and cur == idle_anim:
            win.pet_anim.play(grim_anim)
        elif sanity >= config.SANITY_CRITICAL_THRESHOLD and cur == grim_anim:
            win.pet_anim.play(idle_anim)

    def _unconsciousness(self):
        """连续 idle 超过 UNCONSCIOUS_IDLE_SECONDS 秒 → 播放无意识化动作"""
        win = self._agent._pet_window
        if not win:
            return
        if win.is_docked:
            self._idle_since = None
            return
        if (win.action_queue.current_action_name() is not None
                or win.pet_actions.gravity.falling
                or win.pet_anim.current_action != "idle"
                or self._agent.is_llm_loading):
            self._idle_since = None
            return
        now = time.monotonic()
        if self._idle_since is None:
            self._idle_since = now
            return
        if now - self._idle_since >= config.UNCONSCIOUS_IDLE_SECONDS:
            self._idle_since = None
            logger.info("[ScheduledTasks] idle 超时，播放 unconsciousness")
            win.pet_actions.unconsciousness()

    def _spawn_particles(self):
        """fast_tick 定期粒子特效："""
        win = self._agent._pet_window
        if not win:
            return

        # dark_hearts: 低理智时散发
        sanity = self._agent.mood.numeric_summary().get("sanity", 100)
        if sanity < config.SANITY_CRITICAL_THRESHOLD:
            self._dark_heart_tick += 1
            if self._dark_heart_tick % 2 == 0:
                win.particles.spawn("dark_hearts")
        else:
            self._dark_heart_tick = 0

        # 动作查表触发粒子
        cur = win.action_queue.current_action_name()
        for action, (particle, interval) in _ACTION_PARTICLES.items():
            if cur == action:
                self._particle_ticks[action] = self._particle_ticks.get(action, 0) + 1
                if self._particle_ticks[action] % interval == 0:
                    win.particles.spawn(particle)
            else:
                self._particle_ticks[action] = 0


    def _vitals_save(self):
        self._agent.vitals.save()

    def _vitals_check(self):
        self._agent.vitals.check_thresholds()

    def _mood_save(self):
        self._agent.mood.save()

    def _mood_check(self):
        self._agent.mood.apply_decay()   # 自然衰减（内部联动阈值检查）
        self._agent.mood.check_thresholds()

    def _memory_maintenance(self):
        """定期维护记忆：L3 硬清理 + 容量控制。"""
        try:
            self._agent.memory_store.maintenance()
        except Exception as e:
            logger.warning(f"[ScheduledTasks] memory maintenance failed: {e}")

    def _init_merge_marker(self):
        """重启后以库中最新的 marker 初始化游标，第一个整点不重复汇总已有条目。"""
        try:
            latest = self._agent.memory_store.latest_event_marker()
        except Exception as e:
            logger.warning(f"[ScheduledTasks] merge marker init failed: {e}")
            return
        if latest:
            self._last_merge_at = latest

    def _schedule_next_merge(self):
        """注册下一个整点的汇总闹钟，重复注册覆盖旧闹钟。"""
        if self._scheduler is None:
            return
        now = datetime.now()
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        self._scheduler.schedule_at(
            int(next_hour.timestamp() * 1000),
            self._memory_event_merge,
            key="memory_event_merge",
        )

    def _memory_event_merge(self):
        """整点回调：先续约下一次，再按新增条数判定是否发起汇总。"""
        self._schedule_next_merge()
        if not config.MEMORY_EVENT_MERGE_ENABLED:
            logger.debug("[MemoryMerge] 整点跳过：汇总开关关闭")
            return
        try:
            pending, latest = self._agent.memory_store.pending_events(self._last_merge_at)
        except Exception as e:
            logger.warning(f"[ScheduledTasks] merge precheck failed: {e}")
            return
        if pending < _MERGE_MIN_NEW_EVENTS:
            logger.debug(
                f"[MemoryMerge] 整点跳过：新增 {pending} 条，未达 {_MERGE_MIN_NEW_EVENTS} 条")
            return
        logger.info(
            f"[MemoryMerge] 整点触发：新增 {pending} 条，上次 {self._last_merge_at or '无'}")
        threading.Thread(target=self._run_merge, args=(latest or "",), daemon=True).start()

    def _run_merge(self, latest: str = ""):
        """后台执行汇总；成功后推进游标，失败保留旧值，下一个整点重试同一批。"""
        try:
            run_merge(self._agent.memory_store)
        except Exception as e:
            logger.warning(f"[ScheduledTasks] memory merge failed: {e}")
            return
        self._last_merge_at = latest or self._last_merge_at
        self._refresh_merge_marker()

    def _refresh_merge_marker(self):
        """汇总产物 created_at 回填组内最大值、id 更大，不刷新会被算作下一次的新增。"""
        try:
            latest = self._agent.memory_store.latest_event_marker()
        except Exception as e:
            logger.warning(f"[ScheduledTasks] merge marker refresh failed: {e}")
            return
        if not latest:
            return
        # created_at 前进说明汇总期间写入了真实新事件，留给下次统计
        if latest.split("#", 1)[0] <= self._last_merge_at.split("#", 1)[0]:
            self._last_merge_at = latest

    def _conversation_cleanup(self):
        """定期清理过期对话历史记录（保持至多 7 天）。"""
        try:
            self._agent.conversation_store._cleanup_old(7)
        except Exception as e:
            logger.debug(f"[ScheduledTasks] conversation cleanup skipped: {e}")
