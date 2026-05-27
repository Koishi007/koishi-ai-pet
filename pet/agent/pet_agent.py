"""PetAgent — 编排 Brain，通过 Signal 驱动 UI。"""

import logging
from datetime import datetime
from PySide6.QtCore import QObject, QThread, QThreadPool, QTimer, Signal

from pet.brain.behavior import Behavior, BehaviorOutput
from pet.brain.view import ViewBrain, OcrReader
from pet.agent.scheduler import Scheduler
from pet.agent.state import StateMachine
from pet.agent.screen_reader import ScreenReader
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
    """Agent 调度层"""

    action_requested = Signal(str, object, object)
    speak_requested  = Signal(str, int)
    state_changed    = Signal(str)
    view_ready       = Signal(str)
    view_error       = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.behavior = Behavior()
        self.view_brain = ViewBrain()
        self.screen_reader = ScreenReader()
        self.screen_reader.enable()
        self.ocr_reader = OcrReader(languages=config.OCR_LANGUAGES, gpu=config.OCR_GPU)
        self.scheduler = Scheduler(self)
        self.state_machine = StateMachine()
        self._pet_window = None  # PetWindow 引用，用于获取桌宠当前位置

        self.scheduler.mid_tick.connect(self._on_mid_tick)
        self.scheduler.fast_tick.connect(self._on_fast_tick)
        self.scheduler.slow_tick.connect(self._on_slow_tick)

        self._thread: QThread | None = None
        self._worker: BrainWorker | None = None

        # 后台预加载 OCR 模型，避免首次 mid_tick 卡 UI
        if config.OCR_ENABLED:
            QThreadPool.globalInstance().start(
                lambda: self.ocr_reader._get_reader()
            )

    def set_pet_window(self, window):
        self._pet_window = window

    def start(self):
        if config.SCHEDULER_AUTO_START:
            self.scheduler.start()
            logger.info("[PetAgent] scheduler auto-started")
            # 启动后延迟 5 秒再触发首次决策
            QTimer.singleShot(5000, self._on_mid_tick)
        else:
            logger.info("[PetAgent] scheduler auto-start disabled (SCHEDULER_AUTO_START=false)")

    def stop(self):
        self.scheduler.stop()
        self.screen_reader.disable()
        try:
            if self._thread and self._thread.isRunning():
                self._thread.quit()
                self._thread.wait(3000)
        except RuntimeError:
            pass  # QThread C++ 对象可能已被 Qt 销毁
        logger.info("[PetAgent] stopped")

    def trigger(self, intent: str, **kwargs):
        handlers = {
            "decide":              self._trigger_decide,
            "decide_with_vision":  self._trigger_vision_decide,
            "think":               self._trigger_think,
            "view":                self._trigger_view,
            "chat":                self._trigger_chat,
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

    def _on_mid_tick(self):
        ts = datetime.now().strftime("%H:%M:%S")
        if not self.state_machine.can_decide:
            logger.info(f"[{ts}] [PetAgent] [mid_tick] skipped (state={self.state_machine.state.value})")
            return

        image = self.screen_reader.capture_fullscreen()
        if image:
            # 主线程获取桌宠屏幕坐标，供后台线程计算窗口相对距离
            pet_x, pet_y = 0, 0
            if self._pet_window:
                pet_x = self._pet_window.x()
                pet_y = self._pet_window.y()
            self._async_brain(self._decide_pipeline, image, pet_x, pet_y)

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
        self.action_requested.emit("stretch", (), {})

    def _decide_pipeline(self, image, pet_x=0, pet_y=0):
        """后台线程中运行：窗口探测 + OCR 提取文字 + LLM 决策，不阻塞 UI。"""
        ts = datetime.now().strftime("%H:%M:%S")

        # ── Win32 窗口坐标探测 ──
        window_context = self._build_window_context(pet_x, pet_y)

        # ── OCR ──
        ocr_text = ""
        if config.OCR_ENABLED:
            try:
                ocr_text = self.ocr_reader.extract_text(image)
                if ocr_text:
                    logger.info(f"[{ts}] [PetAgent] OCR({len(ocr_text)} chars): {ocr_text[:100]}")
            except Exception as e:
                logger.warning(f"[{ts}] [PetAgent] OCR failed, skipped: {e}")

        # ── 组装 context ──
        parts = []
        if window_context:
            parts.append(window_context)
        if ocr_text:
            parts.append(f"屏幕文字(OCR): {ocr_text[:config.OCR_MAX_CHARS]}")
        context = "\n\n".join(parts) if parts else ""

        if self.behavior.has_vision:
            return self.behavior.decide_with_vision(image, context)
        else:
            return self.behavior.decide(context)

    def _build_window_context(self, pet_x: int, pet_y: int) -> str:
        """用 Win32 API 枚举可见窗口，生成桌宠可用的跳转参考数据。"""
        try:
            from pet.brain.window_detector import get_visible_windows, is_window_occluded
            windows = get_visible_windows()
        except Exception:
            return ""

        pet_w, pet_h = 125, 125  # 桌宠尺寸
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
            # 排除桌宠自身和过小的窗口
            if abs(left - pet_x) < 10 and abs(top - pet_y) < 10 and w == pet_w and h == pet_h:
                continue
            if w < 200 or h < 100:
                continue
            # 跳过被遮挡超过 80% 的窗口（后台线程中运行，不卡 UI）
            if is_window_occluded(win["hwnd"], threshold=0.8, skip_hwnd=pet_hwnd):
                continue

            # 相对桌宠的距离
            dx_walk = (left + w // 2) - pet_x           # 走到窗口中心需水平移动
            dy_top = top - (pet_y + pet_h)                # 跳到窗口顶部需垂直移动（负=向上）
            dy_bottom = bottom - pet_y                    # 跳到底部

            direction = "右" if dx_walk > 0 else "左"
            dist = abs(dx_walk)
            reachable = "可跳" if abs(dy_top) < 800 else "需攀爬"

            valid += 1
            lines.append(
                f"{valid}. \"{title}\" ｜ "
                f"范围: 左{left} 上{top} 右{right} 下{bottom} (宽{w} 高{h}) ｜ "
                f"相对桌宠: {direction}走{dist}px, 上跳{abs(dy_top)}px 到窗口顶 "
                f"({reachable})"
            )

        if valid == 0:
            lines.append("未发现适合跳转的窗口。")

        return "\n".join(lines)

    def _trigger_decide(self, context: str = ""):
        self._async_brain(self.behavior.decide, context)

    def _trigger_vision_decide(self, image, context: str = ""):
        self._async_brain(self.behavior.decide_with_vision, image, context)

    def _trigger_think(self, prompt: str = ""):
        self._async_brain(self.behavior.think, prompt)

    def _trigger_view(self, image, prompt: str = ""):
        self._async_brain(
            self.view_brain.analyze, image, prompt,
            on_result=self._on_view_result,
            on_error=self._on_view_error,
        )

    def _trigger_chat(self, message: str = ""):
        """用户对话触发：截图+上下文+LLM决策。"""
        from pet.agent.state import PetState
        self.state_machine.transition(PetState.INTERACTING)
        self.state_changed.emit(PetState.INTERACTING.value)

        # 获取当前屏幕上下文（在主线程中获取坐标）
        pet_x, pet_y = 0, 0
        if self._pet_window:
            pet_x = self._pet_window.x()
            pet_y = self._pet_window.y()

        # 清空队列，播放思考动画
        if self._pet_window:
            self._pet_window.action_queue.clear()
            self._pet_window.pet_actions.thinking()

        # 后台执行：构建上下文 + 调用 chat_decide
        self._async_brain(self._chat_pipeline, message, pet_x, pet_y)

    def _chat_pipeline(self, message: str, pet_x: int, pet_y: int):
        """后台线程：构建上下文 + 对话决策。"""
        ts = datetime.now().strftime("%H:%M:%S")
        self.behavior.add_context(f"[{ts}] [user] {message}")

        # 窗口探测
        window_context = self._build_window_context(pet_x, pet_y)

        # OCR（可选）
        ocr_text = ""
        if config.OCR_ENABLED:
            image = self.screen_reader.capture_fullscreen()
            if image:
                try:
                    ocr_text = self.ocr_reader.extract_text(image)
                except Exception:
                    pass

        # 组装 context
        parts = []
        if window_context:
            parts.append(window_context)
        if ocr_text:
            parts.append(f"屏幕文字(OCR): {ocr_text[:config.OCR_MAX_CHARS]}")
        context = "\n\n".join(parts) if parts else "当前无特殊环境信息"

        return self.behavior.chat_decide(message, context)

    def _async_brain(self, fn, *args, on_result=None, on_error=None):
        fn_name = getattr(fn, "__name__", repr(fn))
        ts = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{ts}] [PetAgent] _async_brain: {fn_name}")
        # 如果有旧线程还在跑，先断开信号避免结果乱序
        if self._worker is not None:
            try:
                self._worker.finished.disconnect()
                self._worker.error.disconnect()
            except (RuntimeError, TypeError):
                pass
        if self._thread is not None:
            try:
                if self._thread.isRunning():
                    self._thread.quit()
                    self._thread.wait(1000)
            except RuntimeError:
                pass  # C++ 对象已被 Qt 提前销毁
        self._worker = BrainWorker(fn, *args)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(on_result or self._on_brain_result)
        self._worker.error.connect(on_error or self._on_brain_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_brain_result(self, result):
        ts = datetime.now().strftime("%H:%M:%S")
        # 恢复状态（无论结果类型）
        from pet.agent.state import PetState
        if self.state_machine.state in (PetState.INTERACTING, PetState.TALKING):
            self.state_machine.transition(PetState.IDLE)
            self.state_changed.emit(PetState.IDLE.value)

        if isinstance(result, BehaviorOutput):
            logger.info(f"[{ts}] [PetAgent] ← {result}")
            action_names = [a.name for a in result.actions]
            self.behavior.add_context(
                f"[{ts}] did {', '.join(action_names)}, said: {result.speech or '(silent)'}")
            if result.speech:
                self.speak_requested.emit(result.speech, 4000)
            for step in result.actions:
                self.action_requested.emit(step.name, step.args, step.kwargs)
        elif isinstance(result, str):
            logger.info(f"[{ts}] [PetAgent] ← \"{result[:60]}\"")
            self.behavior.add_context(f"[{ts}] said: {result[:100]}")
            self.speak_requested.emit(result, 5000)

    def _on_brain_error(self, msg: str):
        # 回复状态（防止 INTERACTING/TALKING 卡死）
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


