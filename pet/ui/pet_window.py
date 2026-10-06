import ctypes
import logging
import sys
import time

from PySide6.QtWidgets import QLabel, QVBoxLayout, QMenu
from PySide6.QtCore import Qt, QPoint, QPointF, QDateTime, QTimer, QSize, Property, QPropertyAnimation, Signal
from PySide6.QtGui import (QMouseEvent, QAction, QPainter, QPainterPath, QColor, QPen,
                           QDragEnterEvent, QDragMoveEvent, QDragLeaveEvent, QDropEvent)
from pet.agent.state import PetState
from pet.ui.base_window import TransparentWindow
from pet.ui.pet_animations import PetAnimator
from pet.ui.particle import ParticleWidget
from pet.ui.styles import MENU_QSS
from pet.ui.settings_window import SettingsWindow
from pet.ui.file_drop_handler import FileDropHandler, paths_from_mime
from pet.action import PetActions, ActionQueue, outcome
from pet.brain.prompts import (INTERACT_GRABBED, INTERACT_RELEASED,
                               INTERACT_WINDOW_DISAPPEARED, interact_file_reject_prompt)
from pet.tools.registry import TOOL_REGISTRY
from pet.config import config

logger = logging.getLogger(__name__)

_R = 8  # 菜单圆角半径


class _FlatMenuBase(QMenu):
    """扁平圆角菜单基类 — Windows 下自绘圆角背景，macOS 走原生。"""

    def __init__(self, title="", parent=None):
        super().__init__(title, parent)
        self.setStyleSheet(MENU_QSS)
        self._is_win = sys.platform == "win32"
        if self._is_win:
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.Popup
                | Qt.WindowType.NoDropShadowWindowHint
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        if not self._is_win:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(0, 0, -1, -1), _R, _R)
        painter.fillPath(path, QColor("#ffffff"))
        painter.setPen(QPen(QColor("#dddddd"), 1))
        painter.drawPath(path)
        painter.setClipPath(path)
        super().paintEvent(event)

    def sizeHint(self):
        s = super().sizeHint()
        return QSize(s.width() + 4, s.height() + 4) if self._is_win else s


class StickyMenu(_FlatMenuBase):
    """点击 checkable 项时不关闭菜单。"""

    def mouseReleaseEvent(self, event: QMouseEvent):
        action = self.actionAt(event.pos())
        if action is not None and action.isCheckable():
            action.toggle()
        else:
            super().mouseReleaseEvent(event)


class _SpriteLabel(QLabel):
    """宠物贴图：按呼吸姿态绘制（上抬 + 缩放），不动窗口以免干扰重力判定。"""

    dpr_outdated = Signal(float)  # 窗口 DPR 与帧贴图生成时不一致时发出

    # 点击 Q 弹的形变幅度
    _TAP_SQUASH = 0.9
    _TAP_OVERSHOOT = 1.03
    _TAP_DURATION_MS = 450

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pose: tuple[float, float, float] = (0, 1.0, 1.0)
        self._tap_scale: float = 1.0
        self._tap_anim: QPropertyAnimation | None = None

    def set_pose(self, dy: float, scale_x: float, scale_y: float):
        pose = (dy, scale_x, scale_y)
        if pose != self._pose:
            self._pose = pose
            self.update()

    def play_tap_bounce(self):
        """单击（摸头）的 Q 弹：瞬时压扁后衰减回弹，与呼吸姿态相乘。"""
        anim = QPropertyAnimation(self, b"tap_scale", self)
        anim.setDuration(self._TAP_DURATION_MS)
        anim.setKeyValueAt(0.0, self._TAP_SQUASH)
        anim.setKeyValueAt(0.30, self._TAP_OVERSHOOT)
        anim.setKeyValueAt(0.60, 0.97)
        anim.setKeyValueAt(0.80, 1.02)
        anim.setKeyValueAt(1.0, 1.0)
        self._tap_scale = self._TAP_SQUASH  # 起手立即压扁，不等动画首帧
        self.update()
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._tap_anim = anim

    def _get_tap_scale(self) -> float:
        return self._tap_scale

    def _set_tap_scale(self, value: float):
        if value != self._tap_scale:
            self._tap_scale = value
            self.update()

    tap_scale = Property(float, _get_tap_scale, _set_tap_scale)

    def paintEvent(self, event):
        pixmap = self.pixmap()
        # 帧 DPR 与窗口不符时上报，由上层重建；本帧照常绘制，不等新帧
        if pixmap is not None and not pixmap.isNull():
            if abs(pixmap.devicePixelRatio() - self.devicePixelRatioF()) > 0.01:
                self.dpr_outdated.emit(self.devicePixelRatioF())
        if pixmap is None or (self._pose == (0, 1.0, 1.0) and self._tap_scale == 1.0):
            super().paintEvent(event)
            return

        dy, scale_x, scale_y = self._pose
        scale_x *= self._tap_scale
        scale_y *= self._tap_scale
        # Qt6 的 QPixmap.width()/height() 返回设备像素，帧贴图带 DPR（高DPI适配），
        # 需除回逻辑像素再计算，否则 dpr>1 时贴图会画偏（站立呼吸时左右闪动）
        dpr = pixmap.devicePixelRatio() or 1.0
        pm_w = pixmap.width() / dpr
        pm_h = pixmap.height() / dpr
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        left = round((self.width() - pm_w) / 2)
        top = dy
        # 以脚底中点为锚点缩放：横向居中、纵向站在原处，不会沉下去
        anchor_x = left + pm_w / 2
        anchor_y = top + pm_h
        painter.translate(anchor_x, anchor_y)
        painter.scale(scale_x, scale_y)
        painter.translate(-anchor_x, -anchor_y)
        # QPointF 重载支持亚像素定位：浮点呼吸位移平滑渲染，不取整成方波
        painter.drawPixmap(QPointF(left, top), pixmap)
        painter.end()


class PetWindow(TransparentWindow):
    # 拖拽抓取点在窗口内的相对位置：水平居中、纵向 16%（头部）
    _GRAB_RATIO_X = 0.5
    _GRAB_RATIO_Y = 0.16

    def __init__(self):
        super().__init__()
        self._setup_ui()
        self._grab_local: QPoint | None = None
        self._chat_bubble = None
        self._feed_bubble = None
        self._music_bubble = None
        self._file_bubble = None
        self._speech_bubble = None
        self._emotion_bubble = None
        self._agent = None
        self._debug_window = None
        self._log_window = None
        self._chat_history_window = None
        self._memory_window = None
        self._log_relay = None
        self._app = None
        self._event_reaction = False
        self._mouse_penetration = False  # 鼠标穿透开关，默认关
        self._outcomes_done: set[str] = set()  # 本轮已结算过产出的动作（每轮每个动作最多一条事件）
        self._fall_started_at: float | None = None  # 本次下落起点（monotonic），落地时结算
        self._await_fall_down = False               # 落地跌倒播放中，等它播完再恢复队列
        self._drag_history: list = []  # [(坐标点, 时间戳毫秒), ...]
        self._press_pos: QPoint | None = None  # 按下时的全局坐标
        self._frame_dpr_rebuild: float = 0.0  # 上一次按 DPR 重建帧时的窗口 DPR
        self._click_timer = QTimer(self)       # 单击检测定时器
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(200)      # 200ms 内无移动 → 判定为单击
        self._click_timer.timeout.connect(self._on_click_confirmed)
        self._PROMPT_GRABBED = INTERACT_GRABBED
        self._PROMPT_RELEASED = INTERACT_RELEASED
        self._PROMPT_WINDOW_DISAPPEARED = INTERACT_WINDOW_DISAPPEARED
        self._file_drop = FileDropHandler(
            hover_state=self._file_drop_state,
            is_busy=self._file_drop_busy,
            on_show_bubble=self._show_file_bubble,
            on_reject=self._reject_file_drop,
            on_busy_event=self._note_file_drop_busy,
            on_show_busy=self._show_busy_bubble,
        )
        self.setAcceptDrops(True)

    def set_chat_bubble(self, chat_bubble):
        """注入 ChatBubble 引用。"""
        self._chat_bubble = chat_bubble

    def set_feed_bubble(self, feed_bubble):
        """注入 FeedBubble 引用。"""
        self._feed_bubble = feed_bubble

    def set_music_bubble(self, music_bubble):
        """注入 MusicBubble 引用。"""
        self._music_bubble = music_bubble

    def set_file_bubble(self, file_bubble):
        """注入 FileBubble 引用。"""
        self._file_bubble = file_bubble

    def set_speech_bubble(self, speech_bubble):
        """注入 SpeechBubble 引用。"""
        self._speech_bubble = speech_bubble

    def set_emotion_bubble(self, emotion_bubble):
        """注入 EmotionBubble 引用。"""
        self._emotion_bubble = emotion_bubble

    def set_agent(self, agent):
        """注入 PetAgent 引用，供右键菜单使用。"""
        self._agent = agent

    def set_app(self, app):
        """注入 QApplication 引用，供退出按钮使用。"""
        self._app = app

    def enterEvent(self, event):
        """鼠标进入桌宠区域时显示聊天、喂食和音乐按钮。

        文件气泡显示期间让位：两者位置相邻，共存会互相遮挡并争抢鼠标。
        """
        if self._grab_local is None and not self._file_bubble_visible():
            if self._chat_bubble:
                self._chat_bubble.show_bubble()
            if self._feed_bubble:
                self._feed_bubble.show_bubble()
            if self._music_bubble:
                self._music_bubble.show_bubble()
        super().enterEvent(event)

    def _file_bubble_visible(self) -> bool:
        return bool(self._file_bubble and self._file_bubble.isVisible())

    def leaveEvent(self, event):
        """鼠标离开桌宠区域时延迟隐藏。"""
        if self._chat_bubble:
            self._chat_bubble.schedule_hide()
        if self._feed_bubble:
            self._feed_bubble.schedule_hide()
        if self._music_bubble:
            self._music_bubble.schedule_hide()
        super().leaveEvent(event)

    def _setup_ui(self):
        self.setFixedSize(config.PET_WIDTH, config.PET_HEIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.pet_label = _SpriteLabel()
        self.pet_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.pet_label)

        self.pet_anim = PetAnimator(parent=self)
        self.pet_anim.frame_changed.connect(self.pet_label.setPixmap)
        self.pet_anim.pose_changed.connect(self.pet_label.set_pose)
        self.pet_label.dpr_outdated.connect(self._on_frame_dpr_outdated)
        self.particles = ParticleWidget(self)
        self.pet_actions = PetActions(self, self.pet_anim, parent=self)
        self.action_queue = ActionQueue(self.pet_actions, parent=self)

        self.pet_actions.gravity.falling_started.connect(self._on_falling_started)
        self.pet_actions.gravity.landed.connect(self._on_landed)
        self.pet_anim.animation_finished.connect(self._on_anim_finished)
        self.action_queue.action_finished.connect(self._on_action_finished)
        self.pet_actions.gravity.standing_lost.connect(self._on_standing_lost)

        # 初始位置：屏幕中央
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - config.PET_WIDTH) // 2
            y = (geo.height() - config.PET_HEIGHT) // 2
            self.move(x, y)

        if not self.pet_anim.play("idle"):
            self._use_emoji_fallback()

    def _use_emoji_fallback(self):
        self.pet_label.setText("\U0001f436")
        font = self.pet_label.font()
        font.setPointSize(48)
        self.pet_label.setFont(font)

    def _on_frame_dpr_outdated(self, dpr: float):
        """帧贴图 DPR 与窗口不符时按新 DPR 重建。"""
        if dpr == self._frame_dpr_rebuild:
            return
        self._frame_dpr_rebuild = dpr
        self.pet_anim.rebuild_frames()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._drag_history.clear()
            if self._chat_bubble:
                self._chat_bubble.hide_bubble()
            if self._feed_bubble:
                self._feed_bubble.hide_bubble()
            if self._music_bubble:
                self._music_bubble.hide_bubble()
            if self._file_bubble:
                self._file_bubble.hide_bubble()
            # 先启动单击检测定时器，等待判断是单击还是拖拽
            self._click_timer.start()
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())

    def _start_drag(self):
        """确认为拖拽：激活抓取状态。"""
        self._grab_local = QPoint(
            round(config.PET_WIDTH * self._GRAB_RATIO_X),
            round(config.PET_HEIGHT * self._GRAB_RATIO_Y),
        )
        self.pet_actions.gravity.enable(False)
        self.action_queue.pause()
        self.action_queue.clear()
        self.pet_actions.grabbed()
        logger.info("[PetWindow] grabbed")
        if self._agent:
            self._agent.note_event("grabbed")  # 事件记录独立于 LLM 反应开关
            if self._event_reaction:
                self._agent.trigger("interact", hint=self._PROMPT_GRABBED, is_play_loading=False, thinking=False, enable_tools=False)

    def _on_click_confirmed(self):
        """200ms 内无移动，判定为单击（摸头），并提升心理状态。"""
        self._press_pos = None
        self.pet_label.play_tap_bounce()
        self.particles.spawn("hearts")
        if self._agent is not None:
            self._agent.note_head_pat()
            self._agent.mood.modify_sanity(1.0)
            self._agent.mood.modify_joy(1.0)

    def mouseMoveEvent(self, event: QMouseEvent):
        # 若单击定时器还在跑，检查是否已移动足够距离以判定为拖拽
        if self._click_timer.isActive() and self._press_pos is not None:
            delta = event.globalPosition().toPoint() - self._press_pos
            if delta.manhattanLength() >= 5:
                self._click_timer.stop()
                self._start_drag()
        if self._grab_local is not None:
            new_pos = event.globalPosition().toPoint() - self._grab_local
            self.move(new_pos)
            now = QDateTime.currentMSecsSinceEpoch()
            self._drag_history.append((new_pos, now))
            if len(self._drag_history) > 10:
                self._drag_history.pop(0)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        # 若单击定时器还在跑（松开很快，未触发移动），也当单击处理
        if self._click_timer.isActive():
            self._click_timer.stop()
            self._on_click_confirmed()
            return
        if self._grab_local is None:
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
        if self._agent:
            self._agent.note_event("released")
            if self._event_reaction:
                self._agent.trigger("interact", hint=self._PROMPT_RELEASED, is_play_loading=False, thinking=False)

    # 拖入文件

    @staticmethod
    def _accept_copy(event) -> None:
        """统一以复制语义接受拖放"""
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragEnterEvent(self, event: QDragEnterEvent):
        if not self._file_drop.accept_hover(bool(paths_from_mime(event.mimeData()))):
            event.ignore()
            return
        self._accept_copy(event)

    def dragMoveEvent(self, event: QDragMoveEvent):
        # 不接受则收不到 dropEvent（设计文档 §0.1）
        self._accept_copy(event)

    def dragLeaveEvent(self, event: QDragLeaveEvent):
        event.accept()

    def dropEvent(self, event: QDropEvent):
        self._accept_copy(event)
        self._file_drop.handle_drop(paths_from_mime(event.mimeData()))

    def _file_drop_state(self) -> tuple[bool, bool]:
        """（功能开关, 鼠标穿透）：两项任一为真都走忽略分支。"""
        return bool(config.FILE_DROP_ENABLED), bool(self._mouse_penetration)

    def _file_drop_busy(self) -> bool:
        """忙态：脑线程占用中，即 INTERACTING（请求已发出未返回）或 AUTONOMOUS（自主决策进行中）。"""
        agent = self._agent
        if agent is None:
            return False
        return agent.state_machine.state in (PetState.INTERACTING, PetState.AUTONOMOUS)

    def _hide_hover_bubbles(self) -> None:
        """收起 chat / feed / music 三个悬空气泡，给文件气泡让位。"""
        for bubble in (self._chat_bubble, self._feed_bubble, self._music_bubble):
            if bubble:
                bubble.hide_bubble()

    def _show_file_bubble(self, refs) -> None:
        """放下通过：先收起三个悬空气泡，再显示文件气泡"""
        self._hide_hover_bubbles()
        if self._file_bubble:
            self._file_bubble.show_files(refs)

    def _show_busy_bubble(self, paths) -> None:
        """忙态放下：复用文件气泡提示收不下，不打断脑线程。"""
        self._hide_hover_bubbles()
        if self._file_bubble:
            self._file_bubble.show_busy(paths)

    def _reject_file_drop(self, reason: str, names=()) -> None:
        """三类硬边界拒收：发一次快速交互请求，姿态与数值交给模型决定。"""
        if self._agent is None:
            return
        # 文件动作不做冷却：同一个文件重复拖入各触发一次
        self._agent.trigger("interact", hint=interact_file_reject_prompt(reason, names),
                            delay_ms=150, cooldown_ms=0, is_play_loading=False,
                            thinking=False, enable_tools=False)

    def _note_file_drop_busy(self, text: str) -> None:
        """忙态不请求即时反应（通道在 INTERACTING 状态自我丢弃），改写一次性事件。"""
        if self._agent is not None:
            self._agent.note_once_event("file_drop_busy", text)

    def _show_context_menu(self, pos):
        """右键菜单。"""
        menu = _FlatMenuBase()

        if self._agent:
            # 自主决策开关（mid 已暂停 → 显示"开启"，运行中 → 显示"关闭"）
            mid_paused = self._agent.scheduler.is_mid_paused()
            toggle_sched = QAction("开启自主行动" if mid_paused else "关闭自主行动")
            toggle_sched.triggered.connect(self._toggle_scheduler)
            menu.addAction(toggle_sched)

            # 调试窗口
            debug_action = QAction("调试面板")
            debug_action.triggered.connect(self._show_debug_window)
            menu.addAction(debug_action)

            # 日志窗口
            log_action = QAction("日志")
            log_action.triggered.connect(self._show_log_window)
            menu.addAction(log_action)

            # 对话历史窗口
            history_action = QAction("对话历史")
            history_action.triggered.connect(self._show_chat_history)
            menu.addAction(history_action)

            # 记忆管理窗口
            memory_action = QAction("记忆管理")
            memory_action.triggered.connect(self._show_memory_window)
            menu.addAction(memory_action)

            # 设置窗口
            settings_action = QAction("设置")
            settings_action.triggered.connect(lambda: self._open_settings())
            menu.addAction(settings_action)

            # 工具子菜单（每工具支持独立子菜单）
            tool_menu = StickyMenu("工具", menu)
            for name in TOOL_REGISTRY.tool_names:
                tool = TOOL_REGISTRY._tools.get(name)
                if not tool:
                    continue
                if tool.meta:
                    continue  # 元工具（如 tool_search、food），始终启用不可禁用

                # 工具开关（可勾选）
                tool_action = tool_menu.addAction(name)
                tool_action.setCheckable(True)
                tool_action.setChecked(TOOL_REGISTRY.is_enabled(name))
                tool_action.toggled.connect(
                    lambda checked, n=name: TOOL_REGISTRY.set_enabled(n, checked))

                # 如果有子菜单项，附加到动作上
                if tool.menu_items:
                    sub_menu = _FlatMenuBase(name)
                    for item in tool.menu_items:
                        sub_action = sub_menu.addAction(item["label"])
                        sub_action.triggered.connect(item["handler"])
                    tool_action.setMenu(sub_menu)

            menu.addMenu(tool_menu)

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

        if hasattr(self, "_quit_fn"):
            quit_action = QAction("退出")
            quit_action.triggered.connect(self._quit_fn)
            menu.addAction(quit_action)

        menu.exec(pos)

    def _toggle_scheduler(self):
        scheduler = self._agent.scheduler
        if scheduler.is_mid_paused():
            scheduler.resume_mid()
            self._agent.trigger_once(2000)
        else:
            scheduler.pause_mid()

    def _open_settings(self):
        SettingsWindow.show_instance(self._agent, self)

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

    def set_log_relay(self, relay):
        self._log_relay = relay

    def _show_log_window(self):
        if self._log_window is None:
            from pet.ui.log_window import LogWindow
            self._log_window = LogWindow(self._log_relay)
        self._log_window.show()
        self._log_window.activateWindow()
        self._log_window.raise_()

    def _show_chat_history(self):
        if self._chat_history_window is None:
            from pet.ui.chat_history import ChatHistoryWindow
            store = self._agent.conversation_store if self._agent else None
            if store is None:
                return
            self._chat_history_window = ChatHistoryWindow(store)
        self._chat_history_window.show()
        self._chat_history_window.activateWindow()
        self._chat_history_window.raise_()

    def _show_memory_window(self):
        if self._memory_window is None:
            from pet.ui.memory_window import MemoryWindow
            self._memory_window = MemoryWindow(self._agent)
        self._memory_window.show()
        self._memory_window.activateWindow()
        self._memory_window.raise_()

    def _on_falling_started(self):
        self._fall_started_at = time.monotonic()
        self._await_fall_down = False  # 上一次的等待随新一次下落作废
        self.action_queue.pause()

    def _on_landed(self):
        self.particles.spawn("dust")
        fall_seconds = time.monotonic() - self._fall_started_at if self._fall_started_at else 0.0
        self._fall_started_at = None
        self._await_fall_down = False
        # 长时间下落先播跌倒动作，播完再恢复队列
        if fall_seconds >= config.FALL_DOWN_SECONDS and self.pet_actions.fall_down():
            if self._agent:
                self._agent.note_once_event("fall_down")
            self._await_fall_down = True
            return
        self.action_queue.resume()

    def _on_anim_finished(self, action: str):
        if action == "fall_down" and self._await_fall_down:
            self._await_fall_down = False
            logger.info("[PetWindow] fall_down 播放完成，恢复动作队列")
            self.action_queue.resume()

    def on_action_batch_started(self):
        """agent 一轮动作即将入队：清空产出标记。

        标记在入队时重置、在动作结束时使用，中间隔着一个队列周期，所以上一轮
        还没跑完的动作可能占掉新一轮的名额。不为此引入轮次编号，接受该偏差。
        """
        self._outcomes_done.clear()

    def _on_action_finished(self, name: str):
        """动作正常结束时结算它的产出（玩法见 pet.action.outcome）。

        挂结束而非开始：动作时长接近脑周期，常横跨到下一轮之后才跑完。
        每轮每个动作最多结算一次，注入方式由玩法声明（一次性 / 窗口期）。
        """
        if not self._agent or name in self._outcomes_done:
            return
        spec = outcome.outcome_for(name)
        if spec is None:
            return
        self._outcomes_done.add(name)
        try:
            text = spec.handler()
        except Exception:
            # 槽里抛异常会顺信号冒泡进 Qt 事件循环，产出失败不该牵连调用方
            logger.exception(f"[PetWindow] 动作 '{name}' 的产出结算失败，已跳过")
            return
        if not text:
            return
        if spec.once:
            self._agent.note_once_event(name, text)
        else:
            self._agent.note_event(name, text)
        if spec.effect is not None:
            try:
                effect_name = spec.effect()
            except Exception:
                logger.exception(f"[PetWindow] 动作 '{name}' 的特效结算失败，已跳过")
                return
            if effect_name:
                self.particles.spawn(effect_name)

    def _on_standing_lost(self, window_title: str):
        """站立窗口消失/被遮挡时，触发 LLM 交互反应。"""
        hint = self._PROMPT_WINDOW_DISAPPEARED
        if window_title:
            hint += f"\n消失的窗口标题：「{window_title}」"
        logger.info(f"[PetWindow] standing_lost: \"{window_title}\"")
        if self._agent:
            self._agent.note_event("window_lost")
            self._agent.note_event("fall", "你从窗口上掉了下来")
            if self._event_reaction:
                self._agent.trigger("interact", hint=hint, is_play_loading=False, thinking=False, enable_tools=False)


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

    def hide(self):
        self.particles.hide()
        if self._chat_bubble:
            self._chat_bubble.hide()
        if self._feed_bubble:
            self._feed_bubble.hide()
        if self._music_bubble:
            self._music_bubble.hide()
        if self._file_bubble:
            self._file_bubble.hide_bubble()
        if self._speech_bubble:
            self._speech_bubble.hide()
        if self._emotion_bubble:
            self._emotion_bubble.hide()
        super().hide()

    def set_mouse_penetration(self, enabled: bool):
        """开启/关闭鼠标穿透：开启后窗口不接收任何鼠标事件，点击穿透到下层。"""
        self._mouse_penetration = enabled
        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                if not hwnd:
                    return
                GWL_EXSTYLE = -20
                WS_EX_TRANSPARENT = 0x00000020
                user32 = ctypes.windll.user32
                style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                if enabled:
                    style |= WS_EX_TRANSPARENT
                else:
                    style &= ~WS_EX_TRANSPARENT
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            except Exception as e:
                logger.warning(f"[PetWindow] set_mouse_penetration({enabled}) failed: {e}")
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, enabled)
        logger.info(f"[PetWindow] mouse_penetration={enabled}")

    def show(self):
        super().show()
        if self._mouse_penetration:
            self.set_mouse_penetration(True)
