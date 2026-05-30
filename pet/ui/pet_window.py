from PySide6.QtWidgets import QLabel, QVBoxLayout, QMenu
from PySide6.QtCore import Qt, QPoint, QDateTime
from PySide6.QtGui import QMouseEvent, QAction
from pet.ui.base_window import TransparentWindow
from pet.ui.pet_animations import PetAnimator
from pet.action import PetActions, ActionQueue
from config import config


class PetWindow(TransparentWindow):
    def __init__(self):
        super().__init__()
        self._setup_ui()
        self._grab_local: QPoint | None = None
        self._chat_bubble = None
        self._agent = None
        self._debug_window = None
        self._app = None
        self._drag_history: list = []  # [(QPoint, timestamp_ms), ...]

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

        self.pet_anim = PetAnimator(self.pet_label, parent=self)
        self.pet_actions = PetActions(self, self.pet_anim, parent=self)
        self.action_queue = ActionQueue(self.pet_actions, parent=self)

        self.pet_actions.gravity.falling_started.connect(self._on_falling_started)
        self.pet_actions.gravity.landed.connect(self._on_landed)

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
            self.pet_actions.caught()
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

    def _show_context_menu(self, pos):
        """右键菜单。"""
        menu = QMenu()

        if self._agent:
            # 调度器开关
            scheduler_running = self._agent.scheduler.is_running()
            toggle_sched = QAction("关闭自主行动" if scheduler_running else "开启自主行动")
            toggle_sched.triggered.connect(self._toggle_scheduler)
            menu.addAction(toggle_sched)

            # 窶通状态
            from pet.agent.state import PetState
            if self._agent.state_machine.state == PetState.SLEEPING:
                wake_action = QAction("唤醒")
                wake_action.triggered.connect(lambda: self._agent.force_state("idle"))
                menu.addAction(wake_action)
            else:
                sleep_action = QAction("小憋一下")
                sleep_action.triggered.connect(lambda: self._agent.force_state("sleeping"))
                menu.addAction(sleep_action)

            menu.addSeparator()

            # 调试窗口
            debug_action = QAction("调试面板")
            debug_action.triggered.connect(self._show_debug_window)
            menu.addAction(debug_action)

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
        if self._agent.scheduler.is_running():
            self._agent.scheduler.stop()
        else:
            self._agent.scheduler.start()
            self._agent.trigger_once(2000)  # 启动后 2s 即刻触发首次决策

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
