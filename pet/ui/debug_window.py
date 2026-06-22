"""调试面板"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QGroupBox, QTextEdit, QLabel, QLineEdit,
    QFormLayout, QCheckBox, QFrame, QComboBox, QListWidget,
    QGridLayout,
)
from datetime import datetime

from PySide6.QtCore import Qt, QPoint, QTimer, QSize
from PySide6.QtGui import QFont, QIcon, QPainter, QPainterPath, QColor, QPen
from pet.ui.styles import (
    ICON_PATH, WINDOW_QSS, PANEL_QSS, BUTTON_QSS, BUTTON_PRIMARY_QSS,
    BUTTON_DANGER_QSS, INPUT_HIGHLIGHT_QSS, COMBOBOX_QSS, TEXTEDIT_QSS,
    LIST_QSS, CHECKBOX_QSS, LABEL_SECONDARY_QSS, LABEL_MONO_QSS,
    SCROLLBAR_QSS,
    _COLOR_BG, _COLOR_BORDER_DARK, _COLOR_TEXT_TITLE, _COLOR_TEXT_MUTED, _COLOR_DANGER,
    make_minimize_button, make_close_button, ensure_taskbar_icon,
)

from pet.ui.speech_bubble import SpeechBubble
from pet.ui.emotion import EmotionBubble, EMOTION_MAP
from pet.ui.particle import ParticleWidget
from pet.brain.behavior import Behavior
from config import config


class DebugWindow(QWidget):

    def __init__(self, pet_window, agent=None, parent=None):
        super().__init__(parent)
        self.pet = pet_window
        self.agent = agent
        self.bubble = SpeechBubble(self.pet)
        self.emotion_bubble = EmotionBubble(self.pet)
        if agent is not None:
            self.brain = agent.behavior
        else:
            self.brain = Behavior()

        if self.agent:
            self.agent.action_requested.connect(self._on_agent_action)
            self.agent.speak_requested.connect(self._on_agent_speech)
            self.agent.emotion_requested.connect(self._on_agent_emotion)

        self.setWindowTitle("DeskPet 调试面板")
        self.setObjectName("FlatWindow")
        self.setMinimumSize(1000, 850)
        self.setMaximumSize(1000, 850)
        self.resize(1000, 850)

        # 无边框 + 圆角
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        try:
            self.setWindowIcon(QIcon(ICON_PATH))
        except Exception:
            pass

        self._setup_ui()
        self.setStyleSheet(
            PANEL_QSS + BUTTON_QSS + BUTTON_PRIMARY_QSS +
            INPUT_HIGHLIGHT_QSS + COMBOBOX_QSS + TEXTEDIT_QSS + LIST_QSS +
            CHECKBOX_QSS
        )

        self._pos_timer = QTimer(self)
        self._pos_timer.timeout.connect(self._refresh_pos)
        self._pos_timer.timeout.connect(self._refresh_llm_stats)
        self._pos_timer.timeout.connect(self._refresh_context)
        self._pos_timer.start(1000)

    def showEvent(self, event):
        super().showEvent(event)
        ensure_taskbar_icon(self)
        if self.agent:
            try:
                ns = self.agent.vitals.numeric_summary()
                for key in ("satiety", "energy"):
                    self._param_inputs[key].setText(str(int(ns[key])))
                ms = self.agent.mood.numeric_summary()
                for key in ("affection", "joy", "sanity"):
                    self._param_inputs[key].setText(str(int(ms[key])))
            except Exception:
                pass

    def _refresh_pos(self):
        pos = self.pet.pos()
        self.label_pet_pos.setText(f"({pos.x()}, {pos.y()})")

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 2, 6, 4)
        root.setSpacing(2)

        # ── 自定义标题栏 ──
        header = QWidget()
        header.setObjectName("LogHeader")
        header.setFixedHeight(38)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 6, 0)
        header_layout.setSpacing(6)

        try:
            icon_lbl = QLabel()
            icon_lbl.setPixmap(QIcon(ICON_PATH).pixmap(18, 18))
            header_layout.addWidget(icon_lbl)
        except Exception:
            pass

        title_lbl = QLabel("DeskPet 调试面板")
        title_lbl.setStyleSheet(f"font-size:13px; color:{_COLOR_TEXT_TITLE}; font-weight:bold; background:transparent;")
        header_layout.addWidget(title_lbl)
        header_layout.addStretch()

        header_layout.addWidget(make_minimize_button(self))
        header_layout.addWidget(make_close_button(self))

        root.addWidget(header)

        # ── 拖拽 ──
        header.mousePressEvent = self._header_press
        header.mouseMoveEvent = self._header_move
        self._drag_pos: QPoint | None = None

        # ── 正文区域 ──
        body = QHBoxLayout()
        left = QVBoxLayout()
        right = QVBoxLayout()
        body.addLayout(left, 1)
        body.addLayout(right, 1)
        root.addLayout(body, 1)

        # ── 左栏 ──

        anim_group = QGroupBox("动画测试")
        anim_layout = QVBoxLayout(anim_group)

        self._pet_btns: dict[str, QPushButton] = {}
        actions = self.pet.pet_anim.available_actions()
        if actions:
            COLS = 4
            btn_grid = QGridLayout()
            for i, action in enumerate(actions):
                btn = QPushButton(action)
                btn.setToolTip(action)
                btn.setCheckable(True)
                btn.setMinimumWidth(60)
                btn.clicked.connect(lambda checked, a=action: self._play_pet_anim(a))
                btn_grid.addWidget(btn, i // COLS, i % COLS)
                self._pet_btns[action] = btn
            anim_layout.addLayout(btn_grid)
        else:
            anim_layout.addWidget(QLabel("⚠ 未找到可用帧动画（assets/actions/ 下无帧图片）"))

        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(QLabel("FPS:"))
        self.pet_fps = QLineEdit()
        self.pet_fps.setText(str(config.PET_FPS))
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
        self.label_pet_pos = QLabel("—")
        status_row.addRow("当前动作:", self.label_pet_action)
        status_row.addRow("播放状态:", self.label_pet_status)
        status_row.addRow("宠物位置:", self.label_pet_pos)
        anim_layout.addLayout(status_row)

        self.pet.pet_anim.animation_finished.connect(self._on_pet_anim_finished)
        self.pet.action_queue.changed.connect(self._refresh_queue_list)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        anim_layout.addWidget(sep)

        win_row = QHBoxLayout()
        self.bounce_dir = QComboBox()
        self.bounce_dir.addItems(["right", "left"])
        self.bounce_dir.setFixedWidth(60)
        win_row.addWidget(self.bounce_dir)
        self.bounce_dist = QLineEdit()
        self.bounce_dist.setPlaceholderText("d:400")
        self.bounce_dist.setText("400")
        self.bounce_dist.setFixedWidth(80)
        win_row.addWidget(self.bounce_dist)
        self.bounce_height = QLineEdit()
        self.bounce_height.setPlaceholderText("h:150")
        self.bounce_height.setText("150")
        self.bounce_height.setFixedWidth(90)
        win_row.addWidget(self.bounce_height)
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
        self.move_x = QLineEdit()
        self.move_x.setText("500")
        move_row.addWidget(self.move_x)
        self.move_y = QLineEdit()
        self.move_y.setText("300")
        move_row.addWidget(self.move_y)
        self.btn_move = QPushButton("移动")
        self.btn_move.clicked.connect(self._test_move)
        move_row.addWidget(self.btn_move)
        anim_layout.addLayout(move_row)

        walk_row = QHBoxLayout()
        walk_row.addWidget(QLabel("类型:"))
        self.walk_type = QComboBox()
        self.walk_type.addItems(["drive", "walk"])
        self.walk_type.setCurrentText("drive")
        walk_row.addWidget(self.walk_type)
        walk_row.addWidget(QLabel("方向:"))
        self.walk_dir = QComboBox()
        self.walk_dir.addItems(["left", "right"])
        self.walk_dir.setCurrentText("right")
        walk_row.addWidget(self.walk_dir)
        walk_row.addWidget(QLabel("距离:"))
        self.walk_dist = QLineEdit()
        self.walk_dist.setText("800")
        walk_row.addWidget(self.walk_dist)
        self.btn_walk = QPushButton("行走")
        self.btn_walk.clicked.connect(self._test_walk)
        walk_row.addWidget(self.btn_walk)
        anim_layout.addLayout(walk_row)

        pose_row = QHBoxLayout()
        pose_row.addWidget(QLabel("姿势:"))
        pose_row.addWidget(QLabel("时长:"))
        self.pose_duration = QLineEdit()
        self.pose_duration.setText("5")
        self.pose_duration.setFixedWidth(70)
        pose_row.addWidget(self.pose_duration)
        self.btn_sit = QPushButton("sit")
        self.btn_sit.clicked.connect(self._test_sit)
        pose_row.addWidget(self.btn_sit)
        self.btn_sleep = QPushButton("sleep")
        self.btn_sleep.clicked.connect(self._test_sleep)
        pose_row.addWidget(self.btn_sleep)
        self.btn_idle = QPushButton("idle")
        self.btn_idle.clicked.connect(self._test_idle)
        pose_row.addWidget(self.btn_idle)
        pose_row.addStretch()
        anim_layout.addLayout(pose_row)

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

        # ── 表情调试 ──

        emotion_group = QGroupBox("表情测试")
        emotion_layout = QVBoxLayout(emotion_group)

        EMO_COLS = 5
        emo_grid = QGridLayout()
        for i, emo_name in enumerate(EMOTION_MAP):
            btn = QPushButton(emo_name)
            btn.setToolTip(emo_name)
            btn.setMinimumWidth(54)
            btn.clicked.connect(lambda checked, e=emo_name: self._test_emotion(e))
            emo_grid.addWidget(btn, i // EMO_COLS, i % EMO_COLS)
        emotion_layout.addLayout(emo_grid)

        emo_input_row = QHBoxLayout()
        self.emotion_input = QLineEdit()
        self.emotion_input.setPlaceholderText("输入情绪名或 emoji...")
        self.emotion_input.returnPressed.connect(self._test_emotion_input)
        emo_input_row.addWidget(self.emotion_input)
        self.btn_emotion = QPushButton("显示表情")
        self.btn_emotion.clicked.connect(self._test_emotion_input)
        emo_input_row.addWidget(self.btn_emotion)
        emotion_layout.addLayout(emo_input_row)

        right.addWidget(emotion_group)

        # ── 生理/心理参数 ──

        param_group = QGroupBox("生理/心理参数")
        param_layout = QHBoxLayout(param_group)

        self._param_inputs: dict[str, QLineEdit] = {}
        for key, label in (("satiety","饱食度"), ("energy","精力"),
                           ("affection","好感度"), ("joy","愉悦度"), ("sanity","理智值")):
            col = QVBoxLayout()
            col.addWidget(QLabel(label))
            inp = QLineEdit("50")
            inp.setFixedWidth(60)
            inp.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col.addWidget(inp)
            param_layout.addLayout(col)
            self._param_inputs[key] = inp
        btn = QPushButton("设置")
        btn.setFixedWidth(50)
        btn.clicked.connect(self._set_all_params)
        param_layout.addWidget(btn)
        param_layout.addStretch()

        right.addWidget(param_group)

        # ── 粒子调试 ──

        particle_group = QGroupBox("粒子特效测试")
        particle_layout = QVBoxLayout(particle_group)

        pbtn_row = QHBoxLayout()
        for fx in ("dust", "stars", "zzz", "hearts"):
            btn = QPushButton(fx)
            btn.clicked.connect(lambda checked, e=fx: self._test_particle(e))
            pbtn_row.addWidget(btn)
        particle_layout.addLayout(pbtn_row)

        right.addWidget(particle_group)

        # ── 上下文存储 ──

        ctx_group = QGroupBox("上下文存储 (BrainMixin)")
        ctx_layout = QVBoxLayout(ctx_group)

        ctx_btn_row = QHBoxLayout()
        self.btn_ctx_refresh = QPushButton("刷新")
        self.btn_ctx_refresh.clicked.connect(self._refresh_context)
        ctx_btn_row.addWidget(self.btn_ctx_refresh)
        self.btn_ctx_clear = QPushButton("清空")
        self.btn_ctx_clear.clicked.connect(self._clear_context)
        ctx_btn_row.addWidget(self.btn_ctx_clear)
        self.label_ctx_count = QLabel("共 0 条")
        ctx_btn_row.addWidget(self.label_ctx_count)
        ctx_btn_row.addStretch()
        ctx_layout.addLayout(ctx_btn_row)

        self.ctx_output = QTextEdit()
        self.ctx_output.setReadOnly(True)
        self.ctx_output.setMaximumHeight(180)
        self.ctx_output.setFont(QFont("Consolas", 9))
        self.ctx_output.setStyleSheet(f"QTextEdit {{ background: {_COLOR_BG}; }}" + SCROLLBAR_QSS)
        ctx_layout.addWidget(self.ctx_output)

        right.addWidget(ctx_group)

        # ── LLM 调用统计 ──
        stats_layout = QHBoxLayout()
        self.label_llm_calls = QLabel("累计调用: —")
        self.label_llm_calls.setFont(QFont("Consolas", 9))
        self.label_llm_calls.setStyleSheet("color:#555;")
        stats_layout.addWidget(self.label_llm_calls)
        stats_layout.addStretch()
        right.addLayout(stats_layout)

        log_group = QGroupBox("日志")
        log_layout = QVBoxLayout(log_group)
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(150)
        self.log_output.setFont(QFont("Consolas", 9))
        self.log_output.setStyleSheet(f"QTextEdit {{ background: {_COLOR_BG}; }}" + SCROLLBAR_QSS)
        log_layout.addWidget(self.log_output)
        left.addWidget(log_group)

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_output.append(f"[{ts}] {msg}")

    def _test_bounce(self):
        self.pet.show()
        d, dist, h = self.bounce_dir.currentText(), int(self.bounce_dist.text()), int(self.bounce_height.text())
        self._log(f"↩ enqueue bounce(direction={d}, distance={dist}, height={h})")
        self.pet.queue_enqueue("bounce", direction=d, distance=dist, height=h)

    def _test_fade_in(self):
        self._log("↩ enqueue fade_in()")
        self.pet.queue_enqueue("fade_in")

    def _test_fade_out(self):
        self._log("↩ enqueue fade_out()")
        self.pet.queue_enqueue("fade_out", callback=self.pet.hide)

    def _test_move(self):
        self.pet.show()
        start = self.pet.pos()
        end = QPoint(int(self.move_x.text()), int(self.move_y.text()))
        self._log(f"↩ enqueue move_to from ({start.x()},{start.y()}) → ({end.x()},{end.y()})")
        self.pet.queue_enqueue("move_to", start, end)

    def _test_walk(self):
        self.pet.show()
        walk_type = self.walk_type.currentText()
        direction = self.walk_dir.currentText()
        distance = int(self.walk_dist.text())
        self._log(f"↩ enqueue {walk_type} {direction} {distance}px")
        self.pet.queue_enqueue(walk_type, direction, distance)

    def _test_sit(self):
        self.pet.show()
        t = int(self.pose_duration.text())
        self._log(f"↩ enqueue sit({t}s)")
        self.pet.queue_enqueue("sit", duration=t)

    def _test_sleep(self):
        self.pet.show()
        t = int(self.pose_duration.text())
        self._log(f"↩ enqueue sleep({t}s)")
        self.pet.queue_enqueue("sleep", duration=t)

    def _test_idle(self):
        self.pet.show()
        self._log("↩ direct idle (not queued)")
        self.pet.pet_actions.idle()

    def _play_pet_anim(self, action: str):
        loop = self.pet_loop.isChecked()
        duration = int(self.pose_duration.text()) if loop else None
        ok = self.pet.pet_anim.play(action, duration=duration)
        if ok:
            self._log(f"pet_anim.play('{action}', duration={duration})")
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
        fps = int(self.pet_fps.text())
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

    def _on_agent_action(self, action: str, args=(), kwargs=None):
        kwargs = kwargs or {}
        params = ""
        if args:
            params += f" {', '.join(str(a) for a in args)}"
        if kwargs:
            params += f" {', '.join(f'{k}={v}' for k, v in kwargs.items())}"
        self._log(f"Agent → action: {action}{params}")

    def _on_agent_speech(self, text: str, duration: int):
        self._log(f"Agent → speech: \"{text[:50]}\"")

    def _on_agent_emotion(self, emotion: str, duration: int):
        self._log(f"Agent → emotion: {emotion} ({duration}ms)")

    def _test_emotion(self, emotion: str):
        pet_center = self.pet.geometry().center()
        self._log(f"emotion: \"{emotion}\"")
        self.emotion_bubble.show_emotion(emotion, duration=3000, parent_pos=pet_center)

    def _test_emotion_input(self):
        emotion = self.emotion_input.text().strip() or "happy"
        self._test_emotion(emotion)

    # ── 生理/心理参数 ──

    def _set_all_params(self):
        vitals = self.agent.vitals if self.agent else None
        mood = self.agent.mood if self.agent else None
        if not vitals or not mood:
            return
        try:
            for key in ("satiety", "energy"):
                getattr(vitals, f"set_{key}")(int(self._param_inputs[key].text()))
            for key in ("affection", "joy", "sanity"):
                getattr(mood, f"set_{key}")(int(self._param_inputs[key].text()))
            self._log("参数设置成功")
        except ValueError:
            self._log("⚠ 参数设置失败：请输入有效整数")

    def _test_particle(self, effect: str):
        self._log(f"particle: \"{effect}\"")
        self.pet.particles.spawn(effect)

    def _refresh_context(self):
        scroll_bar = self.ctx_output.verticalScrollBar()
        prev_scroll = scroll_bar.value()
        prev_max = scroll_bar.maximum()

        self.ctx_output.clear()
        if not self.agent or not hasattr(self.agent.behavior, '_context'):
            self.ctx_output.append("（无 agent 或 Behavior 不可用）")
            self.label_ctx_count.setText("共 0 条")
            return
        entries = self.agent.behavior._context
        self.label_ctx_count.setText(f"共 {len(entries)} 条")
        if not entries:
            self.ctx_output.append("（空）")
            return
        now = __import__('time').time()
        for i, e in enumerate(entries, 1):
            age = int(now - e.timestamp)
            age_str = f"{age}s" if age < 60 else f"{age // 60}m{age % 60}s"
            label = {"user": "用户", "assistant": "宠物", "system": "系统"}.get(e.role, e.role)
            prefix = "★摘要" if e.is_summary else label
            score = self.agent.behavior._score_entry(e) if hasattr(self.agent.behavior, '_score_entry') else 0
            self.ctx_output.append(
                f"#{i} [{prefix}] {score:.1f}分 | {age_str}前\n  {e.content[:120]}"
            )

        # 内容追加后恢复滚动位置，新内容追加到底部时不跳转
        if prev_max == scroll_bar.maximum():
            scroll_bar.setValue(prev_scroll)

    def _clear_context(self):
        if self.agent and hasattr(self.agent.behavior, 'clear_context'):
            self.agent.behavior.clear_context()
            self._refresh_context()

    # ── 圆角背景 ──

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        painter.fillPath(path, QColor(_COLOR_BG))
        painter.setPen(QPen(QColor("#000000"), 1))
        painter.drawPath(path)

    # ── 标题栏拖拽 ──

    def _header_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()

    def _header_move(self, event):
        if self._drag_pos is not None:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()

    def closeEvent(self, event):
        self._pos_timer.stop()
        super().closeEvent(event)

    def _refresh_llm_stats(self):
        if hasattr(self.brain, 'llm_stats') and self.brain.llm_stats:
            self.label_llm_calls.setText(f"累计调用: {self.brain.llm_stats.total} 次")
