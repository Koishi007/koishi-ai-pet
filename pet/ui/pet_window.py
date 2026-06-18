import logging

from PySide6.QtWidgets import QLabel, QVBoxLayout, QMenu
from PySide6.QtCore import Qt, QPoint, QDateTime
from PySide6.QtGui import QMouseEvent, QAction
from pet.ui.base_window import TransparentWindow
from pet.ui.pet_animations import PetAnimator
from pet.ui.particle import ParticleWidget
from pet.action import PetActions, ActionQueue
from pet.brain.prompts import INTERACT_GRABBED, INTERACT_RELEASED, INTERACT_WINDOW_DISAPPEARED
from pet.skills.registry import SKILL_REGISTRY
from config import config

logger = logging.getLogger(__name__)


class StickyMenu(QMenu):
    """点击 checkable 项时不关闭菜单。"""

    def mouseReleaseEvent(self, event: QMouseEvent):
        action = self.actionAt(event.pos())
        if action is not None and action.isCheckable():
            action.toggle()
        else:
            super().mouseReleaseEvent(event)


class PetWindow(TransparentWindow):
    def __init__(self):
        super().__init__()
        self._setup_ui()
        self._grab_local: QPoint | None = None
        self._chat_bubble = None
        self._agent = None
        self._debug_window = None
        self._app = None
        self._event_reaction = False
        self._drag_history: list = []  # [(QPoint, timestamp_ms), ...]
        self._PROMPT_GRABBED = INTERACT_GRABBED
        self._PROMPT_RELEASED = INTERACT_RELEASED
        self._PROMPT_WINDOW_DISAPPEARED = INTERACT_WINDOW_DISAPPEARED

    def set_chat_bubble(self, chat_bubble):
        """注入 ChatBubble 引用。"""
        self._chat_bubble = chat_bubble

    def set_agent(self, agent):
        """注入 PetAgent 引用，供右键菜单使用。"""
        self._agent = agent

    def set_app(self, app):
        """注入 QApplication 引用，供退出按钮使用。"""
        self._app = app

    def enterEvent(self, event):
        """鼠标进入桌宠区域时显示聊天按钮。"""
        if self._chat_bubble and self._grab_local is None:
            self._chat_bubble.show_bubble()
        super().enterEvent(event)

    def leaveEvent(self, event):
        """鼠标离开桌宠区域时延迟隐藏。"""
        if self._chat_bubble:
            self._chat_bubble.schedule_hide()
        super().leaveEvent(event)

    def _setup_ui(self):
        self.setFixedSize(config.PET_WIDTH, config.PET_HEIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.pet_label = QLabel()
        self.pet_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.pet_label)

        self.pet_anim = PetAnimator(parent=self)
        self.pet_anim.frame_changed.connect(self.pet_label.setPixmap)
        self.particles = ParticleWidget(self)
        self.pet_actions = PetActions(self, self.pet_anim, parent=self)
        self.action_queue = ActionQueue(self.pet_actions, parent=self)

        self.pet_actions.gravity.falling_started.connect(self._on_falling_started)
        self.pet_actions.gravity.landed.connect(self._on_landed)
        self.pet_actions.gravity.standing_lost.connect(self._on_standing_lost)

        # 初始位置：屏幕底部居中
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - config.PET_WIDTH) // 2
            y = geo.bottom() - config.PET_HEIGHT
            self.move(x, y)

        if not self.pet_anim.play("idle"):
            self._use_emoji_fallback()

    def _use_emoji_fallback(self):
        self.pet_label.setText("\U0001f436")
        font = self.pet_label.font()
        font.setPointSize(48)
        self.pet_label.setFont(font)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._grab_local = QPoint(62, 20)
            self._drag_history.clear()
            if self._chat_bubble:
                self._chat_bubble.hide_bubble()
            self.pet_actions.gravity.enable(False)
            self.action_queue.pause()
            self.action_queue.clear()
            self.pet_actions.grabbed()
            logger.info("[PetWindow] grabbed")
            if self._agent and self._event_reaction :
                self._agent.trigger("interact", hint=self._PROMPT_GRABBED)
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._grab_local is not None:
            new_pos = event.globalPosition().toPoint() - self._grab_local
            self.move(new_pos)
            now = QDateTime.currentMSecsSinceEpoch()
            self._drag_history.append((new_pos, now))
            if len(self._drag_history) > 10:
                self._drag_history.pop(0)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton or self._grab_local is None:
            return
        self._grab_local = None
        self.action_queue.resume()
        vx, vy = 0.0, 0.0
        # 只使用最近 100ms 内的采样帧，过期帧视为停顿（避免释放前停顿导致速度为 0）
        now = QDateTime.currentMSecsSinceEpoch()
        recent = [(p, t) for p, t in self._drag_history if now - t <= 150]
        if len(recent) >= 2:
            p1, t1 = recent[0]
            p2, t2 = recent[-1]
            dt = (t2 - t1) / 1000.0
            if dt > 0.005:
                vx = (p2.x() - p1.x()) / dt
                vy = (p2.y() - p1.y()) / dt
        self._drag_history.clear()
        speed = (vx ** 2 + vy ** 2) ** 0.5
        self.pet_actions.gravity.enable(True)
        if speed > 80:
            self.pet_actions.gravity.apply_impulse(vx, vy)
        logger.info(f"[PetWindow] released speed={speed:.0f}px/s flick={speed > 80}")
        if self._agent and self._event_reaction:
            self._agent.trigger("interact", hint=self._PROMPT_RELEASED)

    def _show_context_menu(self, pos):
        """右键菜单。"""
        menu = QMenu()

        if self._agent:
            # 自主决策开关（未启动或已暂停 → 显示"开启"，运行中 → 显示"关闭"）
            scheduler_running = self._agent.scheduler.is_running()
            mid_paused = self._agent.scheduler.is_mid_paused()
            should_resume = not scheduler_running or mid_paused
            toggle_sched = QAction("开启自主行动" if should_resume else "关闭自主行动")
            toggle_sched.triggered.connect(self._toggle_scheduler)
            menu.addAction(toggle_sched)

            # 调试窗口
            debug_action = QAction("调试面板")
            debug_action.triggered.connect(self._show_debug_window)
            menu.addAction(debug_action)

            # 技能开关子菜单（勾选不关闭）
            skill_menu = StickyMenu("技能", menu)
            for name in SKILL_REGISTRY.skill_names:
                action = skill_menu.addAction(name)
                action.setCheckable(True)
                action.setChecked(SKILL_REGISTRY.is_enabled(name))
                action.toggled.connect(lambda checked, n=name: SKILL_REGISTRY.set_enabled(n, checked))
            menu.addMenu(skill_menu)

            menu.addSeparator()

            # 互动反应开关
            on = self._event_reaction
            toggle_mouse = QAction("关闭互动反应" if on else "开启互动反应")
            toggle_mouse.triggered.connect(self._toggle_event_reaction)
            menu.addAction(toggle_mouse)

        menu.addSeparator()

        # 隐藏 / 退出
        hide_action = QAction("隐藏桌宠")
        hide_action.triggered.connect(self.hide)
        menu.addAction(hide_action)

        if self._app:
            quit_action = QAction("退出")
            quit_action.triggered.connect(self._app.quit)
            menu.addAction(quit_action)

        menu.exec(pos)

    def _toggle_scheduler(self):
        scheduler = self._agent.scheduler
        if not scheduler.is_running():
            scheduler.start()
            self._agent.trigger_once(2000)
        elif scheduler.is_mid_paused():
            scheduler.resume_mid()
            self._agent.trigger_once(2000)
        else:
            scheduler.pause_mid()

    def _toggle_event_reaction(self):
        self._event_reaction = not self._event_reaction
        logger.info(f"Event reaction {'enabled' if self._event_reaction else 'disabled'}")

    def _show_debug_window(self):
        if self._debug_window is None:
            from pet.ui.debug_window import DebugWindow
            self._debug_window = DebugWindow(self, agent=self._agent)
        self._debug_window.show()
        self._debug_window.activateWindow()
        self._debug_window.raise_()

    def _on_falling_started(self):
        self.action_queue.pause()

    def _on_landed(self):
        self.action_queue.resume()
        self.particles.spawn("dust")

    def _on_emotion_hearts(self):
        """love 情绪时触发爱心粒子。"""
        self.particles.spawn("hearts")

    def _on_standing_lost(self, window_title: str):
        """站立窗口消失/被遮挡时，触发 LLM 交互反应。"""
        hint = self._PROMPT_WINDOW_DISAPPEARED
        if window_title:
            hint += f"\n消失的窗口标题：「{window_title}」"
        logger.info(f"[PetWindow] standing_lost: \"{window_title}\"")
        if self._agent and self._event_reaction:
            self._agent.trigger("interact", hint=hint)

    # ── 队列控制接口 ──

    def queue_enqueue(self, method: str, *args, **kwargs):
        self.action_queue.enqueue(method, *args, **kwargs)

    def queue_enqueue_action(self, name: str, args: tuple, kwargs: dict):
        self.action_queue.enqueue(name, *args, **kwargs)

    def queue_start(self):
        self.action_queue.start()

    def queue_stop(self):
        self.action_queue.stop()

    def queue_clear(self):
        self.action_queue.clear()

    def shutdown(self):
        self.pet_anim.stop()
        self.action_queue.clear()
        self.pet_actions.gravity.enable(False)
