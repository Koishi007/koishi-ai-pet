"""PetAgent — 编排 Brain，通过 Signal 驱动 UI。"""

import logging
import threading
import time
from datetime import datetime
from PySide6.QtCore import QObject, QThread, QThreadPool, QTimer, Signal

from pet.brain.behavior import Behavior, BehaviorOutput
from pet.agent.scheduler import Scheduler
from pet.agent.scheduled_tasks import ScheduledTasks
from pet.agent.state import StateMachine
from pet.agent.screen_reader import ScreenReader
from pet.brain.memory import MemoryStore
from pet.brain.conversation_store import ConversationStore
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
    game_board_requested = Signal(str, object)  # (game_name, board_payload)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_head_pat_ts: float = 0.0  # 最近一次用户摸头时间（monotonic）
        self.memory_store = MemoryStore()
        self.conversation_store = ConversationStore()
        self.screen_reader = ScreenReader()
        self.screen_reader.enable()
        self.vitals = Vitals(parent=self)
        self.mood = Mood(parent=self)
        self.behavior = Behavior(memory_store=self.memory_store, screen_reader=self.screen_reader, vitals=self.vitals, mood=self.mood, head_pat_ts_fn=self._head_pat_ts)
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

    def note_head_pat(self):
        """记录一次用户摸头（单击宠物），供上下文备注注入。"""
        self._last_head_pat_ts = time.monotonic()

    def _head_pat_ts(self) -> float:
        return self._last_head_pat_ts

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
        # 唤醒等待中的游戏会话，避免脑线程卡在等待用户落子导致退出挂起
        try:
            from pet.game.gamebase import GAME
            GAME.cancel_all()
        except Exception:
            pass
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
        if hasattr(self, 'conversation_store'):
            self.conversation_store.close()
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
        arg_list = list(args or ())
        if name in _DURATION_ACTION_DEFS:
            if arg_list and isinstance(arg_list[0], int):
                kw["duration"] = arg_list.pop(0)
            else:
                kw["duration"] = default_duration(name)
            logger.debug(f"[PetAgent] duration for '{name}': {kw['duration']}s")
        self.action_requested.emit(name, tuple(arg_list), kw)
    def _autonomous_pipeline(self, pet_x=0, pet_y=0):
        self.behavior.note_autonomous_round()
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

        result = self.behavior.autonomous_decide_stream(context, screenshot=True, on_chunk=on_chunk, on_stream_end=on_stream_end, cancel_check=self._is_cancelled)

        if stream_started:
            self.speak_stream_end.emit(5000)
        return result

    def _play_loading(self, is_play_loading: bool = True):
        """is_play_loading 为 True 时清空动作队列并直接播放 thinking 动画。"""
        if not is_play_loading or not self._pet_window:
            return
        self._pet_window.action_queue.clear()
        anim_fn = getattr(self._pet_window.pet_actions, "thinking", None)
        if callable(anim_fn):
            anim_fn()

    def _trigger_interact(self, hint: str = "", delay_ms: int = 100,
                          cooldown_ms: int = 15000, record_context: bool = False,
                          context_hint: str = "", is_play_loading: bool = True,
                          thinking: bool | None = None,
                          enable_tools: bool | None = None):
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

            self.behavior.reset_user_interaction()

            self.speak_stream_end.emit(0)

            self.state_machine.transition(PetState.INTERACTING)

            self._play_loading(is_play_loading)

            self._async_brain(self._interact_pipeline, hint, record_context, context_hint, thinking, enable_tools)

        QTimer.singleShot(delay_ms, _execute)

    def _interact_pipeline(self, hint: str, record_context: bool = False,
                           context_hint: str = "", thinking: bool | None = None,
                           enable_tools: bool | None = None):
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
            thinking=thinking, enable_tools=enable_tools,
            cancel_check=self._is_cancelled,
        )

        if stream_started:
            self.speak_stream_end.emit(4000)
        return result

    def _trigger_chat(self, message: str = "", is_play_loading: bool = True,
                      thinking: bool | None = None,
                      enable_tools: bool | None = None):
        from pet.agent.state import PetState
        if self.state_machine.state == PetState.INTERACTING:
            logger.info("[PetAgent] chat request ignored (INTERACTING)")
            return

        self.behavior.reset_user_interaction()

        self.speak_stream_end.emit(0)

        self.state_machine.transition(PetState.INTERACTING)

        pet_x, pet_y = 0, 0
        if self._pet_window:
            pet_x = self._pet_window.x()
            pet_y = self._pet_window.y()

        self._play_loading(is_play_loading)

        self._async_brain(self._chat_pipeline, message, pet_x, pet_y, thinking, enable_tools)
        logger.info(f"[PetAgent] user chat:{message}")
        try:
            self.conversation_store.add("user", message)
        except Exception:
            pass

    def _chat_pipeline(self, message: str, pet_x: int, pet_y: int,
                       thinking: bool | None = None,
                       enable_tools: bool | None = None):
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
                self.speak_stream_end.emit(4000)
                stream_started = False

        result = self.behavior.chat_decide_stream(
            message, context, screenshot=True,
            on_chunk=on_chunk, on_stream_end=on_stream_end,
            thinking=thinking, enable_tools=enable_tools,
            cancel_check=self._is_cancelled,
        )

        if stream_started:
            self.speak_stream_end.emit(4000)
        return result

    def _is_cancelled(self) -> bool:
        """协作式取消检查：供 Behavior 流式循环轮询。"""
        return self._cancel_flag

    def _async_brain(self, fn, *args, on_result=None, on_error=None):
        fn_name = getattr(fn, "__name__", repr(fn))
        ts = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{ts}] [PetAgent] _async_brain: {fn_name}")
        old_thread = self._thread
        old_worker = self._worker
        if old_thread is not None and old_thread.isRunning():
            # 协作式取消：设置标志让旧线程在流式循环里快速退出，不阻塞主线程
            self._cancel_flag = True
            # 终结旧线程持有的游戏会话（可能阻塞在等待用户落子，远超 1s 等待窗口），
            # 避免新旧脑线程并发操作同一 session
            try:
                from pet.game.gamebase import GAME
                GAME.cancel_all()
            except Exception:
                pass
            old_thread.quit()
            # 给旧线程一个短等待窗口（最多 1s），超时不强杀，让其自然退出
            for _ in range(20):
                if not old_thread.isRunning():
                    break
                QThread.msleep(50)
            if old_thread.isRunning():
                logger.warning(f"[{ts}] [PetAgent] old brain thread still running after cancel, continue anyway")
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
            self.behavior.add_context(
                role="assistant",
                content=f"{result.speech or '(silent)'}")
            if result.speech and not result.speech_streamed:
                parts = result.speech_parts if result.speech_parts else [result.speech]
                for part in parts:
                    self.speak_requested.emit(part, 5000)
            if result.speech:
                try:
                    self.conversation_store.add("pet", result.speech)
                except Exception:
                    pass
            if result.summary:
                self.behavior.add_context(role="assistant", content=result.summary)
            for step in result.actions:
                self._emit_action(step.name, step.args, step.kwargs)
            if result.emotion:
                self.emotion_requested.emit(result.emotion, 3000)
        elif isinstance(result, str):
            logger.info(f"[{ts}] [PetAgent] ← \"{result[:60]}\"")
            self.behavior.add_context(role="assistant", content=result[:100])
            self.speak_requested.emit(result, 5000)
            try:
                self.conversation_store.add("pet", result)
            except Exception:
                pass

        if hasattr(result, 'memory_line') and result.memory_line:
            # 保存含 embedding 网络调用，放后台线程避免阻塞主线程 UI
            threading.Thread(
                target=self._save_memory_line,
                args=(result.memory_line,),
                daemon=True,
            ).start()
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
        self.behavior.reset_active_tool_groups()
        threading.Thread(target=self.behavior._flush_pending_summaries, daemon=True).start()

    def _save_memory_line(self, line: str):
        """后台线程保存记忆（含 embedding 网络调用）。"""
        try:
            self.memory_store.save_from_line(line)
        except Exception as e:
            logger.warning(f"[PetAgent] memory save failed: {e}")

    def _on_brain_error(self, msg: str):
        self._stop_loading()
        from pet.agent.state import PetState
        if self.state_machine.state in (PetState.INTERACTING, PetState.AUTONOMOUS):
            self.state_machine.transition(PetState.IDLE)
        logger.error(f"[PetAgent] ERROR: {msg}")
        self.behavior.reset_active_tool_groups()


