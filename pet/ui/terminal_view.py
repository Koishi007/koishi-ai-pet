"""终端回显视图：只读文档、块渲染与按 run_id 归属的事件状态机"""

from __future__ import annotations

import os

from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QFrame, QTextEdit

from pet.terminal import core
from pet.terminal.events import CommandSource, EventKind, TerminalEvent
from pet.ui.styles import (TERMINAL_BG, TERMINAL_BORDER, TERMINAL_DANGER,
                           TERMINAL_MUTED, TERMINAL_STDERR, TERMINAL_STDOUT,
                           TERMINAL_TEXT, terminal_mono_font,
                           terminal_source_color, terminal_source_label)

MAX_LINES = 5000
REJECTED_TEXT = "已拒绝"


def same_path(left: str, right: str) -> bool:
    """两路径是否指同一目录；空路径不算相同。"""
    if not left or not right:
        return False
    return (os.path.normcase(os.path.abspath(left))
            == os.path.normcase(os.path.abspath(right)))


class TerminalView(QTextEdit):
    """只读回显区：把带 run_id 的事件流渲染成互不串块的命令块。"""

    def __init__(self, cwd_provider=None, parent=None):
        super().__init__(parent)
        self._cwd_provider = cwd_provider or (lambda: "")
        self.setObjectName("TerminalView")
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFont(terminal_mono_font(11))
        self.setStyleSheet(
            f"QTextEdit {{ background:{TERMINAL_BG}; color:{TERMINAL_STDOUT}; border:none; }}"
            f"QScrollBar:vertical {{ background:{TERMINAL_BG}; width:8px; }}"
            f"QScrollBar::handle:vertical {{ background:{TERMINAL_BORDER}; width:8px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}"
        )
        self.verticalScrollBar().valueChanged.connect(self.on_scroll)
        self._follow = True
        self._block_open = False
        self._active_run_id: str | None = None
        self._deferred_blocks: list[TerminalEvent] = []
        self.set_placeholder()

    @property
    def active_run_id(self) -> str | None:
        return self._active_run_id

    @property
    def block_open(self) -> bool:
        return self._block_open

    @property
    def deferred_blocks(self) -> list[TerminalEvent]:
        return self._deferred_blocks

    def set_placeholder(self) -> None:
        self.setPlaceholderText("输入命令后回车执行\n↑ ↓ 翻历史 · Ctrl+L 清屏")

    def clear_view(self) -> None:
        self.clear()
        self._block_open = False
        self._active_run_id = None
        self._deferred_blocks = []
        self.set_placeholder()

    def append_run(self, event: TerminalEvent) -> None:
        run_id = event.run_id
        active = self._active_run_id
        foreign = bool(run_id and active and run_id != active)
        if event.kind is EventKind.START:
            self._begin_block(event)
        elif event.kind is EventKind.CHUNK:
            if not foreign:
                self._append_chunk(event)
        elif event.kind is EventKind.END:
            if foreign:
                # busy / 策略拒绝没有 start，等当前块闭合后再独立成块
                if self._block_open:
                    self._deferred_blocks.append(event)
                else:
                    self._render_block(event)
            else:
                self._end_block(event)
        elif event.kind is EventKind.PHASE:
            raise ValueError("phase 事件应由窗口处理，不应交给 TerminalView")
        else:
            raise ValueError(f"未知终端事件类型：{event.kind!r}")

    def _begin_block(self, event: TerminalEvent) -> None:
        """先落命令行，运行中的输出逐块追加到同一块。"""
        at_bottom = self.at_bottom()
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._insert_header(cursor, event)
        self._block_open = True
        self._active_run_id = event.run_id or None
        self.trim_lines()
        if at_bottom:
            self.scroll_to_bottom()

    def _append_chunk(self, event: TerminalEvent) -> None:
        """执行核心保证 text 以换行结尾，这里直接按行插入，不再二次拼装。"""
        if not self._block_open:
            return
        at_bottom = self.at_bottom()
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for line in str(event.text or "").splitlines():
            self._insert_output_line(cursor, event.stream, line)
        self.trim_lines()
        if at_bottom:
            self.scroll_to_bottom()

    def _end_block(self, event: TerminalEvent) -> None:
        if not self._block_open:
            self._render_block(event)
            self._active_run_id = None
            self._flush_deferred()
            return
        at_bottom = self.at_bottom()
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if event.rejected:
            self._insert_line(cursor, self._rejected_text(event.error), TERMINAL_MUTED)
        else:
            self._insert_cwd_line(cursor, event)
            self._insert_tail(cursor, event.exit_code, int(event.duration_ms or 0))
        cursor.insertText("\n")
        self._block_open = False
        self._active_run_id = None
        self.trim_lines()
        self._flush_deferred()
        if at_bottom:
            self.scroll_to_bottom()

    def _render_block(self, event: TerminalEvent) -> None:
        """整块回显：本地命令结果，或没有 start 的拒绝事件。"""
        at_bottom = self.at_bottom()
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._insert_header(cursor, event, muted=bool(event.rejected))
        if event.rejected:
            self._insert_line(cursor, self._rejected_text(event.error), TERMINAL_MUTED)
        else:
            for line in str(event.stdout or "").splitlines():
                self._insert_output_line(cursor, "stdout", line)
            for line in str(event.stderr or "").splitlines():
                self._insert_output_line(cursor, "stderr", line)
            self._insert_cwd_line(cursor, event)
            self._insert_tail(cursor, event.exit_code, int(event.duration_ms or 0))
        cursor.insertText("\n")
        self.trim_lines()
        if at_bottom:
            self.scroll_to_bottom()

    def _flush_deferred(self) -> None:
        """当前块闭合后补齐期间被拒绝的命令，避免块内容交错。"""
        pending, self._deferred_blocks = self._deferred_blocks, []
        for event in pending:
            self._render_block(event)

    @staticmethod
    def _rejected_text(reason: str) -> str:
        return f"{reason} · {REJECTED_TEXT}" if reason else REJECTED_TEXT

    def _insert_header(self, cursor, event: TerminalEvent,
                       muted: bool = False) -> None:
        source = event.source.value
        command = str(event.command or core.display_argv(list(event.argv)))
        header = QTextCharFormat()
        color = TERMINAL_MUTED if muted else terminal_source_color(source)
        header.setForeground(QColor(color))
        header.setFont(terminal_mono_font(11, bold=True))
        cursor.insertText(f"▍{terminal_source_label(source)} ", header)
        body_fmt = QTextCharFormat()
        body_fmt.setForeground(QColor(TERMINAL_MUTED if muted else TERMINAL_TEXT))
        body_fmt.setFont(terminal_mono_font(11))
        cursor.insertText(command + "\n", body_fmt)

    def _insert_cwd_line(self, cursor, event: TerminalEvent) -> None:
        """模型命令的工作目录与窗口当前目录不同时补一行 cwd。"""
        if event.source is CommandSource.USER:
            return
        target = str(event.cwd or "")
        if same_path(target, str(self._cwd_provider() or "")):
            return
        if target:
            self._insert_line(cursor, f"cwd: {target}", TERMINAL_MUTED, prefix="  ")

    @staticmethod
    def _insert_output_line(cursor, stream: str, line: str) -> None:
        if stream == "stderr":
            marker = QTextCharFormat()
            marker.setForeground(QColor(TERMINAL_STDERR))
            marker.setFont(terminal_mono_font(11))
            cursor.insertText("  stderr ", marker)
            TerminalView._insert_line(cursor, line, TERMINAL_STDOUT, prefix="")
            return
        TerminalView._insert_line(cursor, line, TERMINAL_STDOUT)

    @staticmethod
    def _insert_line(cursor, text: str, color: str, prefix: str = "  ") -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        fmt.setFont(terminal_mono_font(11))
        cursor.insertText(f"{prefix}{text}\n", fmt)

    @staticmethod
    def _insert_tail(cursor, code, duration_ms: int) -> None:
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

    def at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - 2

    def on_scroll(self, value: int) -> None:
        self._follow = value >= self.verticalScrollBar().maximum() - 2

    def scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
        self._follow = True

    def trim_lines(self) -> None:
        """超出上限时从头部整行删除，保留最近 MAX_LINES 行。"""
        document = self.document()
        total = document.blockCount()
        if total <= MAX_LINES:
            return
        excess = total - MAX_LINES
        cursor = QTextCursor(document)
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(QTextCursor.MoveOperation.NextBlock,
                            QTextCursor.MoveMode.KeepAnchor, excess)
        cursor.removeSelectedText()
