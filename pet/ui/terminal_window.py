"""终端窗口：装配回显视图、输入控制器、状态点与执行线程的信号桥。

文档块渲染在 pet/ui/terminal_view.py，输入交互在 pet/ui/terminal_input.py；
这里只保留布局、状态点、提交分发与跨线程信号。
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid

from PySide6.QtCore import QPoint, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                               QWidget)

from pet import cli
from pet.terminal import core, policy
from pet.terminal.events import (CommandSource, EventKind, Phase,
                                 TerminalEvent)
from pet.terminal.runner import get_runner
from pet.ui.styles import (ICON_PATH, TERMINAL_ACCENT, TERMINAL_ACCENT_DIM,
                           TERMINAL_BG, TERMINAL_BORDER, TERMINAL_CONTROL_RADIUS,
                           TERMINAL_DANGER, TERMINAL_HOVER, TERMINAL_IDLE,
                           TERMINAL_MUTED, TERMINAL_MUTED_LIGHT, TERMINAL_PANEL,
                           TERMINAL_TEXT, TERMINAL_USER, TERMINAL_WINDOW_RADIUS,
                           ensure_taskbar_icon, terminal_mono_font,
                           terminal_ui_font)
from pet.ui.terminal_input import InputController, InputLine, cwd_prompt
from pet.ui.terminal_view import TerminalView
from pet.ui.widgets import NoWheelComboBox

logger = logging.getLogger(__name__)

_BREATH_MS = 600

PASTE_TRUNCATED_NOTICE = "已忽略后续行"
BUSY_NOTICE = "上一条命令还在执行"
PHASE_TEXT = {"waiting": "等待执行", "running": "运行中"}


def _title_button(glyph: str, on_click) -> QPushButton:
    """标题栏按钮：默认灰字透明底，hover 只加深底色与字色。"""
    button = QPushButton(glyph)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFixedSize(24, 24)
    button.setFont(terminal_ui_font(11))
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setStyleSheet(
        f"QPushButton {{ color:{TERMINAL_MUTED}; background:transparent; border:none;"
        f" border-radius:{TERMINAL_CONTROL_RADIUS}px; }}"
        f"QPushButton:hover {{ background:{TERMINAL_HOVER}; color:{TERMINAL_TEXT}; }}"
        f"QPushButton:pressed {{ background:{TERMINAL_BORDER}; }}"
    )
    button.clicked.connect(on_click)
    return button


def resolve_initial_cwd() -> str:
    """初始目录取工作目录根，未配置时为桌面。"""
    root = policy.sandbox_root()
    if root and os.path.isdir(root):
        return root
    return os.getcwd()


def cd_target(text: str) -> str | None:
    """识别「cd 单个目录参数」的输入，返回目录参数；其它写法返回 None。"""
    stripped = (text or "").strip()
    if not stripped.startswith("cd "):
        return None
    argument = stripped[3:].strip()
    if not argument or len(argument.split()) != 1:
        return None
    if argument[0] in "\"'":
        return None
    return argument


_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:$")


def drive_target(text: str) -> str | None:
    """识别裸盘符（cmd 的 `D:`），返回该盘根；其它写法返回 None。"""
    if os.name != "nt":
        return None
    value = str(text or "").strip().strip('"')
    if _DRIVE_PATTERN.match(value):
        return value.upper() + os.sep
    return None


class _WindowControl:
    """终端命令到应用控制的桥：只发信号，实际动作回主线程执行。"""

    def __init__(self, window: "TerminalWindow"):
        self._window = window

    def quit(self) -> bool:
        return self._window.request_quit()

    def restart(self) -> bool:
        return self._window.request_restart()


class _Combo(QWidget):
    """状态栏用的小下拉框，无滚轮响应。"""

    def __init__(self, items: list[str], parent=None):
        super().__init__(parent)
        self._combo = NoWheelComboBox(self)
        self._combo.addItems(items)
        self.currentTextChanged = self._combo.currentTextChanged
        self._combo.setFont(terminal_mono_font(10))
        self._combo.setStyleSheet(
            f"QComboBox {{ color:{TERMINAL_MUTED_LIGHT}; background:{TERMINAL_PANEL};"
            f" border:1px solid {TERMINAL_BORDER}; border-radius:{TERMINAL_CONTROL_RADIUS}px;"
            f" padding:2px 6px; }}"
            f"QComboBox::drop-down {{ border:none; width:12px; }}"
            f"QComboBox QAbstractItemView {{ background:{TERMINAL_PANEL}; color:{TERMINAL_TEXT};"
            f" border:1px solid {TERMINAL_BORDER}; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._combo)

    def currentText(self) -> str:
        return self._combo.currentText()


class TerminalWindow(QWidget):
    """终端窗口：用户输入直接执行，模型执行的命令同步回显。"""

    run_event = Signal(object)
    run_finished = Signal(object)
    local_finished = Signal(object)
    quit_requested = Signal()
    restart_requested = Signal()

    def __init__(self, runner=None, quit_fn=None, restart_fn=None, parent=None):
        super().__init__(parent)
        self._runner = runner if runner is not None else get_runner()
        self._quit_fn = quit_fn
        self._restart_fn = restart_fn
        self._control = _WindowControl(self)
        self.quit_requested.connect(self._on_quit_requested)
        self.restart_requested.connect(self._on_restart_requested)
        self._cwd = resolve_initial_cwd()
        self._busy = False
        self._started_at = 0.0
        self._follow = True
        self._phase = Phase.IDLE.value
        self._breath_on = False
        self._drag_origin: QPoint | None = None

        self.setWindowTitle("终端")
        self.setMinimumSize(520, 360)
        self.resize(680, 480)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        try:
            self.setWindowIcon(QIcon(ICON_PATH))
        except Exception:
            pass

        self._dot = QLabel()
        self._dot.setFixedSize(8, 8)
        self._set_dot(TERMINAL_IDLE)
        self._title = QLabel("终端")
        self._title.setFont(terminal_ui_font(12, bold=True))
        self._title.setStyleSheet(f"color:{TERMINAL_TEXT};")
        self._shell_combo = _Combo([value for value in core.shell_choices() if value])

        clear_btn = QPushButton("清空")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setFont(terminal_ui_font(11))
        clear_btn.setStyleSheet(
            f"QPushButton {{ color:{TERMINAL_MUTED}; background:transparent; border:none; }}"
            f"QPushButton:hover {{ color:{TERMINAL_TEXT}; }}"
        )
        clear_btn.clicked.connect(self.clear)

        status_bar = QWidget()
        status_bar.setFixedHeight(34)
        status_row = QHBoxLayout(status_bar)
        status_row.setContentsMargins(12, 0, 6, 0)
        status_row.setSpacing(8)
        status_row.addWidget(self._dot)
        status_row.addWidget(self._title)
        status_row.addStretch()
        status_row.addWidget(self._shell_combo)
        status_row.addWidget(clear_btn)
        status_row.addWidget(_title_button("−", self.showMinimized))
        status_row.addWidget(_title_button("✕", self.hide))
        status_bar.mousePressEvent = self._drag_press
        status_bar.mouseMoveEvent = self._drag_move

        self._view = TerminalView(cwd_provider=lambda: self._cwd)
        self._input = InputLine()
        self._input.setFont(terminal_mono_font(11))
        self._input.setFrame(False)
        self._input.setStyleSheet(
            f"QLineEdit {{ background:{TERMINAL_BG}; color:{TERMINAL_TEXT};"
            f" border:none; padding:0 8px 0 12px; }}"
        )
        self._input.set_prompt(cwd_prompt(self._cwd))
        self._input.setToolTip(self._cwd)
        self._input.submitted.connect(self._submit)
        self._input.tab_pressed.connect(self._on_tab)
        self._input.history_prev.connect(self._history_prev)
        self._input.history_next.connect(self._history_next)
        self._input.clear_requested.connect(self.clear)
        self._input.paste_truncated.connect(
            lambda: self._set_status(PASTE_TRUNCATED_NOTICE))
        self._input_controller = InputController(
            self._input, lambda: self._cwd,
            lambda: self._shell_combo.currentText(), self._set_status)

        self._status = QLabel("")
        self._status.setFont(terminal_ui_font(11))
        self._status.setStyleSheet(f"color:{TERMINAL_MUTED};")

        input_row = QWidget()
        input_row.setFixedHeight(40)
        input_layout = QHBoxLayout(input_row)
        input_layout.setContentsMargins(0, 0, 12, 0)
        input_layout.setSpacing(8)
        input_layout.addWidget(self._input, 1)
        input_layout.addWidget(self._status)

        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)
        root.addWidget(status_bar)
        root.addWidget(self._view, 1)
        root.addWidget(input_row)

        self.run_event.connect(self._append_run)
        self.run_finished.connect(self._on_run_finished)
        self.local_finished.connect(self._on_local_finished)
        # 视图与槽就绪后再注册，窗口缺位期间的事件在这里补发
        self._bind_runner()

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(500)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._breath_timer = QTimer(self)
        self._breath_timer.setInterval(_BREATH_MS)
        self._breath_timer.timeout.connect(self._toggle_breath)

    def _bind_runner(self) -> None:
        """注册终端回显与共享目录；重复调用无副作用。"""
        self._runner.set_terminal_sink(self.run_event.emit)
        self._runner.set_cwd_source(lambda: self._cwd)

    def closeEvent(self, event):
        """窗口关闭时注销 runner 回调；模型命令仍可独立执行。"""
        try:
            self._runner.set_terminal_sink(None)
            self._runner.set_cwd_source(None)
        except Exception:
            logger.exception("[Terminal] 注销 runner 回调失败")
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        ensure_taskbar_icon(self)
        # 真关闭会注销回调，重新显示时把回显与共享目录接回来
        self._bind_runner()
        self._input.setFocus()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, TERMINAL_WINDOW_RADIUS, TERMINAL_WINDOW_RADIUS)
        painter.fillPath(path, QColor(TERMINAL_BG))
        painter.setPen(QPen(QColor(TERMINAL_BORDER), 1))
        painter.drawPath(path)

    def _drag_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint()

    def _drag_move(self, event):
        if self._drag_origin is not None:
            delta = event.globalPosition().toPoint() - self._drag_origin
            self.move(self.pos() + delta)
            self._drag_origin = event.globalPosition().toPoint()

    def _set_dot(self, color: str) -> None:
        self._dot.setStyleSheet(f"background:{color}; border-radius:4px;")

    def _toggle_breath(self) -> None:
        self._breath_on = not self._breath_on
        self._set_dot(TERMINAL_ACCENT if self._breath_on else TERMINAL_ACCENT_DIM)

    def _set_phase(self, phase: str) -> None:
        """状态点四态：空闲、等待闸门、运行中（呼吸）、出错。"""
        self._phase = phase
        if phase == Phase.WAITING.value:
            self._breath_timer.stop()
            self._set_dot(TERMINAL_USER)
            if not self._busy:
                self._set_status(PHASE_TEXT["waiting"], TERMINAL_USER)
        elif phase == Phase.RUNNING.value:
            self._breath_on = True
            self._set_dot(TERMINAL_ACCENT)
            self._breath_timer.start()
            if not self._busy:
                self._set_status(PHASE_TEXT["running"])
        elif phase == Phase.ERROR.value:
            self._breath_timer.stop()
            self._set_dot(TERMINAL_DANGER)
        else:
            self._breath_timer.stop()
            self._set_dot(TERMINAL_IDLE)
            if not self._busy:
                self._set_status("")

    def _set_status(self, text: str, color: str = TERMINAL_MUTED) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color:{color};")

    def _append_run(self, event: TerminalEvent) -> None:
        if event.kind is EventKind.PHASE:
            active = self._view.active_run_id
            # 交错命令的阶段事件不得覆盖当前运行的状态点与计时
            if event.run_id and active and event.run_id != active:
                return
            self._set_phase(event.phase.value if event.phase else Phase.IDLE.value)
            return
        self._view.append_run(event)

    def _history_prev(self) -> None:
        self._input_controller.history_prev()

    def _history_next(self) -> None:
        self._input_controller.history_next()

    def _on_tab(self, reverse: bool = False) -> None:
        self._input_controller.on_tab(reverse)

    def _reset_completion(self) -> None:
        self._input_controller.reset_completion()

    def _path_commands(self) -> tuple[str, ...]:
        return self._input_controller.path_commands()

    def _submit(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        self._input_controller.record(text)
        self._input.clear()
        target = cd_target(text) or drive_target(text)
        if target is not None:
            self._change_dir(target)
            return
        if self._busy:
            self._set_status(BUSY_NOTICE, TERMINAL_USER)
            return
        if cli.is_local(text):
            self._start_local(text)
            return
        self._start_run(text)

    def _change_dir(self, target: str) -> bool:
        expanded = os.path.expanduser(str(target or "").strip().strip('"'))
        if os.name == "nt" and _DRIVE_PATTERN.match(expanded):
            expanded = expanded.upper() + os.sep
        if not os.path.isabs(expanded):
            expanded = os.path.join(self._cwd, expanded)
        path = os.path.abspath(expanded)
        if not os.path.isdir(path):
            self._set_status(f"目录不存在：{path}", TERMINAL_DANGER)
            return False
        self._cwd = path
        self._input.set_prompt(cwd_prompt(path))
        self._input.setToolTip(path)
        return True

    def _start_run(self, text: str) -> None:
        self._busy = True
        self._started_at = time.monotonic()
        self._set_phase(Phase.WAITING.value)
        self._elapsed_timer.start()
        self._set_status(PHASE_TEXT["waiting"], TERMINAL_USER)
        self._input.setEnabled(False)
        shell = self._shell_combo.currentText()
        cwd = self._cwd
        threading.Thread(target=self._worker, args=(text, cwd, shell),
                         daemon=True).start()

    def _worker(self, text: str, cwd: str, shell: str) -> None:
        try:
            outcome = self._runner.run_user(command=text, cwd=cwd, timeout=0,
                                            shell=shell)
        except Exception as exc:
            logger.exception("[Terminal] 命令执行异常")
            outcome = {"error": str(exc), "code": "internal_error"}
        self.run_finished.emit(outcome)

    def _on_run_finished(self, outcome) -> None:
        self._busy = False
        self._elapsed_timer.stop()
        self._input.setEnabled(True)
        self._input.setFocus()
        error = outcome.get("error") if isinstance(outcome, dict) else ""
        if error:
            self._set_phase(Phase.ERROR.value)
            self._set_status(str(error), TERMINAL_DANGER)
            QTimer.singleShot(1200, lambda: self._set_phase(Phase.IDLE.value))
        else:
            self._set_phase(Phase.IDLE.value)
            self._set_status("")

    def _tick_elapsed(self) -> None:
        if not self._busy:
            return
        elapsed = time.monotonic() - self._started_at
        label = PHASE_TEXT.get(self._phase, PHASE_TEXT["running"])
        self._set_status(f"{label} {elapsed:.1f}s")

    def _start_local(self, text: str) -> None:
        """本地命令在独立线程执行：知识库与记忆检索要等 embedding。"""
        self._busy = True
        self._started_at = time.monotonic()
        self._set_phase(Phase.RUNNING.value)
        self._elapsed_timer.start()
        self._input.setEnabled(False)
        threading.Thread(target=self._local_worker, args=(text, self._cwd),
                         daemon=True).start()

    def _local_worker(self, text: str, cwd: str) -> None:
        try:
            result = cli.run(text, cwd, self._runner, self._control)
        except Exception as exc:
            logger.exception("[Terminal] 本地命令异常")
            result = cli.LocalResult(
                stderr=f"命令执行失败：{exc}", exit_code=cli.EXIT_TOOL)
        self.local_finished.emit({"command": text, "result": result})

    def _on_local_finished(self, payload) -> None:
        self._busy = False
        self._elapsed_timer.stop()
        self._input.setEnabled(True)
        self._input.setFocus()
        result = payload.get("result")
        if result is None:
            return
        if result.exit_code:
            self._set_phase(Phase.ERROR.value)
            QTimer.singleShot(1200, lambda: self._set_phase(Phase.IDLE.value))
        else:
            self._set_phase(Phase.IDLE.value)
        self._set_status("")
        if result.clear:
            self.clear()
            return
        # 本地结果也走事件协议：独立 run_id 保证不会关掉正在输出的命令块
        event = TerminalEvent.finished(
            f"local-{uuid.uuid4().hex}", CommandSource.USER,
            str(payload.get("command") or ""),
            (str(payload.get("command") or ""),), self._cwd,
            rejected=False, exit_code=result.exit_code,
            stdout=result.stdout, stderr=result.stderr,
            duration_ms=int((time.monotonic() - self._started_at) * 1000))
        self._append_run(event)

    def request_quit(self) -> bool:
        """请求退出应用；可从任意线程调用，返回是否已提交。"""
        if self._quit_fn is None:
            return False
        self.quit_requested.emit()
        return True

    def request_restart(self) -> bool:
        """请求重启应用；可从任意线程调用，返回是否已提交。"""
        if self._restart_fn is None:
            return False
        self.restart_requested.emit()
        return True

    @Slot()
    def _on_quit_requested(self) -> None:
        if self._quit_fn is not None:
            self._quit_fn()

    @Slot()
    def _on_restart_requested(self) -> None:
        if self._restart_fn is not None:
            self._restart_fn()

    def clear(self) -> None:
        """清空回显区。"""
        self._view.clear_view()
        self._reset_completion()

    def _update_placeholder(self) -> None:
        self._view.set_placeholder()
