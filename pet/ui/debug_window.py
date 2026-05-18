from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QGroupBox, QTextEdit, QLabel, QLineEdit, QSpinBox,
    QFormLayout,
)
from datetime import datetime

from PySide6.QtCore import Qt, QPoint, QTimer, QThread, Signal
from PySide6.QtGui import QFont

from pet.ui.animations import AnimationManager
from pet.ui.bubble import SpeechBubble
from pet.brain.openai_brain import OpenAIBrain
from pet.skills.system_monitor import SystemMonitor


class ApiWorker(QThread):
    """在工作线程执行 API 调用，通过信号回传结果"""
    result_ready = Signal(str)   # 成功时发射回复文本
    error_occurred = Signal(str) # 失败时发射错误信息

    def __init__(self, callable_fn, *args, parent=None):
        super().__init__(parent)
        self._fn = callable_fn
        self._args = args

    def run(self):
        try:
            result = self._fn(*self._args)
            self.result_ready.emit(result)
        except Exception as e:
            self.error_occurred.emit(str(e))


class DebugWindow(QWidget):
    """调试窗口 — 用于测试桌宠的各项行为"""

    def __init__(self, pet_window, parent=None):
        super().__init__(parent)
        self.pet = pet_window
        self.anim = AnimationManager(self)
        self.bubble = SpeechBubble(self.pet)
        self.brain = OpenAIBrain()
        self.monitor = SystemMonitor()
        self.monitor.enable()

        self.setWindowTitle("DeskPet 调试面板")
        self.setMinimumWidth(420)
        self._setup_ui()

        # 实时刷新监控数据
        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start(2000)

    def _setup_ui(self):
        root = QVBoxLayout(self)

        # ── 动画测试 ──
        anim_group = QGroupBox("动画测试")
        anim_layout = QVBoxLayout(anim_group)

        row1 = QHBoxLayout()
        self.btn_bounce = QPushButton("弹跳 (Bounce)")
        self.btn_bounce.clicked.connect(self._test_bounce)
        row1.addWidget(self.btn_bounce)

        self.btn_fade_in = QPushButton("淡入 (Fade In)")
        self.btn_fade_in.clicked.connect(self._test_fade_in)
        row1.addWidget(self.btn_fade_in)

        self.btn_fade_out = QPushButton("淡出 (Fade Out)")
        self.btn_fade_out.clicked.connect(self._test_fade_out)
        row1.addWidget(self.btn_fade_out)
        anim_layout.addLayout(row1)

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

        root.addWidget(anim_group)

        # ── 气泡测试 ──
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

        root.addWidget(bubble_group)

        # ── AI 大脑测试 ──
        brain_group = QGroupBox("AI 大脑测试")
        brain_layout = QVBoxLayout(brain_group)

        brain_input_row = QHBoxLayout()
        self.brain_input = QLineEdit()
        self.brain_input.setPlaceholderText("输入 prompt...")
        self.brain_input.returnPressed.connect(self._test_think)
        brain_input_row.addWidget(self.brain_input)
        self.btn_think = QPushButton("Think")
        self.btn_think.clicked.connect(self._test_think)
        brain_input_row.addWidget(self.btn_think)
        self.btn_greet = QPushButton("Greet")
        self.btn_greet.clicked.connect(self._test_greet)
        brain_input_row.addWidget(self.btn_greet)
        brain_layout.addLayout(brain_input_row)

        self.brain_output = QTextEdit()
        self.brain_output.setReadOnly(True)
        self.brain_output.setMaximumHeight(100)
        self.brain_output.setFont(QFont("Microsoft YaHei", 10))
        brain_layout.addWidget(self.brain_output)

        root.addWidget(brain_group)

        # ── 系统监控 ──
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
        root.addWidget(stats_group)

        # ── 宠物显隐 ──
        toggle_row = QHBoxLayout()
        self.btn_toggle = QPushButton("切换显隐")
        self.btn_toggle.clicked.connect(self._toggle_pet)
        toggle_row.addWidget(self.btn_toggle)
        root.addLayout(toggle_row)

        # ── 日志 ──
        log_group = QGroupBox("日志")
        log_layout = QVBoxLayout(log_group)
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(150)
        self.log_output.setFont(QFont("Consolas", 9))
        log_layout.addWidget(self.log_output)
        root.addWidget(log_group)

        root.addStretch()

    # ── 日志 ──
    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_output.append(f"[{ts}] {msg}")

    # ── 动画 ──
    def _test_bounce(self):
        self.pet.show()
        self._log(f"bounce() from {self.pet.pos().toTuple()}")
        self.anim.bounce(self.pet)

    def _test_fade_in(self):
        self.pet.setWindowOpacity(0.0)
        self.pet.show()
        self._log("fade_in()")
        self.anim.fade_in(self.pet)

    def _test_fade_out(self):
        self._log("fade_out() → hide after")
        self.anim.fade_out(self.pet, callback=self.pet.hide)

    def _test_move(self):
        self.pet.show()
        start = self.pet.pos().toTuple()
        end = (self.move_x.value(), self.move_y.value())
        self._log(f"move_to() from {start} → {end}")
        self.anim.move_to(
            self.pet,
            self.pet.pos(),
            QPoint(*end),
        )

    # ── 气泡 ──
    def _test_bubble(self):
        text = self.bubble_input.text().strip() or "汪汪！调试中..."
        pet_center = self.pet.geometry().center()
        self._log(f"bubble: \"{text[:30]}\"")
        self.bubble.show_text(text, duration=4000, parent_pos=pet_center)

    # ── 大脑 ──
    def _test_think(self):
        prompt = self.brain_input.text().strip() or "Say something cute!"
        self._log(f"brain.think(\"{prompt[:40]}\")")
        self.brain_output.append(f">>> {prompt}")
        self._set_brain_buttons_enabled(False)
        self._worker = ApiWorker(self.brain.think, prompt)
        #信号槽连接
        self._worker.result_ready.connect(self._on_think_result)
        self._worker.error_occurred.connect(self._on_think_error)
        self._worker.finished.connect(lambda: self._set_brain_buttons_enabled(True))
        self._worker.start()

    def _on_think_result(self, reply: str):
        self._log(f"  ↳ {reply}")
        self.brain_output.append(f"<<< {reply}")

    def _on_think_error(self, error: str):
        self._log(f"  ↳ ERROR: {error}")
        self.brain_output.append(f"[Error] {error}")

    def _test_greet(self):
        self._log("brain.greet()")
        self.brain_output.append(">>> greet()")
        self._set_brain_buttons_enabled(False)
        self._worker = ApiWorker(self.brain.greet)
        self._worker.result_ready.connect(self._on_greet_result)
        self._worker.error_occurred.connect(self._on_greet_error)
        self._worker.finished.connect(lambda: self._set_brain_buttons_enabled(True))
        self._worker.start()

    def _on_greet_result(self, reply: str):
        self._log(f"  ↳ {reply}")
        self.brain_output.append(f"<<< {reply}")

    def _on_greet_error(self, error: str):
        self._log(f"  ↳ ERROR: {error}")
        self.brain_output.append(f"[Error] {error}")

    def _set_brain_buttons_enabled(self, enabled: bool):
        self.btn_think.setEnabled(enabled)
        self.btn_greet.setEnabled(enabled)
        self.brain_input.setEnabled(enabled)

    # ── 显隐 ──
    def _toggle_pet(self):
        visible = not self.pet.isVisible()
        self._log(f"toggle visibility → {'show' if visible else 'hide'}")
        self.pet.setVisible(visible)

    # ── 监控刷新 ──
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

    def closeEvent(self, event):
        """关闭调试窗口时停止定时器"""
        self._stats_timer.stop()
        super().closeEvent(event)
