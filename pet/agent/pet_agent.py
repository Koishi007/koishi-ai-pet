"""PetAgent — 编排 Brain，通过 Signal 驱动 UI。"""

import logging
import threading
import time
from datetime import datetime
from PySide6.QtCore import QObject, QThread, QThreadPool, QTimer, Signal

from pet.brain.behavior import Behavior
from pet.brain.output import BehaviorOutput
from pet.agent.scheduler import Scheduler
from pet.agent.scheduled_tasks import ScheduledTasks
from pet.agent.state import StateMachine, PetState
from pet.agent.screen_reader import ScreenReader
from pet.brain.memory import get_memory_store
from pet.brain.conversation_store import ConversationStore
from pet.action.registry import default_duration, has_duration
from pet.game.gamebase import GAME
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
    action_batch_started = Signal()  # 一轮动作即将入队（供「每轮只做一次」的判定重置状态）
    speak_requested  = Signal(str, int)
    emotion_requested = Signal(str, int)
    state_changed    = Signal(str)
    speak_stream_start = Signal()
    speak_stream_chunk = Signal(str)
    speak_stream_end   = Signal(int)
    llm_loading        = Signal(bool)  # True=开始等待, False=结束
    notify_requested   = Signal(str, str, int)  # title, message, duration_ms
    game_board_requested = Signal(str, object)  # (game_name, board_payload)
    tool_interact_requested = Signal(dict)  # 工具线程请求的即时交互（kwargs），转回主线程执行

    def __init__(self, parent=None):
        super().__init__(parent)
        self._recent_events: list[tuple[str, float, str]] = []  # 供上下文注入的最近事件（wall-clock 时间戳）
        self._once_events: list[tuple[str, float, str]] = []  # 只注入一轮的事件（如钓鱼收获），被消费后即消失
        self._brain_busy_since: float | None = None  # 进入脑线程占用状态（autonomous/interacting）的时刻（monotonic）
        self._llm_loading: bool = False  # LLM 交互进行中（与 llm_loading 信号同源，供内部查询）
        self._brain_progress_ts: float | None = None  # 最近一次管线进展的时刻（monotonic）
        self.memory_store = get_memory_store()
        self.conversation_store = ConversationStore()
        self.screen_reader = ScreenReader()
        self.screen_reader.enable()
        self.vitals = Vitals(parent=self)
        self.mood = Mood(parent=self)
        self.behavior = Behavior(memory_store=self.memory_store, screen_reader=self.screen_reader, vitals=self.vitals, mood=self.mood, recent_events_fn=self.recent_events, once_events_fn=self.take_once_events, progress_fn=self.note_brain_progress)
        self.scheduler = Scheduler(self)
        self.state_machine = StateMachine(parent=self)
        self.state_machine.state_changed.connect(self.state_changed)
        self._pet_window = None
        self.voice_session = None  # pet/app.py 装配时注入

        self._tasks = ScheduledTasks(self)
        self._tasks.register_all(self.scheduler)

        self._thread: QThread | None = None
        self._worker: BrainWorker | None = None
        self._retired: list[tuple[QThread, BrainWorker]] = []  # 被抢占且仍在运行的旧脑线程
        self._cancel_flag = False
        self._active_stream_id = 0
        self._last_interact_ms: dict[str, int] = {}
        self.state_machine.state_changed.connect(self._on_state_changed)
        self.tool_interact_requested.connect(self._trigger_tool_interact)

    _RECENT_EVENT_MAX = 16
    _ONCE_EVENT_MAX = 16  # 一次性事件积压上限（正常会在下一轮被消费掉，纯防御）

    def note_event(self, kind: str, text: str = ""):
        """记录一次事件，供上下文「最近发生了什么」注入。

        kind: 事件类型，展示层按它去重（同类型只留最近一次）；
        text: 展示文案，留空则由 ContextBuilder 查内置文案表。
        """
        self._recent_events.append((kind, time.time(), text))
        if len(self._recent_events) > self._RECENT_EVENT_MAX:
            del self._recent_events[: len(self._recent_events) - self._RECENT_EVENT_MAX]

    def recent_events(self) -> list[tuple[str, float, str]]:
        """返回最近事件快照（脑线程构造上下文时读取）。"""
        return list(self._recent_events)

    def note_once_event(self, kind: str, text: str = ""):
        """记录一个只注入一轮的事件，被上下文消费一次后即消失。

        text 留空则由 ContextBuilder 查内置文案表。
        """
        self._once_events.append((kind, time.time(), text))
        if len(self._once_events) > self._ONCE_EVENT_MAX:
            del self._once_events[: len(self._once_events) - self._ONCE_EVENT_MAX]

    def take_once_events(self) -> list[tuple[str, float, str]]:
        """取走全部待注入的一次性事件并清空（每轮构造上下文时调用一次）。"""
        events, self._once_events = self._once_events, []
        return events

    def note_head_pat(self):
        """记录一次用户摸头（单击宠物），供上下文注入。"""
        self.note_event("head_pat")

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
            if not self.state_machine.try_transition(PetState.AUTONOMOUS):
                logger.info(f"[PetAgent] trigger_once skipped (state={self.state_machine.state.value})")
                return

            pet_x, pet_y = (self._pet_window.x(), self._pet_window.y()) if self._pet_window else (0, 0)
            snap = self.window_snapshot()

            if stream:
                self._async_brain(self._autonomous_pipeline, pet_x, pet_y, snap)
            else:
                def _non_stream(px, py, snap):
                    wctx = self._window_context(px, py, snap)
                    return self.behavior.autonomous_decide(wctx or "", screenshot=screenshot)
                self._async_brain(_non_stream, pet_x, pet_y, snap)

        QTimer.singleShot(delay_ms, _execute)

    def stop(self):
        self.scheduler.stop()
        self.screen_reader.disable()
        # 唤醒等待中的游戏会话，避免脑线程卡在等待用户落子导致退出挂起
        try:
            GAME.cancel_all()
        except Exception:
            pass
        try:
            if self._thread and self._thread.isRunning():
                self._cancel_flag = True
                self._active_stream_id += 1  # 世代失效，令管线在下一个轮询点退出
                self._thread.quit()
                self._thread.wait(3000)
                if self._thread.isRunning():
                    # 不强等（阻塞期由各超时上界决定），退役持有引用，退出守卫兜底
                    logger.warning("[PetAgent] brain thread still running after cancel, retiring it")
                    self._retire(self._thread, self._worker)
                    self._thread = None
                    self._worker = None
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
            "analyze":  self._trigger_analyze,
            "interact": self._trigger_interact,
        }
        handler = handlers.get(intent)
        if handler:
            handler(**kwargs)

    def _emit_action(self, name: str, args, kwargs):
        kw = dict(kwargs) if kwargs else {}
        arg_list = list(args or ())
        if has_duration(name):
            if arg_list and isinstance(arg_list[0], int):
                kw["duration"] = arg_list.pop(0)
            else:
                kw["duration"] = default_duration(name)
            logger.debug(f"[PetAgent] duration for '{name}': {kw['duration']}s")
        self.action_requested.emit(name, tuple(arg_list), kw)

    def window_snapshot(self) -> tuple[int, float, int]:
        """在 GUI 线程取桌宠窗口快照：(句柄, 所在屏 DPR, 屏可用高度)。

        QWidget 只能在 GUI 线程访问，脑线程要用的是纯数值。
        """
        if not self._pet_window:
            return 0, 1.0, 1080
        hwnd = int(self._pet_window.winId())
        screen = self._pet_window.screen()
        if screen is None:
            return hwnd, 1.0, 1080
        return hwnd, screen.devicePixelRatio(), screen.availableGeometry().height()

    def _window_context(self, pet_x: int, pet_y: int, snap=None) -> str:
        """用主线程取好的快照生成窗口上下文。"""
        hwnd, dpr, screen_h = snap or (0, 1.0, 1080)
        return self.behavior.ctx.build_window_context(pet_x, pet_y, hwnd, dpr, screen_h)

    def _autonomous_pipeline(self, pet_x=0, pet_y=0, snap=None):
        self.behavior.note_autonomous_round()
        window_context = self._window_context(pet_x, pet_y, snap)
        context = window_context if window_context else ""

        stream_started = False
        self._active_stream_id += 1
        my_stream_id = self._active_stream_id

        def _is_stale() -> bool:
            # 新一轮管线启动即令旧管线过期：共享取消标志会被新管线复位，不能只依赖它
            return self._cancel_flag or my_stream_id != self._active_stream_id

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

        result = self.behavior.autonomous_decide_stream(context, screenshot=True, on_chunk=on_chunk, on_stream_end=on_stream_end, cancel_check=_is_stale)

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

    def _trigger_tool_interact(self, kwargs: dict):
        """工具线程经信号转来的即时交互请求：回主线程执行（QTimer 依赖主线程事件循环）。"""
        self.trigger("interact", **kwargs)

    def _trigger_interact(self, hint: str = "", delay_ms: int = 100,
                          cooldown_ms: int = 15000, record_context: bool = False,
                          context_hint: str = "", is_play_loading: bool = True,
                          thinking: bool | None = None,
                          enable_tools: bool | None = None,
                          attachment_text: str | None = None,
                          attachment_image=None):
        """cooldown_ms 为 0 表示不做冷却，同一 hint 也各触发一次（文件动作走这条）。"""
        if not hint:
            return
        from PySide6.QtCore import QDateTime
        now = QDateTime.currentMSecsSinceEpoch()
        last = self._last_interact_ms.get(hint, 0)
        if cooldown_ms > 0 and now - last < cooldown_ms:
            logger.info(f"[PetAgent] interact skipped (cooldown, {cooldown_ms - (now - last)}ms remaining)")
            return
        self._last_interact_ms[hint] = now  # 提前占位防同 hint 重复入队，_execute 去重失败时回滚

        def _execute():
            if self.state_machine.state == PetState.INTERACTING:
                self._last_interact_ms[hint] = last
                logger.info("[PetAgent] interact ignored (INTERACTING)")
                return

            self.behavior.reset_user_interaction()

            self.speak_stream_end.emit(0)

            self.state_machine.transition(PetState.INTERACTING)

            self._play_loading(is_play_loading)

            self._async_brain(self._interact_pipeline, hint, record_context, context_hint,
                              thinking, enable_tools, attachment_text, attachment_image)

        QTimer.singleShot(delay_ms, _execute)

    def _interact_pipeline(self, hint: str, record_context: bool = False,
                           context_hint: str = "", thinking: bool | None = None,
                           enable_tools: bool | None = None,
                           attachment_text: str | None = None,
                           attachment_image=None):
        if record_context:
            store_hint = context_hint if context_hint else hint
            self.behavior.add_context(role="user", content=store_hint)
        stream_started = False
        self._active_stream_id += 1
        my_stream_id = self._active_stream_id

        def _is_stale() -> bool:
            return self._cancel_flag or my_stream_id != self._active_stream_id

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
            cancel_check=_is_stale,
            attachment_text=attachment_text, attachment_image=attachment_image,
        )

        if stream_started:
            self.speak_stream_end.emit(4000)
        return result

    def _trigger_chat(self, **kwargs):
        """用户对话。"""
        self._trigger_dialogue("chat", **kwargs)

    def _trigger_analyze(self, **kwargs):
        """分析用户交付的文件或图片（看一看）。"""
        self._trigger_dialogue("analyze", **kwargs)

    def _trigger_dialogue(self, kind: str, message: str = "", is_play_loading: bool = True,
                          thinking: bool | None = None,
                          enable_tools: bool | None = None,
                          attachment_text: str | None = None,
                          attachment_image=None,
                          log_message: str | None = None):
        """对话与分析共用入口：log_message 是进历史与上下文池的那一份，缺省用 message。"""
        if self.state_machine.state == PetState.INTERACTING:
            logger.info(f"[PetAgent] {kind} request ignored (INTERACTING)")
            return

        self.behavior.reset_user_interaction()

        self.speak_stream_end.emit(0)

        self.state_machine.transition(PetState.INTERACTING)

        pet_x, pet_y = 0, 0
        if self._pet_window:
            pet_x = self._pet_window.x()
            pet_y = self._pet_window.y()
        snap = self.window_snapshot()

        self._play_loading(is_play_loading)

        self._async_brain(self._dialogue_pipeline, kind, message, pet_x, pet_y, snap, thinking,
                          enable_tools, attachment_text, attachment_image, log_message)
        logger.info(f"[PetAgent] user {kind}:{message}")
        try:
            self.conversation_store.add("user", log_message if log_message is not None else message)
        except Exception:
            pass

    def _dialogue_pipeline(self, kind: str, message: str, pet_x: int, pet_y: int, snap=None,
                           thinking: bool | None = None,
                           enable_tools: bool | None = None,
                           attachment_text: str | None = None,
                           attachment_image=None,
                           log_message: str | None = None):
        self.behavior.add_context(role="user", content=log_message if log_message is not None else message)

        window_context = self._window_context(pet_x, pet_y, snap)
        context = window_context if window_context else "当前无窗口信息"

        stream_started = False
        self._active_stream_id += 1
        my_stream_id = self._active_stream_id

        def _is_stale() -> bool:
            return self._cancel_flag or my_stream_id != self._active_stream_id

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

        decide = (self.behavior.analyze_decide_stream if kind == "analyze"
                  else self.behavior.chat_decide_stream)
        # 分析只看交付物，不附当前屏幕
        result = decide(
            message, context, screenshot=kind != "analyze",
            on_chunk=on_chunk, on_stream_end=on_stream_end,
            thinking=thinking, enable_tools=enable_tools,
            cancel_check=_is_stale,
            attachment_text=attachment_text, attachment_image=attachment_image,
        )

        if stream_started:
            self.speak_stream_end.emit(4000)
        return result

    def _on_state_changed(self, state: str):
        """记录进入脑线程占用状态（autonomous/interacting）的时刻，供看门狗检测挂死。"""
        if state in (PetState.AUTONOMOUS.value, PetState.INTERACTING.value):
            now = time.monotonic()
            self._brain_busy_since = now
            self._brain_progress_ts = now
        else:
            self._brain_busy_since = None
            self._brain_progress_ts = None

    def note_brain_progress(self):
        """管线产生实质进展（chunk / 工具轮次 / LLM 返回）时由 Behavior 回调。"""
        if self._brain_busy_since is not None:
            self._brain_progress_ts = time.monotonic()

    def brain_idle_seconds(self) -> float | None:
        """脑线程占用状态下「无进展」的秒数；空闲/休眠返回 None"""
        if self._brain_busy_since is None:
            return None
        base = self._brain_progress_ts or self._brain_busy_since
        return time.monotonic() - base

    def is_game_active(self) -> bool:
        """是否存在进行中的游戏对局（脑线程可能正阻塞等待玩家落子）。"""
        try:
            return GAME.has_active_session()
        except Exception:
            return False

    def recover_stuck_brain(self):
        """脑线程管线疑似挂死：取消并回收脑线程，强制回 IDLE，复位加载态。"""
        ts = datetime.now().strftime("%H:%M:%S")
        self._cancel_running_thread(ts)
        self.state_machine.force(PetState.IDLE)
        self._set_llm_loading(False)
        self.notify_requested.emit("状态恢复", "LLM调用疑似挂死，已强制恢复", 5000)

    def _cancel_running_thread(self, ts: str = ""):
        """协作式取消当前脑线程：置取消标志、终结游戏会话、短窗口等待退出"""
        old_thread = self._thread
        if old_thread is None or not old_thread.isRunning():
            return
        self._cancel_flag = True
        # 终结旧线程持有的游戏会话
        try:
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

    def _async_brain(self, fn, *args, on_result=None, on_error=None):
        fn_name = getattr(fn, "__name__", repr(fn))
        ts = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{ts}] [PetAgent] _async_brain: {fn_name}")
        old_thread = self._thread
        old_worker = self._worker
        self._cancel_running_thread(ts)
        self._retire(old_thread, old_worker)
        self._cancel_flag = False
        self._set_llm_loading(True)  # 开始 LLM 加载粒子
        self._worker = BrainWorker(fn, *args)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(on_result or self._on_brain_result)
        self._worker.error.connect(on_error or self._on_brain_error)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _set_llm_loading(self, loading: bool):
        """更新 LLM 交互状态，同时广播给 UI。"""
        self._llm_loading = loading
        self.llm_loading.emit(loading)

    @property
    def is_llm_loading(self) -> bool:
        """LLM 交互（脑线程）是否进行中。"""
        return self._llm_loading

    def _stop_loading(self):
        """停止 LLM 加载粒子（流式开始或调用结束时调用）。"""
        self._set_llm_loading(False)

    def _cleanup_thread(self):
        sender = self.sender()
        if self._thread is not None and self._thread is sender:  # 仅清理当前线程，忽略旧线程延迟信号
            self._thread.deleteLater()
            self._thread = None
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _retire(self, thread, worker):
        """回收旧脑线程：已结束则立即延迟删除；仍在运行则等自然结束后再回收。

        绝不析构运行中的 QThread（~QThread 会触发 qFatal abort），
        必须持有 Python 引用直至线程结束，避免 GC 触发同样的析构。
        """
        if thread is None:
            return
        try:
            thread.finished.disconnect()
        except (RuntimeError, TypeError):
            pass
        if worker is not None:
            try:
                worker.finished.disconnect()
                worker.error.disconnect()
            except (RuntimeError, TypeError):
                pass

        if not thread.isRunning():
            thread.deleteLater()
            if worker is not None:
                worker.deleteLater()
            return

        logger.info("[PetAgent] old brain thread still running, defer deletion until finished")
        self._retired.append((thread, worker))
        if worker is not None:
            # 保持退出链路：worker 结束后让线程事件循环退出
            worker.finished.connect(thread.quit)
            worker.error.connect(thread.quit)
            # finished 在线程内直连触发 deleteLater，延迟删除随线程退出处理
            thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_retired_finished)

    def _on_retired_finished(self):
        """退役线程自然结束后，释放持有的 Python 引用。"""
        thread = self.sender()
        self._retired = [pair for pair in self._retired if pair[0] is not thread]

    def has_running_threads(self) -> bool:
        """是否存在仍在运行的脑线程（含退役列表）。"""
        if self._thread is not None and self._thread.isRunning():
            return True
        return any(thread is not None and thread.isRunning()
                   for thread, _worker in self._retired)

    def _on_brain_result(self, result):
        self._stop_loading()
        ts = datetime.now().strftime("%H:%M:%S")
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
            if result.actions:
                self.action_batch_started.emit()
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
        threading.Thread(target=self.behavior.flush_summaries, daemon=True).start()

    def _save_memory_line(self, line: str):
        """后台线程保存记忆（含 embedding 网络调用）。"""
        try:
            self.memory_store.save_from_line(line)
        except Exception as e:
            logger.warning(f"[PetAgent] memory save failed: {e}")

    def _on_brain_error(self, msg: str):
        self._stop_loading()
        if self.state_machine.state in (PetState.INTERACTING, PetState.AUTONOMOUS):
            self.state_machine.transition(PetState.IDLE)
        logger.error(f"[PetAgent] ERROR: {msg}")
        self.behavior.reset_active_tool_groups()


