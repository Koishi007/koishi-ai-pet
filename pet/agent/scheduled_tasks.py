"""定时任务注册与回调"""

import logging
from datetime import datetime

from pet.agent.state import PetState
from pet.config import config

logger = logging.getLogger(__name__)

# 动作名称 → (粒子名, 触发间隔 tick)
_ACTION_PARTICLES: dict[str, tuple[str, int]] = {
    "shake_arms":   ("stars", 2),
    "rotate":       ("stars", 2),
    "finger_heart": ("hearts", 2),
    "calling":      ("notes", 2),
    "sleep":        ("zzz", 3),
    "bathing":      ("bubbles", 3),
}


class ScheduledTasks:
    """管理 Scheduler 上的定时任务注册与回调实现。"""

    def __init__(self, agent):
        self._agent = agent
        self._dark_heart_tick: int = 0
        self._particle_ticks: dict[str, int] = {}  # 粒子名 → 累计 tick


    def register_all(self, scheduler):
        scheduler.register("mid", self._autonomous)
        scheduler.register("fast", self._vitals_tick)
        scheduler.register("fast", self._update_idle_anim)
        scheduler.register("fast", self._spawn_particles)
        scheduler.register("slow", self._wakeup)
        scheduler.register("slow", self._vitals_save)
        scheduler.register("slow", self._vitals_check)
        scheduler.register("slow", self._mood_save)
        scheduler.register("slow", self._mood_check)
        scheduler.register("slow", self._memory_maintenance)
        scheduler.register("slow", self._conversation_cleanup)


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
        cur = win.pet_anim.current_action
        if sanity < config.SANITY_CRITICAL_THRESHOLD and cur == "idle":
            win.pet_anim.play("grim")
        elif sanity >= config.SANITY_CRITICAL_THRESHOLD and cur == "grim":
            win.pet_anim.play("idle")

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
        self._agent.mood.check_thresholds()

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
            self._agent.memory_store.maintenance()
        except Exception as e:
            logger.warning(f"[ScheduledTasks] memory maintenance failed: {e}")

    def _conversation_cleanup(self):
        """定期清理过期对话历史记录（保持至多 7 天）。"""
        try:
            self._agent.conversation_store._cleanup_old(7)
        except Exception as e:
            logger.debug(f"[ScheduledTasks] conversation cleanup skipped: {e}")
