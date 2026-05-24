from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QGroupBox, QTextEdit, QLabel, QLineEdit, QSpinBox,
    QFormLayout, QCheckBox, QFrame, QComboBox, QListWidget,
)
from datetime import datetime

from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtGui import QFont

from pet.ui.bubble import SpeechBubble
from pet.brain.behavior import Behavior
from pet.brain.view import ViewBrain
from pet.skills.system_monitor import SystemMonitor
from pet.agent.screen_reader import ScreenReader
from config import config


class DebugWindow(QWidget):

    def __init__(self, pet_window, agent=None, parent=None):
        super().__init__(parent)
        self.pet = pet_window
        self.agent = agent
        self.bubble = SpeechBubble(self.pet)
        if agent is not None:
            self.brain = agent.behavior
            self.view_brain = agent.view_brain
        else:
            self.brain = Behavior()
            self.view_brain = ViewBrain()
        self.monitor = SystemMonitor()
        self.monitor.enable()
        self.screen_reader = ScreenReader()
        self.screen_reader.enable()

        if self.agent:
            self.agent.action_requested.connect(self._on_agent_action)
            self.agent.speak_requested.connect(self._on_agent_speech)
            self.agent.view_ready.connect(self._on_view_ready)
            self.agent.view_error.connect(self._on_view_error)

        self.setWindowTitle("DeskPet 调试面板")
        self.setMinimumWidth(680)
        self._setup_ui()

        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start(2000)

    def _setup_ui(self):
        root = QHBoxLayout(self)

        left = QVBoxLayout()
        right = QVBoxLayout()
        root.addLayout(left, 1)
        root.addLayout(right, 1)

        # ── 左栏 ──

        anim_group = QGroupBox("动画测试")
        anim_layout = QVBoxLayout(anim_group)

        self._pet_btns: dict[str, QPushButton] = {}
        actions = self.pet.pet_anim.available_actions()
        if actions:
            btn_row = QHBoxLayout()
            for action in actions:
                btn = QPushButton(action)
                btn.setCheckable(True)
                btn.clicked.connect(lambda checked, a=action: self._play_pet_anim(a))
                btn_row.addWidget(btn)
                self._pet_btns[action] = btn
            anim_layout.addLayout(btn_row)
        else:
            anim_layout.addWidget(QLabel("⚠ 未找到可用帧动画（assets/actions/ 下无帧图片）"))

        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(QLabel("FPS:"))
        self.pet_fps = QSpinBox()
        self.pet_fps.setRange(1, 60)
        self.pet_fps.setValue(config.PET_FPS)
        self.pet_fps.setFixedWidth(150)
        ctrl_row.addWidget(self.pet_fps)
        self.pet_loop = QCheckBox("循环")
        self.pet_loop.setChecked(True)
        ctrl_row.addWidget(self.pet_loop)
        self.btn_pet_stop = QPushButton("停止")
        self.btn_pet_stop.clicked.connect(self._stop_pet_anim)
        ctrl_row.addWidget(self.btn_pet_stop)
        ctrl_row.addStretch()
        anim_layout.addLayout(ctrl_row)

        status_row = QFormLayout()
        self.label_pet_action = QLabel("—")
        self.label_pet_status = QLabel("—")
        status_row.addRow("当前动作:", self.label_pet_action)
        status_row.addRow("播放状态:", self.label_pet_status)
        anim_layout.addLayout(status_row)

        self.pet.pet_anim.animation_finished.connect(self._on_pet_anim_finished)
        self.pet.action_queue.changed.connect(self._refresh_queue_list)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        anim_layout.addWidget(sep)

        win_row = QHBoxLayout()
        self.bounce_dx = QSpinBox()
        self.bounce_dx.setRange(-3000, 3000)
        self.bounce_dx.setValue(0)
        self.bounce_dx.setPrefix("dx:")
        self.bounce_dx.setFixedWidth(90)
        win_row.addWidget(self.bounce_dx)
        self.bounce_dy = QSpinBox()
        self.bounce_dy.setRange(-3000, 3000)
        self.bounce_dy.setValue(-150)
        self.bounce_dy.setPrefix("dy:")
        self.bounce_dy.setFixedWidth(90)
        win_row.addWidget(self.bounce_dy)
        self.btn_bounce = QPushButton("弹跳")
        self.btn_bounce.clicked.connect(self._test_bounce)
        win_row.addWidget(self.btn_bounce)
        self.btn_fade_in = QPushButton("淡入 (Fade In)")
        self.btn_fade_in.clicked.connect(self._test_fade_in)
        win_row.addWidget(self.btn_fade_in)
        self.btn_fade_out = QPushButton("淡出 (Fade Out)")
        self.btn_fade_out.clicked.connect(self._test_fade_out)
        win_row.addWidget(self.btn_fade_out)
        anim_layout.addLayout(win_row)

        move_row = QHBoxLayout()
        move_row.addWidget(QLabel("移动到:"))
        self.move_x = QSpinBox()
        self.move_x.setRange(0, 3000)
        self.move_x.setValue(500)
        move_row.addWidget(self.move_x)
        self.move_y = QSpinBox()
        self.move_y.setRange(0, 2000)
        self.move_y.setValue(300)
        move_row.addWidget(self.move_y)
        self.btn_move = QPushButton("移动")
        self.btn_move.clicked.connect(self._test_move)
        move_row.addWidget(self.btn_move)
        anim_layout.addLayout(move_row)

        walk_row = QHBoxLayout()
        walk_row.addWidget(QLabel("方向:"))
        self.walk_dir = QComboBox()
        self.walk_dir.addItems(["left", "right"])
        self.walk_dir.setCurrentText("right")
        walk_row.addWidget(self.walk_dir)
        walk_row.addWidget(QLabel("距离:"))
        self.walk_dist = QSpinBox()
        self.walk_dist.setRange(0, 3000)
        self.walk_dist.setValue(800)
        walk_row.addWidget(self.walk_dist)
        self.btn_walk = QPushButton("行走")
        self.btn_walk.clicked.connect(self._test_walk)
        walk_row.addWidget(self.btn_walk)
        anim_layout.addLayout(walk_row)

        left.addWidget(anim_group)

        # ── 队列调试 ──

        queue_group = QGroupBox("行为队列")
        qlayout = QVBoxLayout(queue_group)

        qbtn_row = QHBoxLayout()
        self.btn_q_start = QPushButton("启动")
        self.btn_q_start.clicked.connect(self._queue_start)
        qbtn_row.addWidget(self.btn_q_start)
        self.btn_q_stop = QPushButton("停止")
        self.btn_q_stop.clicked.connect(self._queue_stop)
        qbtn_row.addWidget(self.btn_q_stop)
        self.btn_q_clear = QPushButton("清空")
        self.btn_q_clear.clicked.connect(self._queue_clear)
        qbtn_row.addWidget(self.btn_q_clear)
        qlayout.addLayout(qbtn_row)

        self.qlist = QListWidget()
        self.qlist.setMaximumHeight(120)
        self.qlist.setFont(QFont("Consolas", 10))
        qlayout.addWidget(self.qlist)

        left.addWidget(queue_group)

        # ── 右栏 ──

        bubble_group = QGroupBox("气泡测试")
        bubble_layout = QVBoxLayout(bubble_group)

        input_row = QHBoxLayout()
        self.bubble_input = QLineEdit()
        self.bubble_input.setPlaceholderText("输入气泡文字...")
        self.bubble_input.returnPressed.connect(self._test_bubble)
        input_row.addWidget(self.bubble_input)
        self.btn_bubble = QPushButton("显示气泡")
        self.btn_bubble.clicked.connect(self._test_bubble)
        input_row.addWidget(self.btn_bubble)
        bubble_layout.addLayout(input_row)

        right.addWidget(bubble_group)

        chat_group = QGroupBox("Chat 调试")
        chat_layout = QVBoxLayout(chat_group)

        chat_input_row = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("输入消息...")
        self.chat_input.returnPressed.connect(self._test_chat_think)
        chat_input_row.addWidget(self.chat_input)
        self.btn_chat_send = QPushButton("发送")
        self.btn_chat_send.clicked.connect(self._test_chat_think)
        chat_input_row.addWidget(self.btn_chat_send)
        chat_layout.addLayout(chat_input_row)

        chat_btn_row = QHBoxLayout()
        self.btn_chat_greet = QPushButton("问候")
        self.btn_chat_greet.clicked.connect(self._test_chat_greet)
        chat_btn_row.addWidget(self.btn_chat_greet)
        self.btn_chat_add_ctx = QPushButton("添加上下文")
        self.btn_chat_add_ctx.clicked.connect(self._chat_add_context)
        chat_btn_row.addWidget(self.btn_chat_add_ctx)
        self.btn_chat_clr_ctx = QPushButton("清除上下文")
        self.btn_chat_clr_ctx.clicked.connect(self._chat_clear_context)
        chat_btn_row.addWidget(self.btn_chat_clr_ctx)
        chat_layout.addLayout(chat_btn_row)

        self.label_chat_ctx = QLabel("上下文: 0 条")
        chat_layout.addWidget(self.label_chat_ctx)

        self.chat_output = QTextEdit()
        self.chat_output.setReadOnly(True)
        self.chat_output.setMaximumHeight(120)
        self.chat_output.setFont(QFont("Microsoft YaHei", 10))
        chat_layout.addWidget(self.chat_output)

        right.addWidget(chat_group)

        decide_group = QGroupBox("Behavior 决策")
        decide_layout = QVBoxLayout(decide_group)

        decide_btn_row = QHBoxLayout()
        self.btn_decide = QPushButton("decide()")
        self.btn_decide.clicked.connect(self._test_decide)
        decide_btn_row.addWidget(self.btn_decide)
        if self.agent:
            self.chk_tick = QCheckBox("Tick")
            self.agent.scheduler.stop()
            self.chk_tick.setChecked(False)
            self.chk_tick.toggled.connect(self._toggle_tick)
            decide_btn_row.addWidget(self.chk_tick)
            self.label_tick_state = QLabel("已停")
            decide_btn_row.addWidget(self.label_tick_state)
        decide_btn_row.addStretch()
        decide_layout.addLayout(decide_btn_row)

        self.decide_output = QTextEdit()
        self.decide_output.setReadOnly(True)
        self.decide_output.setMaximumHeight(80)
        self.decide_output.setFont(QFont("Consolas", 10))
        decide_layout.addWidget(self.decide_output)

        right.addWidget(decide_group)

        stats_group = QGroupBox("系统监控")
        stats_layout = QFormLayout(stats_group)
        self.label_cpu = QLabel("--")
        self.label_mem = QLabel("--")
        self.label_pet_pos = QLabel("--")
        self.label_pet_visible = QLabel("--")
        stats_layout.addRow("CPU:", self.label_cpu)
        stats_layout.addRow("内存:", self.label_mem)
        stats_layout.addRow("宠物位置:", self.label_pet_pos)
        stats_layout.addRow("宠物可见:", self.label_pet_visible)
        right.addWidget(stats_group)

        # ── 左栏（续）──

        view_group = QGroupBox("View 调试")
        view_layout = QVBoxLayout(view_group)

        capture_row = QHBoxLayout()
        self.btn_capture = QPushButton("截取全屏")
        self.btn_capture.clicked.connect(self._test_view_capture)
        capture_row.addWidget(self.btn_capture)
        self.label_screenshot = QLabel("未截图")
        capture_row.addWidget(self.label_screenshot)
        view_layout.addLayout(capture_row)

        view_input_row = QHBoxLayout()
        self.view_input = QLineEdit()
        self.view_input.setPlaceholderText("输入分析问题（可选）...")
        self.view_input.returnPressed.connect(self._test_view_analyze)
        view_input_row.addWidget(self.view_input)
        self.btn_view_analyze = QPushButton("分析")
        self.btn_view_analyze.clicked.connect(self._test_view_analyze)
        self.btn_view_analyze.setEnabled(False)
        view_input_row.addWidget(self.btn_view_analyze)
        view_layout.addLayout(view_input_row)

        self.view_output = QTextEdit()
        self.view_output.setReadOnly(True)
        self.view_output.setMaximumHeight(100)
        self.view_output.setFont(QFont("Microsoft YaHei", 10))
        view_layout.addWidget(self.view_output)

        left.addWidget(view_group)

        log_group = QGroupBox("日志")
        log_layout = QVBoxLayout(log_group)
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(150)
        self.log_output.setFont(QFont("Consolas", 9))
        log_layout.addWidget(self.log_output)
        left.addWidget(log_group)

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_output.append(f"[{ts}] {msg}")

    def _test_bounce(self):
        self.pet.show()
        dx, dy = self.bounce_dx.value(), self.bounce_dy.value()
        self._log(f"↩ enqueue bounce(dx={dx}, dy={dy})")
        self.pet.queue_enqueue("bounce", dx=dx, dy=dy)

    def _test_fade_in(self):
        self._log("↩ enqueue fade_in()")
        self.pet.queue_enqueue("fade_in")

    def _test_fade_out(self):
        self._log("↩ enqueue fade_out()")
        self.pet.queue_enqueue("fade_out", callback=self.pet.hide)

    def _test_move(self):
        self.pet.show()
        start = self.pet.pos()
        end = QPoint(self.move_x.value(), self.move_y.value())
        self._log(f"↩ enqueue move_to from ({start.x()},{start.y()}) → ({end.x()},{end.y()})")
        self.pet.queue_enqueue("move_to", start, end)

    def _test_walk(self):
        self.pet.show()
        direction = self.walk_dir.currentText()
        distance = self.walk_dist.value()
        self._log(f"↩ enqueue walk {direction} {distance}px")
        self.pet.queue_enqueue("walk", direction, distance)

    def _play_pet_anim(self, action: str):
        loop = self.pet_loop.isChecked()
        fps = self.pet_fps.value()
        ok = self.pet.pet_anim.play(action, loop=loop, fps=fps)
        if ok:
            self._log(f"pet_anim.play('{action}', loop={loop}, fps={fps})")
            self._update_pet_anim_status(action)
            for a, btn in self._pet_btns.items():
                btn.setChecked(a == action)
        else:
            self._log(f"⚠ 动作 '{action}' 无可用帧")

    def _stop_pet_anim(self):
        self.pet.pet_anim.stop()
        self._log("pet_anim.stop()")
        self.label_pet_status.setText("已停止")
        for btn in self._pet_btns.values():
            btn.setChecked(False)

    def _on_pet_anim_finished(self, action: str):
        self._log(f"动作 '{action}' 播放完成")
        self.label_pet_status.setText("已完成")
        for btn in self._pet_btns.values():
            btn.setChecked(False)

    def _update_pet_anim_status(self, action: str):
        loop = self.pet_loop.isChecked()
        fps = self.pet_fps.value()
        self.label_pet_action.setText(action)
        mode = "循环" if loop else "单次"
        self.label_pet_status.setText(f"播放中 · {fps} FPS · {mode}")

    # ── 队列调试 ──

    def _queue_start(self):
        self.pet.queue_start()
        self._log("队列: 启动")

    def _queue_stop(self):
        self.pet.queue_stop()
        self._log("队列: 停止")

    def _queue_clear(self):
        self.pet.queue_clear()
        self._log("队列: 清空")

    def _refresh_queue_list(self):
        self.qlist.clear()
        for line in self.pet.action_queue.describe():
            self.qlist.addItem(line)

    def _test_bubble(self):
        text = self.bubble_input.text().strip() or "调试中..."
        pet_center = self.pet.geometry().center()
        self._log(f"bubble: \"{text[:30]}\"")
        self.bubble.show_text(text, duration=4000, parent_pos=pet_center)

    def _on_agent_action(self, action: str):
        self._log(f"Agent → action: {action}")
        self.decide_output.append(f"Action: {action}")

    def _on_agent_speech(self, text: str, duration: int):
        self._log(f"Agent → speech: \"{text[:50]}\"")
        self.chat_output.append(f"<<< {text}")

    def _test_chat_think(self):
        prompt = self.chat_input.text().strip()
        if not prompt:
            return
        self._log(f"chat.think(\"{prompt[:40]}\")")
        self.chat_output.append(f">>> {prompt}")
        if self.agent:
            self.agent.trigger("think", prompt=prompt)
        else:
            reply = self.brain.think(prompt)
            self.chat_output.append(f"<<< {reply}")

    def _test_chat_greet(self):
        self._log("chat.greet()")
        self.chat_output.append(">>> greet()")
        if self.agent:
            self.agent.trigger("greet")
        else:
            reply = self.brain.greet()
            self.chat_output.append(f"<<< {reply}")

    def _test_decide(self):
        self._log("behavior.decide()")
        self.decide_output.clear()
        if self.agent:
            self.agent.trigger("decide")
        else:
            result = self.brain.decide()
            self._log(f"  ↳ action={result.action}, speech={result.speech}")
            self.decide_output.append(f"Action: {result.action}")
            self.decide_output.append(f"Speech: {result.speech or '(none)'}")

    def _toggle_tick(self, enabled: bool):
        if not self.agent:
            return
        if enabled:
            self.agent.scheduler.start()
            self._log("Tick: ON")
        else:
            self.agent.scheduler.stop()
            self._log("Tick: OFF")

    def _chat_add_context(self):
        text = self.chat_input.text().strip()
        if text:
            self.brain.add_context(text)
            self.chat_input.clear()
            self._chat_update_context_label()
            self._log(f"上下文 +1: \"{text[:30]}\"")

    def _chat_clear_context(self):
        self.brain.clear_context()
        self._chat_update_context_label()
        self._log("上下文已清除")

    def _chat_update_context_label(self):
        n = len(self.brain._context)
        self.label_chat_ctx.setText(f"上下文: {n} 条")

    def _test_view_capture(self):
        self._log("截取全屏...")
        self._last_screenshot = self.screen_reader.capture_fullscreen()
        if self._last_screenshot:
            w, h = self._last_screenshot.size
            self.label_screenshot.setText(f"已截图: {w}×{h}")
            self.btn_view_analyze.setEnabled(True)
            self._log(f"截图成功: {w}×{h}")
        else:
            self.label_screenshot.setText("截图失败")
            self._log("截图失败")

    def _test_view_analyze(self):
        if not hasattr(self, '_last_screenshot') or self._last_screenshot is None:
            self.view_output.setText("请先截取屏幕")
            return
        prompt = self.view_input.text().strip()
        self._log(f"view.analyze(\"{prompt[:30]}\")")
        self.view_output.clear()
        self.view_output.append("分析中...")
        if self.agent:
            self.agent.trigger("view", image=self._last_screenshot, prompt=prompt)
        else:
            reply = self.view_brain.analyze(self._last_screenshot, prompt)
            self._on_view_ready(reply)

    def _on_view_ready(self, reply: str):
        self._log(f"  ↳ view: {reply}")
        self.view_output.clear()
        self.view_output.append(reply)

    def _on_view_error(self, msg: str):
        self._log(f"  ↳ VIEW ERROR: {msg}")
        self.view_output.clear()
        self.view_output.append(f"[Error] {msg}")

    def _refresh_stats(self):
        try:
            cpu = self.monitor.get_cpu_usage()
            self.label_cpu.setText(f"{cpu:.1f}%")
        except Exception:
            self.label_cpu.setText("N/A")

        try:
            mem = self.monitor.get_memory_usage()
            self.label_mem.setText(f"{mem:.1f}%")
        except Exception:
            self.label_mem.setText("N/A")

        pos = self.pet.pos()
        self.label_pet_pos.setText(f"({pos.x()}, {pos.y()})")
        self.label_pet_visible.setText("是" if self.pet.isVisible() else "否")

        if self.agent and hasattr(self, 'chk_tick'):
            running = self.agent.scheduler.is_running()
            self.chk_tick.setChecked(running)
            self.label_tick_state.setText("运行中" if running else "已停")

    def closeEvent(self, event):
        self._stats_timer.stop()
        super().closeEvent(event)
