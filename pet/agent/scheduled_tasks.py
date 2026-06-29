"""定时任务注册与回调"""

import logging
from datetime import datetime

from pet.agent.state import PetState
from pet.config import config

logger = logging.getLogger(__name__)


class ScheduledTasks:
    """管理 Scheduler 上的定时任务注册与回调实现。"""

    def __init__(self, agent):
        self._agent = agent
        self._sleep_tick: int = 0
        self._dark_heart_tick: int = 0
        self._star_tick: int = 0
        self._heart_tick: int = 0
        self._note_tick: int = 0

    # ── 注册 ──

    def register_all(self, scheduler):
        scheduler.register("mid", self._autonomous)
        scheduler.register("fast", self._recover)
        scheduler.register("fast", self._update_idle_anim)
        scheduler.register("fast", self._spawn_particles)
        scheduler.register("slow", self._wakeup)
        scheduler.register("slow", self._agent.vitals.reduce)
        scheduler.register("slow", self._agent.vitals.save)
        scheduler.register("slow", self._agent.vitals.check_thresholds)
        scheduler.register("slow", self._agent.mood.save)
        scheduler.register("slow", self._agent.mood.check_thresholds)
        scheduler.register("slow", self._memory_maintenance)
        scheduler.register("slow", self._conversation_cleanup)

    # ── mid ──

    def _autonomous(self):
        ts = datetime.now().strftime("%H:%M:%S")
        if not self._agent.state_machine.try_transition(PetState.AUTONOMOUS):
            logger.info(f"[{ts}] [PetAgent] [mid_tick] skipped (state={self._agent.state_machine.state.value})")
            return

        pet_x, pet_y = 0, 0
        win = self._agent._pet_window
        if win:
            pet_x = win.x()
            pet_y = win.y()
        self._agent._async_brain(self._agent._autonomous_pipeline, pet_x, pet_y)

    # ── fast ──

    def _recover(self):
        """sit/sleep 期间每秒 +0.1 精力，sleep 每 3s 触发 zzz 粒子。"""
        win = self._agent._pet_window
        if not win:
            return
        cur = win.action_queue.current_action_name()
        if cur == "sleep":
            self._agent.vitals.modify_energy(0.1)
            self._sleep_tick += 1
            if self._sleep_tick % 3 == 0:
                win.particles.spawn("zzz")
        elif cur == "sit":
            self._agent.vitals.modify_energy(0.1)
            self._sleep_tick = 0

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
        cur = win.pet_anim.current_action
        if sanity < config.SANITY_CRITICAL_THRESHOLD and cur == "idle":
            win.pet_anim.play("grim")
        elif sanity >= config.SANITY_CRITICAL_THRESHOLD and cur == "grim":
            win.pet_anim.play("idle")

    def _spawn_particles(self):
        """fast_tick 定期粒子特效：
        - 低理智 → dark_hearts（每 2 tick）
        - shake_arms 播放中 → stars（每 2 tick）
        """
        win = self._agent._pet_window
        if not win:
            return
        ms = self._agent.mood.numeric_summary()
        sanity = ms.get("sanity", 100)

        # dark_hearts: 低理智时散发
        if sanity < config.SANITY_CRITICAL_THRESHOLD:
            self._dark_heart_tick += 1
            if self._dark_heart_tick % 2 == 0:
                win.particles.spawn("dark_hearts")
        else:
            self._dark_heart_tick = 0

        # stars: shake_arms和rotate 播放中散发
        cur = win.action_queue.current_action_name()
        if cur == "shake_arms" or cur == "rotate":
            self._star_tick += 1
            if self._star_tick % 2 == 0:
                win.particles.spawn("stars")
        else:
            self._star_tick = 0

        # hearts: finger_heart 播放中散发
        if cur == "finger_heart":
            self._heart_tick += 1
            if self._heart_tick % 2 == 0:
                win.particles.spawn("hearts")
        else:
            self._heart_tick = 0

        # notes: calling 播放中散发
        if cur == "calling":
            self._note_tick += 1
            if self._note_tick % 2 == 0:
                win.particles.spawn("notes")
        else:
            self._note_tick = 0

    # ── slow ──

    def _wakeup(self):
        """定期唤醒：sleeping → idle，并 stretch。"""
        ts = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{ts}] [PetAgent] [slow_tick]")
        sm = self._agent.state_machine
        if sm.state == PetState.SLEEPING:
            sm.transition(PetState.IDLE)
            logger.info(f"[{ts}] [PetAgent] slow_tick: woke up, emitting stretch")
            self._agent._emit_action("stretch", (), {})

    def _memory_maintenance(self):
        """定期维护记忆：L3 硬清理 + 容量控制。"""
        try:
            self._agent.behavior._memory_store.maintenance()
        except Exception as e:
            logger.debug(f"[ScheduledTasks] memory maintenance skipped: {e}")

    def _conversation_cleanup(self):
        """定期清理过期对话历史记录（保持至多 7 天）。"""
        try:
            self._agent.conversation_store._cleanup_old(7)
        except Exception as e:
            logger.debug(f"[ScheduledTasks] conversation cleanup skipped: {e}")
