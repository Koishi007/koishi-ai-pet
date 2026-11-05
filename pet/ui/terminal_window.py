"""终端窗口：只读回显、输入行、来源标记、命令历史与补全交互。"""

import logging
import os
import re
import threading
import time
from collections import deque

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QIcon, QPainter, QPainterPath, QPen,
                           QTextCharFormat, QTextCursor)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTextEdit, QVBoxLayout, QWidget)

from pet import cli
from pet.terminal import core, policy
from pet.terminal.runner import get_runner
from pet.ui.completion import (Completion, CompletionContext, complete,
                               quote_candidate)
from pet.ui.styles import (ICON_PATH, TERMINAL_ACCENT, TERMINAL_ACCENT_DIM, TERMINAL_BG,
                           TERMINAL_BORDER, TERMINAL_CONTROL_RADIUS, TERMINAL_DANGER,
                           TERMINAL_HOVER, TERMINAL_IDLE, TERMINAL_MUTED,
                           TERMINAL_MUTED_LIGHT, TERMINAL_PANEL, TERMINAL_STDERR,
                           TERMINAL_STDOUT, TERMINAL_TEXT, TERMINAL_USER,
                           TERMINAL_WINDOW_RADIUS, ensure_taskbar_icon,
                           terminal_mono_font, terminal_source_color,
                           terminal_source_label, terminal_ui_font)
from pet.ui.widgets import NoWheelComboBox

logger = logging.getLogger(__name__)

_MAX_BLOCKS = 5000
_HISTORY_MAX = 100
_PATH_CACHE_S = 60.0
_CWD_LABEL_MAX = 40
_PROMPT_INSET = 12
_BREATH_MS = 600

REJECTED_TEXT = "已拒绝"
PASTE_TRUNCATED_NOTICE = "已忽略后续行"
BUSY_NOTICE = "上一条命令还在执行"
PHASE_TEXT = {"waiting": "等待执行", "running": "运行中"}


def _same_path(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return (os.path.normcase(os.path.abspath(left))
            == os.path.normcase(os.path.abspath(right)))


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


def push_history(history: deque, text: str) -> None:
    """历史去重：与上一条相同则不重复入栈。"""
    value = (text or "").strip()
    if not value:
        return
    if history and history[-1] == value:
        return
    history.append(value)


def elide_cwd(path: str, limit: int = _CWD_LABEL_MAX) -> str:
    """超长目录中间省略为 C:\\Users\\...\\project。"""
    text = str(path or "")
    if len(text) <= limit:
        return text
    keep = (limit - 3) // 2
    return f"{text[:keep]}...{text[-keep:]}"


def cwd_prompt(path: str) -> str:
    """输入行左侧的目录提示，形如 C:\\Users\\tqzth>。"""
    return f"{elide_cwd(path)}>"


class _InputLine(QLineEdit):
    """输入行：回车提交、上下翻历史、Tab 补全、多行粘贴只取首行。"""

    submitted = Signal()
    tab_pressed = Signal(bool)
    history_prev = Signal()
    history_next = Signal()
    clear_requested = Signal()
    paste_truncated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._prompt = ""

    def set_prompt(self, text: str) -> None:
        """输入框内左侧的目录提示；文字紧接提示之后开始。"""
        self._prompt = str(text or "")
        self.setTextMargins(self._prompt_width() + 2, 0, 0, 0)
        self.update()

    def prompt_text(self) -> str:
        return self._prompt

    def _prompt_width(self) -> int:
        if not self._prompt:
            return 0
        return self.fontMetrics().horizontalAdvance(self._prompt)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._prompt:
            return
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(QColor(TERMINAL_TEXT if self.isEnabled() else TERMINAL_MUTED))
        rect = QRect(self.contentsRect().left() + _PROMPT_INSET, 0,
                     self._prompt_width(), self.height())
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self._prompt)

    def insertFromMimeData(self, source):
        text = source.text() if source is not None else ""
        if "\n" in text or "\r" in text:
            first = text.replace("\r\n", "\n").replace("\r", "\n").split("\n", 1)[0]
            self.insert(first)
            self.paste_truncated.emit()
            return
        super().insertFromMimeData(source)

    def focusNextPrevChild(self, next: bool) -> bool:
        """Tab 与 Shift+Tab 交给补全，不做焦点切换。"""
        return False

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.submitted.emit()
            return
        if key == Qt.Key.Key_Escape:
            self.clear()
            return
        if key == Qt.Key.Key_Up:
            self.history_prev.emit()
            return
        if key == Qt.Key.Key_Down:
            self.history_next.emit()
            return
        if key == Qt.Key.Key_Tab:
            self.tab_pressed.emit(bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            return
        if key == Qt.Key.Key_L and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.clear_requested.emit()
            return
        super().keyPressEvent(event)


class TerminalWindow(QWidget):
    """终端窗口：用户输入直接执行，模型执行的命令同步回显。"""

    run_event = Signal(dict)
    run_finished = Signal(object)
    local_finished = Signal(object)

    def __init__(self, runner=None, parent=None):
        super().__init__(parent)
        self._runner = runner if runner is not None else get_runner()
        self._runner.set_terminal_sink(self.run_event.emit)
        self._cwd = resolve_initial_cwd()
        self._runner.set_cwd_source(lambda: self._cwd)
        self._history: deque[str] = deque(maxlen=_HISTORY_MAX)
        self._history_index: int | None = None
        self._draft = ""
        self._busy = False
        self._follow = True
        self._started_at = 0.0
        self._completion: Completion | None = None
        self._cycle_index = 0
        self._quoted = False
        self._block_open = False
        self._pending_lines = {"stdout": "", "stderr": ""}
        self._breath_on = False
        self._phase = "idle"
        self._path_cache: tuple[float, tuple[str, ...]] = (0.0, ())

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
        self._drag_origin: QPoint | None = None

        self._view = QTextEdit()
        self._view.setObjectName("TerminalView")
        self._view.setReadOnly(True)
        self._view.setUndoRedoEnabled(False)
        self._view.setFrameShape(QFrame.Shape.NoFrame)
        self._view.setFont(terminal_mono_font(11))
        self._view.setStyleSheet(
            f"QTextEdit {{ background:{TERMINAL_BG}; color:{TERMINAL_STDOUT}; border:none; }}"
            f"QScrollBar:vertical {{ background:{TERMINAL_BG}; width:8px; }}"
            f"QScrollBar::handle:vertical {{ background:{TERMINAL_BORDER}; border-radius:4px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}"
        )
        self._view.verticalScrollBar().valueChanged.connect(self._on_scroll)

        self._input = _InputLine()
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

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(500)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._breath_timer = QTimer(self)
        self._breath_timer.setInterval(_BREATH_MS)
        self._breath_timer.timeout.connect(self._toggle_breath)

        self._update_placeholder()

    def showEvent(self, event):
        super().showEvent(event)
        ensure_taskbar_icon(self)
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
        if phase == "waiting":
            self._breath_timer.stop()
            self._set_dot(TERMINAL_USER)
            if not self._busy:
                self._set_status(PHASE_TEXT["waiting"], TERMINAL_USER)
        elif phase == "running":
            self._breath_on = True
            self._set_dot(TERMINAL_ACCENT)
            self._breath_timer.start()
            if not self._busy:
                self._set_status(PHASE_TEXT["running"])
        elif phase == "error":
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

    def _append_run(self, payload: dict) -> None:
        """执行线程的事件入口：阶段、开始、输出分块、结束与整块回显。"""
        kind = str(payload.get("kind") or "")
        if kind == "phase":
            self._set_phase(str(payload.get("phase") or ""))
        elif kind == "start":
            self._begin_block(payload)
        elif kind == "chunk":
            self._append_chunk(payload)
        elif kind == "end":
            self._end_block(payload)
        else:
            self._render_block(payload)

    def _begin_block(self, payload: dict) -> None:
        """先落命令行，运行中的输出逐块追加到同一块。"""
        self._pending_lines = {"stdout": "", "stderr": ""}
        at_bottom = self._at_bottom()
        cursor = QTextCursor(self._view.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._insert_header(cursor, payload)
        self._block_open = True
        self._trim_blocks()
        if at_bottom:
            self._scroll_to_bottom()

    def _append_chunk(self, payload: dict) -> None:
        if not self._block_open:
            return
        stream = str(payload.get("stream") or "stdout")
        if stream not in self._pending_lines:
            stream = "stdout"
        text = self._pending_lines[stream] + str(payload.get("text") or "")
        cut = text.rfind("\n")
        if cut < 0:
            self._pending_lines[stream] = text
            return
        complete, rest = text[:cut + 1], text[cut + 1:]
        self._pending_lines[stream] = rest
        at_bottom = self._at_bottom()
        cursor = QTextCursor(self._view.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for line in complete.splitlines():
            self._insert_output_line(cursor, stream, line)
        self._trim_blocks()
        if at_bottom:
            self._scroll_to_bottom()

    def _end_block(self, payload: dict) -> None:
        if not self._block_open:
            self._render_block(payload)
            return
        at_bottom = self._at_bottom()
        cursor = QTextCursor(self._view.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for stream in ("stdout", "stderr"):
            rest = self._pending_lines.get(stream, "")
            if rest:
                self._insert_output_line(cursor, stream, rest)
        self._pending_lines = {"stdout": "", "stderr": ""}
        if payload.get("rejected"):
            reason = str(payload.get("error") or "")
            self._insert_line(cursor, self._rejected_text(reason), TERMINAL_MUTED)
        else:
            self._insert_cwd_line(cursor, payload)
            self._insert_tail(cursor, payload.get("exit_code"),
                              int(payload.get("duration_ms") or 0))
        cursor.insertText("\n")
        self._block_open = False
        self._trim_blocks()
        if at_bottom:
            self._scroll_to_bottom()

    def _render_block(self, payload: dict) -> None:
        """整块回显：历史回填与未产生 start 事件的拒绝。"""
        rejected = bool(payload.get("rejected"))
        at_bottom = self._at_bottom()
        cursor = QTextCursor(self._view.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._insert_header(cursor, payload, muted=rejected)
        if rejected:
            reason = str(payload.get("error") or "")
            self._insert_line(cursor, self._rejected_text(reason), TERMINAL_MUTED)
        else:
            for line in str(payload.get("stdout") or "").splitlines():
                self._insert_output_line(cursor, "stdout", line)
            for line in str(payload.get("stderr") or "").splitlines():
                self._insert_output_line(cursor, "stderr", line)
            self._insert_cwd_line(cursor, payload)
            self._insert_tail(cursor, payload.get("exit_code"),
                              int(payload.get("duration_ms") or 0))
        cursor.insertText("\n")
        self._trim_blocks()
        if at_bottom:
            self._scroll_to_bottom()

    @staticmethod
    def _rejected_text(reason: str) -> str:
        return f"{reason} · {REJECTED_TEXT}" if reason else REJECTED_TEXT

    def _insert_header(self, cursor, payload: dict, muted: bool = False) -> None:
        source = str(payload.get("source") or "user")
        command = str(payload.get("command") or core.display_argv(payload.get("argv") or []))
        header = QTextCharFormat()
        color = TERMINAL_MUTED if muted else terminal_source_color(source)
        header.setForeground(QColor(color))
        header.setFont(terminal_mono_font(11, bold=True))
        cursor.insertText(f"▍{terminal_source_label(source)} ", header)
        body_fmt = QTextCharFormat()
        body_fmt.setForeground(QColor(TERMINAL_MUTED if muted else TERMINAL_TEXT))
        body_fmt.setFont(terminal_mono_font(11))
        cursor.insertText(command + "\n", body_fmt)

    def _insert_cwd_line(self, cursor, payload: dict) -> None:
        """模型命令的工作目录与沙箱根不同时补一行 cwd；状态栏已显示用户命令的目录。"""
        if str(payload.get("source") or "user") == "user":
            return
        target = str(payload.get("cwd") or "")
        if _same_path(target, self._cwd):
            return
        if target:
            self._insert_line(cursor, f"cwd: {target}", TERMINAL_MUTED, prefix="  ")

    def _insert_output_line(self, cursor, stream: str, line: str) -> None:
        if stream == "stderr":
            marker = QTextCharFormat()
            marker.setForeground(QColor(TERMINAL_STDERR))
            marker.setFont(terminal_mono_font(11))
            cursor.insertText("  stderr ", marker)
            self._insert_line(cursor, line, TERMINAL_STDOUT, prefix="")
            return
        self._insert_line(cursor, line, TERMINAL_STDOUT)

    def _insert_line(self, cursor, text: str, color: str, prefix: str = "  ") -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        fmt.setFont(terminal_mono_font(11))
        cursor.insertText(f"{prefix}{text}\n", fmt)

    def _insert_tail(self, cursor, code, duration_ms: int) -> None:
        meta = QTextCharFormat()
        meta.setForeground(QColor(TERMINAL_MUTED))
        meta.setFont(terminal_mono_font(10))
        cursor.insertText("  exit ", meta)
        code_fmt = QTextCharFormat()
        success = code in (0, "0", None)
        code_fmt.setForeground(QColor(TERMINAL_MUTED if success else TERMINAL_DANGER))
        code_fmt.setFont(terminal_mono_font(10))
        cursor.insertText("?" if code is None else str(code), code_fmt)
        cursor.insertText(f" · {duration_ms / 1000:.2f}s\n", meta)

    def _at_bottom(self) -> bool:
        bar = self._view.verticalScrollBar()
        return bar.value() >= bar.maximum() - 2

    def _on_scroll(self, value: int) -> None:
        bar = self._view.verticalScrollBar()
        self._follow = value >= bar.maximum() - 2

    def _scroll_to_bottom(self) -> None:
        bar = self._view.verticalScrollBar()
        bar.setValue(bar.maximum())
        self._follow = True

    def _trim_blocks(self) -> None:
        """超出上限时从头部整块删除，保留最近 _MAX_BLOCKS 块。"""
        document = self._view.document()
        total = document.blockCount()
        if total <= _MAX_BLOCKS:
            return
        excess = total - _MAX_BLOCKS
        cursor = QTextCursor(document)
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(QTextCursor.MoveOperation.NextBlock,
                            QTextCursor.MoveMode.KeepAnchor, excess)
        cursor.removeSelectedText()

    def _submit(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        push_history(self._history, text)
        self._history_index = None
        self._draft = ""
        self._input.clear()
        self._reset_completion()
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

    def _start_local(self, text: str) -> None:
        """本地命令在独立线程执行：知识库与记忆检索要等 embedding。"""
        self._busy = True
        self._started_at = time.monotonic()
        self._set_phase("running")
        self._elapsed_timer.start()
        self._input.setEnabled(False)
        threading.Thread(target=self._local_worker, args=(text, self._cwd),
                         daemon=True).start()

    def _local_worker(self, text: str, cwd: str) -> None:
        try:
            result = cli.run(text, cwd, self._runner)
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
            self._set_phase("error")
            QTimer.singleShot(1200, lambda: self._set_phase("idle"))
        else:
            self._set_phase("idle")
        self._set_status("")
        if result.clear:
            self.clear()
            return
        self._render_block({
            "source": "user",
            "command": str(payload.get("command") or ""),
            "cwd": self._cwd,
            "rejected": False,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": int((time.monotonic() - self._started_at) * 1000),
        })

    def _start_run(self, text: str) -> None:
        self._busy = True
        self._started_at = time.monotonic()
        self._set_phase("waiting")
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
            self._set_phase("error")
            self._set_status(str(error), TERMINAL_DANGER)
            QTimer.singleShot(1200, lambda: self._set_phase("idle"))
        else:
            self._set_phase("idle")
            self._set_status("")

    def _tick_elapsed(self) -> None:
        if not self._busy:
            return
        elapsed = time.monotonic() - self._started_at
        label = PHASE_TEXT.get(self._phase, PHASE_TEXT["running"])
        self._set_status(f"{label} {elapsed:.1f}s")

    def _history_prev(self) -> None:
        if not self._history:
            return
        if self._history_index is None:
            self._draft = self._input.text()
            self._history_index = len(self._history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        self._input.setText(self._history[self._history_index])

    def _history_next(self) -> None:
        if self._history_index is None:
            return
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self._input.setText(self._history[self._history_index])
            return
        self._history_index = None
        self._input.setText(self._draft)

    def _on_tab(self, reverse: bool = False) -> None:
        """Tab 直接轮换候选：首次取第一个，之后逐个循环；Shift+Tab 反向。"""
        if self._completion is None:
            ctx = CompletionContext(
                cwd=self._cwd,
                case_sensitive=os.name != "nt",
                shell=self._shell_combo.currentText(),
                path_commands=self._path_commands(),
                local_commands=cli.command_names(),
                local_subcommands=cli.subcommands(),
                local_path_commands=cli.path_commands(),
                local_aliases=cli.aliases(),
                local_flags=cli.flags(),
                local_values=cli.values(),
            )
            result = complete(self._input.text(), self._input.cursorPosition(), ctx)
            if not result.candidates:
                self._set_status("无匹配")
                return
            self._completion = result
            self._cycle_index = 0
            line = self._input.text()
            self._quoted = result.start > 0 and line[result.start - 1] == '"'
            self._apply_candidate(result.candidates[0],
                                  append_space=len(result.candidates) == 1)
            if len(result.candidates) == 1:
                self._reset_completion()
            return
        step = -1 if reverse else 1
        self._cycle_index = (self._cycle_index + step) % len(self._completion.candidates)
        self._apply_candidate(self._completion.candidates[self._cycle_index],
                              append_space=False)

    def _apply_candidate(self, candidate: str, append_space: bool) -> None:
        text = quote_candidate(candidate, quoted=self._quoted)
        self._replace_token(text)
        if self._completion is None:
            return
        if append_space and not text.endswith(os.sep):
            self._input.setText(self._input.text() + " ")

    def _replace_token(self, text: str) -> None:
        if self._completion is None:
            return
        line = self._input.text()
        start = min(self._completion.start, len(line))
        cursor = max(self._input.cursorPosition(), start)
        self._input.setText(line[:start] + text + line[cursor:])
        self._input.setCursorPosition(start + len(text))

    def _reset_completion(self) -> None:
        self._completion = None
        self._cycle_index = 0
        self._quoted = False

    def _path_commands(self) -> tuple[str, ...]:
        now = time.monotonic()
        cached_at, cached = self._path_cache
        if cached and now - cached_at < _PATH_CACHE_S:
            return cached
        names: set[str] = set()
        for directory in (os.environ.get("PATH") or "").split(os.pathsep):
            if not directory:
                continue
            try:
                with os.scandir(directory) as items:
                    for item in items:
                        try:
                            if not item.is_file():
                                continue
                        except OSError:
                            continue
                        stem, ext = os.path.splitext(item.name)
                        if os.name == "nt":
                            if ext.lower() in (".exe", ".bat", ".cmd", ".com"):
                                names.add(stem)
                        else:
                            names.add(item.name)
            except OSError:
                continue
        result = tuple(sorted(names))
        self._path_cache = (now, result)
        return result

    def clear(self) -> None:
        """清空回显区。"""
        self._view.clear()
        self._reset_completion()
        self._update_placeholder()

    def _update_placeholder(self) -> None:
        self._view.setPlaceholderText("输入命令后回车执行\n↑ ↓ 翻历史 · Ctrl+L 清屏")


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

    def setCurrentText(self, text: str) -> None:
        self._combo.setCurrentText(text)
