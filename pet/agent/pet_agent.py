"""PetAgent — 编排 Brain，通过 Signal 驱动 UI。"""

import logging
from datetime import datetime
from PySide6.QtCore import QObject, QThread, QThreadPool, QTimer, Signal

from pet.brain.behavior import Behavior, BehaviorOutput
from pet.brain.view import View
from pet.agent.scheduler import Scheduler
from pet.agent.state import StateMachine
from pet.agent.screen_reader import ScreenReader
from pet.agent.memory_store import MemoryStore
from pet.action.registry import DEFAULT_ACTION_DURATIONS
from config import config

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
    state_changed    = Signal(str)
    view_ready       = Signal(str)
    view_error       = Signal(str)
    speak_stream_start = Signal()
    speak_stream_chunk = Signal(str)
    speak_stream_end   = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.memory_store = MemoryStore()
        self.view_brain = View()
        self.screen_reader = ScreenReader()
        self.screen_reader.enable()
        self.behavior = Behavior(memory_store=self.memory_store, screen_reader=self.screen_reader)
        self.scheduler = Scheduler(self)
        self.state_machine = StateMachine()
        self._pet_window = None

        self.scheduler.mid_tick.connect(self._on_mid_tick)
        self.scheduler.fast_tick.connect(self._on_fast_tick)
        self.scheduler.slow_tick.connect(self._on_slow_tick)

        self._thread: QThread | None = None
        self._worker: BrainWorker | None = None
        self._cancel_flag = False
        self._active_stream_id = 0
        self._last_interact_ms: dict[str, int] = {}

    def set_pet_window(self, window):
        self._pet_window = window

    def start(self):
        if config.SCHEDULER_AUTO_START:
            self.scheduler.start()
            logger.info("[PetAgent] scheduler auto-started")
            self.trigger_once(5000)
        else:
            logger.info("[PetAgent] scheduler auto-start disabled (SCHEDULER_AUTO_START=false)")

    def trigger_once(self, delay_ms: int = 2000, stream: bool = True,
                      screenshot: bool = True):
        logger.info(f"[PetAgent] trigger_once in {delay_ms}ms (stream={stream}, screenshot={screenshot})")

        def _execute():
            from pet.agent.state import PetState
            if not self.state_machine.try_transition(PetState.TALKING):
                logger.info(f"[PetAgent] trigger_once skipped (state={self.state_machine.state.value})")
                return
            self.state_changed.emit(PetState.TALKING.value)

            pet_x, pet_y = (self._pet_window.x(), self._pet_window.y()) if self._pet_window else (0, 0)

            if stream:
                self._async_brain(self._decide_pipeline, pet_x, pet_y)
            else:
                def _non_stream(px, py):
                    wctx = self._build_window_context(px, py)
                    return self.behavior.decide(wctx or "", screenshot=screenshot)
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
        self.state_changed.emit(st.value)

    def _emit_action(self, name: str, args, kwargs):
        kw = dict(kwargs) if kwargs else {}
        if "duration" not in kw and name in DEFAULT_ACTION_DURATIONS:
            kw["duration"] = DEFAULT_ACTION_DURATIONS[name]
            logger.debug(f"[PetAgent] backfill default duration for '{name}': {kw['duration']}s")
        self.action_requested.emit(name, args or (), kw)

    def _on_mid_tick(self):
        ts = datetime.now().strftime("%H:%M:%S")
        from pet.agent.state import PetState
        if not self.state_machine.try_transition(PetState.TALKING):
            logger.info(f"[{ts}] [PetAgent] [mid_tick] skipped (state={self.state_machine.state.value})")
            return
        self.state_changed.emit(PetState.TALKING.value)

        pet_x, pet_y = 0, 0
        if self._pet_window:
            pet_x = self._pet_window.x()
            pet_y = self._pet_window.y()
        self._async_brain(self._decide_pipeline, pet_x, pet_y)

    def _on_fast_tick(self):
        pass

    def _on_slow_tick(self):
        from pet.agent.state import PetState
        ts = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{ts}] [PetAgent] [slow_tick]")
        if self.state_machine.state == PetState.SLEEPING:
            self.state_machine.transition(PetState.IDLE)
            self.state_changed.emit(PetState.IDLE.value)
            logger.info(f"[{ts}] [PetAgent] slow_tick: woke up, emitting stretch")
        self._emit_action("stretch", (), {})

    def _decide_pipeline(self, pet_x=0, pet_y=0):

        window_context = self._build_window_context(pet_x, pet_y)
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

        result = self.behavior.decide_stream(context, screenshot=True, on_chunk=on_chunk, on_stream_end=on_stream_end)

        if stream_started:
            self.speak_stream_end.emit(5000)
        return result

    def _build_window_context(self, pet_x: int, pet_y: int) -> str:
        try:
            from pet.brain.window_detector import get_visible_windows, is_window_occluded
            windows = get_visible_windows()
        except Exception:
            return ""

        pet_w, pet_h = 125, 125
        pet_hwnd = int(self._pet_window.winId()) if self._pet_window else 0
        lines = [f"=== 窗口探测（系统 API，坐标精确） ==="]
        lines.append(f"桌宠位置: 左{pet_x} 上{pet_y} (宽{pet_w} 高{pet_h})")

        valid = 0
        for win in windows:
            left, top, right, bottom = win["rect"]
            w, h = right - left, bottom - top
            title = win["title"].strip()
            if not title or len(title) > 50:
                continue
            if abs(left - pet_x) < 10 and abs(top - pet_y) < 10 and w == pet_w and h == pet_h:
                continue
            if w < 200 or h < 100:
                continue
            if is_window_occluded(win["hwnd"], threshold=0.8, skip_hwnd=pet_hwnd):
                continue

            dx_walk = (left + w // 2) - pet_x
            dy_top = top - (pet_y + pet_h)
            dy_bottom = bottom - pet_y

            direction = "右" if dx_walk > 0 else "左"
            dist = abs(dx_walk)
            jump_px = abs(dy_top)
            if jump_px <= 400:
                reachable = "可跳"
            elif jump_px <= 800:
                reachable = "勉强可跳"
            else:
                reachable = "禁止跳跃（距离过高）"

            valid += 1
            lines.append(
                f"{valid}. \"{title}\" ｜ "
                f"范围: 左{left} 上{top} 右{right} 下{bottom} (宽{w} 高{h}) ｜ "
                f"相对桌宠: {direction}走{dist}px, 上跳{jump_px}px 到窗口顶 "
                f"({reachable})"
            )

        if valid == 0:
            lines.append("未发现适合跳转的窗口。")

        return "\n".join(lines)

    def analyze_view(self, image, prompt: str = ""):
        self._async_brain(
            self.view_brain.analyze, image, prompt,
            on_result=self._on_view_result,
            on_error=self._on_view_error,
        )

    def _trigger_interact(self, hint: str = "", delay_ms: int = 100,
                          cooldown_ms: int = 15000):
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
            if (self.state_machine.state == PetState.INTERACTING
                    and self._thread and self._thread.isRunning()):
                self._last_interact_ms[hint] = last
                logger.info("[PetAgent] interact ignored, already processing")
                return

            if self._thread and self._thread.isRunning():
                self.speak_stream_end.emit(0)

            self.state_machine.transition(PetState.INTERACTING)
            self.state_changed.emit(PetState.INTERACTING.value)

            if self._pet_window:
                self._pet_window.action_queue.clear()

            self._async_brain(self._interact_pipeline, hint)

        QTimer.singleShot(delay_ms, _execute)

    def _interact_pipeline(self, hint: str):
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
        if (self.state_machine.state == PetState.INTERACTING
                and self._thread and self._thread.isRunning()):
            logger.info("[PetAgent] chat request ignored, already processing")
            return

        if self._thread and self._thread.isRunning():
            self.speak_stream_end.emit(0)

        self.state_machine.transition(PetState.INTERACTING)
        self.state_changed.emit(PetState.INTERACTING.value)

        pet_x, pet_y = 0, 0
        if self._pet_window:
            pet_x = self._pet_window.x()
            pet_y = self._pet_window.y()

        if self._pet_window:
            self._pet_window.action_queue.clear()
            self._pet_window.pet_actions.thinking()

        self._async_brain(self._chat_pipeline, message, pet_x, pet_y)

    def _chat_pipeline(self, message: str, pet_x: int, pet_y: int):
        ts = datetime.now().strftime("%H:%M:%S")
        self.behavior.add_context(f"[{ts}] [user] {message}")

        window_context = self._build_window_context(pet_x, pet_y)
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
        self._worker = BrainWorker(fn, *args)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(on_result or self._on_brain_result)
        self._worker.error.connect(on_error or self._on_brain_error)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _cleanup_thread(self):
        sender = self.sender()
        if self._thread is not None and self._thread is sender:  # 仅清理当前线程，忽略旧线程延迟信号
            self._thread.deleteLater()
            self._thread = None
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _on_brain_result(self, result):
        ts = datetime.now().strftime("%H:%M:%S")
        from pet.agent.state import PetState
        if self.state_machine.state in (PetState.INTERACTING, PetState.TALKING):
            self.state_machine.transition(PetState.IDLE)
            self.state_changed.emit(PetState.IDLE.value)

        if isinstance(result, BehaviorOutput):
            logger.info(f"[{ts}] [PetAgent] ← {result}")
            action_names = [a.name for a in result.actions]
            self.behavior.add_context(
                f"[{ts}] did {', '.join(action_names)}, said: {result.speech or '(silent)'}")
            if result.speech and not result.speech_streamed:
                self.speak_stream_start.emit()
                self.speak_stream_chunk.emit(result.speech)
                self.speak_stream_end.emit(4000)
            if result.summary:
                self.behavior.add_context(f"[{ts}] [summary] {result.summary}")
            for step in result.actions:
                self._emit_action(step.name, step.args, step.kwargs)
        elif isinstance(result, str):
            logger.info(f"[{ts}] [PetAgent] ← \"{result[:60]}\"")
            self.behavior.add_context(f"[{ts}] said: {result[:100]}")
            self.speak_requested.emit(result, 5000)

        if hasattr(result, 'memory_line') and result.memory_line:
            try:
                self.memory_store.save_from_line(result.memory_line)
            except Exception as e:
                logger.warning(f"[PetAgent] memory save failed: {e}")

    def _on_brain_error(self, msg: str):
        from pet.agent.state import PetState
        if self.state_machine.state in (PetState.INTERACTING, PetState.TALKING):
            self.state_machine.transition(PetState.IDLE)
            self.state_changed.emit(PetState.IDLE.value)
        logger.error(f"[PetAgent] ERROR: {msg}")

    def _on_view_result(self, result):
        if isinstance(result, str):
            self.view_ready.emit(result)

    def _on_view_error(self, msg: str):
        self.view_error.emit(msg)


