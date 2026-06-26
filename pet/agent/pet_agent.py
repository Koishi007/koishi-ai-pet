"""PetAgent — 编排 Brain，通过 Signal 驱动 UI。"""

import logging
from datetime import datetime
from PySide6.QtCore import QObject, QThread, QThreadPool, QTimer, Signal

from pet.brain.behavior import Behavior, BehaviorOutput
from pet.agent.scheduler import Scheduler
from pet.agent.scheduled_tasks import ScheduledTasks
from pet.agent.state import StateMachine
from pet.agent.screen_reader import ScreenReader
from pet.brain.memory import MemoryStore
from pet.action.registry import default_duration, _DURATION_ACTION_DEFS
from pet.pulse.vitals import Vitals
from pet.pulse.mood import Mood

from pet.config import config

logger = logging.getLogger(__name__)

class BrainWorker(QObject):

    finished = Signal(object)
    error    = Signal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self._fn = fn
        self._args = args
        self._name = getattr(fn, "__name__", repr(fn))

    def run(self):
        ts = datetime.now().strftime("%H:%M:%S")
        logger.debug(f"[{ts}] [BrainWorker] run: {self._name}({self._args})")
        try:
            result = self._fn(*self._args)
            logger.debug(f"[{ts}] [BrainWorker] done: {self._name} → {type(result).__name__}")
            self.finished.emit(result)
        except Exception as e:
            logger.error(f"[{ts}] [BrainWorker] ERROR: {self._name}: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))


class PetAgent(QObject):

    action_requested = Signal(str, object, object)
    speak_requested  = Signal(str, int)
    emotion_requested = Signal(str, int)
    state_changed    = Signal(str)
    speak_stream_start = Signal()
    speak_stream_chunk = Signal(str)
    speak_stream_end   = Signal(int)
    llm_loading        = Signal(bool)  # True=开始等待, False=结束
    notify_requested   = Signal(str, str, int)  # title, message, duration_ms

    def __init__(self, parent=None):
        super().__init__(parent)
        self.memory_store = MemoryStore()
        self.screen_reader = ScreenReader()
        self.screen_reader.enable()
        self.vitals = Vitals(parent=self)
        self.mood = Mood(parent=self)
        self.behavior = Behavior(memory_store=self.memory_store, screen_reader=self.screen_reader, vitals=self.vitals, mood=self.mood)
        self.scheduler = Scheduler(self)
        self.state_machine = StateMachine(parent=self)
        self.state_machine.state_changed.connect(self.state_changed)
        self._pet_window = None
        self._voice_session = None  # pet/app.py 设置

        self._tasks = ScheduledTasks(self)
        self._tasks.register_all(self.scheduler)

        self._thread: QThread | None = None
        self._worker: BrainWorker | None = None
        self._cancel_flag = False
        self._active_stream_id = 0
        self._last_interact_ms: dict[str, int] = {}

    def set_pet_window(self, window):
        self._pet_window = window

    def start(self):
        self.scheduler.init(
            auto_fast=config.SCHEDULER_AUTO_START_FAST,
            auto_mid=config.SCHEDULER_AUTO_START_MID,
            auto_slow=config.SCHEDULER_AUTO_START_SLOW,
        )
        if config.SCHEDULER_AUTO_START_MID:
            self.trigger_once(5000)

    def trigger_once(self, delay_ms: int = 2000, stream: bool = True,
                      screenshot: bool = True):
        logger.info(f"[PetAgent] trigger_once in {delay_ms}ms (stream={stream}, screenshot={screenshot})")

        def _execute():
            from pet.agent.state import PetState
            if not self.state_machine.try_transition(PetState.AUTONOMOUS):
                logger.info(f"[PetAgent] trigger_once skipped (state={self.state_machine.state.value})")
                return

            pet_x, pet_y = (self._pet_window.x(), self._pet_window.y()) if self._pet_window else (0, 0)

            if stream:
                self._async_brain(self._autonomous_pipeline, pet_x, pet_y)
            else:
                def _non_stream(px, py):
                    wctx = self.behavior.ctx.build_window_context(px, py, int(self._pet_window.winId()) if self._pet_window else 0)
                    return self.behavior.autonomous_decide(wctx or "", screenshot=screenshot)
                self._async_brain(_non_stream, pet_x, pet_y)

        QTimer.singleShot(delay_ms, _execute)

    def stop(self):
        self.scheduler.stop()
        self.screen_reader.disable()
        try:
            if self._thread and self._thread.isRunning():
                self._thread.quit()
                self._thread.wait(3000)
        except RuntimeError:
            pass
        if hasattr(self, 'memory_store'):
            self.memory_store.close()
        if hasattr(self, 'vitals'):
            self.vitals.close()
        if hasattr(self, 'mood'):
            self.mood.close()
        logger.info("[PetAgent] stopped")

    def trigger(self, intent: str, **kwargs):
        handlers = {
            "chat":     self._trigger_chat,
            "interact": self._trigger_interact,
        }
        handler = handlers.get(intent)
        if handler:
            handler(**kwargs)

    def force_state(self, state_name: str):
        from pet.agent.state import PetState
        try:
            st = PetState(state_name)
        except ValueError:
            return
        self.state_machine.force(st)

    def _emit_action(self, name: str, args, kwargs):
        kw = dict(kwargs) if kwargs else {}
        if "duration" not in kw and name in _DURATION_ACTION_DEFS:
            kw["duration"] = default_duration(name)
            logger.debug(f"[PetAgent] backfill default duration for '{name}': {kw['duration']}s")
        self.action_requested.emit(name, args or (), kw)
    def _autonomous_pipeline(self, pet_x=0, pet_y=0):
        window_context = self.behavior.ctx.build_window_context(pet_x, pet_y, int(self._pet_window.winId()) if self._pet_window else 0)
        context = window_context if window_context else ""

        stream_started = False
        self._active_stream_id += 1
        my_stream_id = self._active_stream_id

        def on_chunk(delta: str):
            nonlocal stream_started
            if self._cancel_flag or my_stream_id != self._active_stream_id:
                return
            if not stream_started:
                self.speak_stream_start.emit()
                stream_started = True
            self.speak_stream_chunk.emit(delta)

        def on_stream_end():
            nonlocal stream_started
            if self._cancel_flag or my_stream_id != self._active_stream_id:
                return
            if stream_started:
                self.speak_stream_end.emit(5000)
                stream_started = False

        result = self.behavior.autonomous_decide_stream(context, screenshot=True, on_chunk=on_chunk, on_stream_end=on_stream_end)

        if stream_started:
            self.speak_stream_end.emit(5000)
        return result

    def _trigger_interact(self, hint: str = "", delay_ms: int = 100,
                          cooldown_ms: int = 15000, record_context: bool = False,
                          context_hint: str = ""):
        if not hint:
            return
        from PySide6.QtCore import QDateTime
        now = QDateTime.currentMSecsSinceEpoch()
        last = self._last_interact_ms.get(hint, 0)
        if now - last < cooldown_ms:
            logger.info(f"[PetAgent] interact skipped (cooldown, {cooldown_ms - (now - last)}ms remaining)")
            return
        self._last_interact_ms[hint] = now  # 提前占位防同 hint 重复入队，_execute 去重失败时回滚

        def _execute():
            from pet.agent.state import PetState
            if self.state_machine.state == PetState.INTERACTING:
                self._last_interact_ms[hint] = last
                logger.info("[PetAgent] interact ignored (INTERACTING)")
                return

            self.speak_stream_end.emit(0)

            self.state_machine.transition(PetState.INTERACTING)

            if self._pet_window:
                self._pet_window.action_queue.clear()
                self._pet_window.pet_actions.thinking()

            self._async_brain(self._interact_pipeline, hint, record_context, context_hint)

        QTimer.singleShot(delay_ms, _execute)

    def _interact_pipeline(self, hint: str, record_context: bool = False,
                           context_hint: str = ""):
        if record_context:
            store_hint = context_hint if context_hint else hint
            self.behavior.add_context(role="user", content=store_hint)
        stream_started = False
        self._active_stream_id += 1
        my_stream_id = self._active_stream_id

        def on_chunk(delta: str):
            nonlocal stream_started
            if self._cancel_flag or my_stream_id != self._active_stream_id:
                return
            if not stream_started:
                self.speak_stream_start.emit()
                stream_started = True
            self.speak_stream_chunk.emit(delta)

        def on_stream_end():
            nonlocal stream_started
            if self._cancel_flag or my_stream_id != self._active_stream_id:
                return
            if stream_started:
                self.speak_stream_end.emit(4000)
                stream_started = False

        result = self.behavior.interact_decide_stream(
            hint, on_chunk=on_chunk, on_stream_end=on_stream_end,
        )

        if stream_started:
            self.speak_stream_end.emit(4000)
        return result

    def _trigger_chat(self, message: str = ""):
        from pet.agent.state import PetState
        if self.state_machine.state == PetState.INTERACTING:
            logger.info("[PetAgent] chat request ignored (INTERACTING)")
            return

        self.speak_stream_end.emit(0)

        self.state_machine.transition(PetState.INTERACTING)

        pet_x, pet_y = 0, 0
        if self._pet_window:
            pet_x = self._pet_window.x()
            pet_y = self._pet_window.y()

        if self._pet_window:
            self._pet_window.action_queue.clear()
            self._pet_window.pet_actions.thinking()

        self._async_brain(self._chat_pipeline, message, pet_x, pet_y)
        logger.info(f"[PetAgent] user chat:{message}")

    def _chat_pipeline(self, message: str, pet_x: int, pet_y: int):
        self.behavior.add_context(role="user", content=message)

        window_context = self.behavior.ctx.build_window_context(pet_x, pet_y, int(self._pet_window.winId()) if self._pet_window else 0)
        context = window_context if window_context else "当前无窗口信息"

        stream_started = False
        self._active_stream_id += 1
        my_stream_id = self._active_stream_id

        def on_chunk(delta: str):
            nonlocal stream_started
            if self._cancel_flag or my_stream_id != self._active_stream_id:
                return
            if not stream_started:
                self.speak_stream_start.emit()
                stream_started = True
            self.speak_stream_chunk.emit(delta)

        def on_stream_end():
            nonlocal stream_started
            if self._cancel_flag or my_stream_id != self._active_stream_id:
                return
            if stream_started:
                self.speak_stream_end.emit(8000)
                stream_started = False

        result = self.behavior.chat_decide_stream(
            message, context, screenshot=True,
            on_chunk=on_chunk, on_stream_end=on_stream_end,
        )

        if stream_started:
            self.speak_stream_end.emit(8000)
        return result

    def _async_brain(self, fn, *args, on_result=None, on_error=None):
        fn_name = getattr(fn, "__name__", repr(fn))
        ts = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{ts}] [PetAgent] _async_brain: {fn_name}")
        old_thread = self._thread
        old_worker = self._worker
        if old_thread is not None and old_thread.isRunning():
            self._cancel_flag = True
            try:
                old_thread.quit()
                if not old_thread.wait(2000):
                    logger.warning(f"[{ts}] [PetAgent] old brain thread timeout, force terminate")
                    old_thread.terminate()
                    old_thread.wait(500)
                    import threading
                    if hasattr(self, 'behavior') and hasattr(self.behavior, '_lock'):
                        self.behavior._lock = threading.RLock()  # terminate 后原锁可能随线程死锁，重建一把
                        logger.warning(f"[PetAgent] behavior._lock rebuilt after thread terminate")
            except RuntimeError:
                pass
        if old_thread is not None:
            try:
                old_thread.finished.disconnect()
            except (RuntimeError, TypeError):
                pass
            old_thread.deleteLater()
        if old_worker is not None:
            try:
                old_worker.finished.disconnect()
                old_worker.error.disconnect()
            except (RuntimeError, TypeError):
                pass
            old_worker.deleteLater()
        self._cancel_flag = False
        self.llm_loading.emit(True)  # 开始 LLM 加载粒子
        self._worker = BrainWorker(fn, *args)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(on_result or self._on_brain_result)
        self._worker.error.connect(on_error or self._on_brain_error)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _stop_loading(self):
        """停止 LLM 加载粒子（流式开始或调用结束时调用）。"""
        self.llm_loading.emit(False)

    def _cleanup_thread(self):
        sender = self.sender()
        if self._thread is not None and self._thread is sender:  # 仅清理当前线程，忽略旧线程延迟信号
            self._thread.deleteLater()
            self._thread = None
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _on_brain_result(self, result):
        self._stop_loading()
        ts = datetime.now().strftime("%H:%M:%S")
        from pet.agent.state import PetState
        if self.state_machine.state in (PetState.INTERACTING, PetState.AUTONOMOUS):
            self.state_machine.transition(PetState.IDLE)

        if isinstance(result, BehaviorOutput):
            logger.info(f"[{ts}] [PetAgent] ← {result}")
            if not result.actions and not result.speech:
                logger.warning(f"[{ts}] [PetAgent] empty response from LLM (no actions, no speech)")
            action_names = [a.name for a in result.actions]
            self.behavior.add_context(
                role="assistant",
                content=f"did {', '.join(action_names)}, said: {result.speech or '(silent)'}")
            if result.speech and not result.speech_streamed:
                self.speak_stream_start.emit()
                self.speak_stream_chunk.emit(result.speech)
                self.speak_stream_end.emit(4000)
            if result.summary:
                self.behavior.add_context(role="assistant", content=result.summary, is_summary=True)
            for step in result.actions:
                self._emit_action(step.name, step.args, step.kwargs)
            if result.emotion:
                self.emotion_requested.emit(result.emotion, 3000)
        elif isinstance(result, str):
            logger.info(f"[{ts}] [PetAgent] ← \"{result[:60]}\"")
            self.behavior.add_context(role="assistant", content=result[:100])
            self.speak_requested.emit(result, 5000)

        if hasattr(result, 'memory_line') and result.memory_line:
            try:
                self.memory_store.save_from_line(result.memory_line)
            except Exception as e:
                logger.warning(f"[PetAgent] memory save failed: {e}")
        if hasattr(result, 'mood_deltas') and result.mood_deltas:
            try:
                for key, delta in result.mood_deltas.items():
                    method = getattr(self.mood, f"modify_{key}", None)
                    if method:
                        method(delta)
            except Exception as e:
                logger.warning(f"[PetAgent] mood update failed: {e}")
        if hasattr(result, 'vitals_deltas') and result.vitals_deltas:
            try:
                for key, delta in result.vitals_deltas.items():
                    method = getattr(self.vitals, f"modify_{key}", None)
                    if method:
                        method(delta)
            except Exception as e:
                logger.warning(f"[PetAgent] vitals_deltas update failed: {e}")
        logger.info(f"[{ts}] [PetAgent] === call complete ===")
        
    def _on_brain_error(self, msg: str):
        self._stop_loading()
        from pet.agent.state import PetState
        if self.state_machine.state in (PetState.INTERACTING, PetState.AUTONOMOUS):
            self.state_machine.transition(PetState.IDLE)
        logger.error(f"[PetAgent] ERROR: {msg}")


