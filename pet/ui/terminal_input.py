"""终端输入：输入行控件、历史轮换、Tab 补全与 PATH 扫描"""

from __future__ import annotations

import os
import time
from collections import deque

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QLineEdit

from pet import cli
from pet.ui.completion import (Completion, CompletionContext, complete,
                               quote_candidate)
from pet.ui.styles import TERMINAL_MUTED, TERMINAL_TEXT

HISTORY_MAX = 100
PATH_CACHE_S = 60.0
CWD_LABEL_MAX = 40
PROMPT_INSET = 12


def push_history(history: deque, text: str) -> None:
    """历史去重：与上一条相同则不重复入栈。"""
    value = (text or "").strip()
    if not value:
        return
    if history and history[-1] == value:
        return
    history.append(value)


def elide_cwd(path: str, limit: int = CWD_LABEL_MAX) -> str:
    """超长目录中间省略为 C:\\Users\\...\\project。"""
    text = str(path or "")
    if len(text) <= limit:
        return text
    keep = (limit - 3) // 2
    return f"{text[:keep]}...{text[-keep:]}"


def cwd_prompt(path: str) -> str:
    """输入行左侧的目录提示，形如 C:\\Users\\tqzth>。"""
    return f"{elide_cwd(path)}>"


class InputLine(QLineEdit):
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
        rect = QRect(self.contentsRect().left() + PROMPT_INSET, 0,
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


class InputController:
    """历史、补全轮换与 PATH 候选缓存；不碰回显文档与执行闸门。"""

    def __init__(self, line: InputLine, cwd_provider, shell_provider,
                 status_fn=None):
        self.line = line
        self._cwd_provider = cwd_provider
        self._shell_provider = shell_provider
        self._status_fn = status_fn or (lambda text: None)
        self.history: deque[str] = deque(maxlen=HISTORY_MAX)
        self._history_index: int | None = None
        self._draft = ""
        self._completion: Completion | None = None
        self._cycle_index = 0
        self._quoted = False
        self._path_cache: tuple[float, tuple[str, ...]] = (0.0, ())

    def record(self, text: str) -> None:
        """提交一条命令：入历史、复位补全轮换。"""
        push_history(self.history, text)
        self._history_index = None
        self._draft = ""
        self.reset_completion()

    def history_prev(self) -> None:
        if not self.history:
            return
        if self._history_index is None:
            self._draft = self.line.text()
            self._history_index = len(self.history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        self.line.setText(self.history[self._history_index])

    def history_next(self) -> None:
        if self._history_index is None:
            return
        if self._history_index < len(self.history) - 1:
            self._history_index += 1
            self.line.setText(self.history[self._history_index])
            return
        self._history_index = None
        self.line.setText(self._draft)

    def reset_completion(self) -> None:
        self._completion = None
        self._cycle_index = 0
        self._quoted = False

    def on_tab(self, reverse: bool = False) -> None:
        """Tab 直接轮换候选：首次取第一个，之后逐个循环；Shift+Tab 反向。"""
        if self._completion is None:
            ctx = CompletionContext(
                cwd=str(self._cwd_provider() or ""),
                case_sensitive=os.name != "nt",
                shell=str(self._shell_provider() or ""),
                path_commands=self.path_commands(),
                local_commands=cli.command_names(),
                local_subcommands=cli.subcommands(),
                local_path_commands=cli.path_commands(),
                local_aliases=cli.aliases(),
                local_flags=cli.flags(),
                local_values=cli.values(),
            )
            result = complete(self.line.text(), self.line.cursorPosition(), ctx)
            if not result.candidates:
                self._status_fn("无匹配")
                return
            self._completion = result
            self._cycle_index = 0
            line = self.line.text()
            self._quoted = result.start > 0 and line[result.start - 1] == '"'
            self._apply_candidate(result.candidates[0],
                                  append_space=len(result.candidates) == 1)
            if len(result.candidates) == 1:
                self.reset_completion()
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
            self.line.setText(self.line.text() + " ")

    def _replace_token(self, text: str) -> None:
        if self._completion is None:
            return
        line = self.line.text()
        start = min(self._completion.start, len(line))
        cursor = max(self.line.cursorPosition(), start)
        self.line.setText(line[:start] + text + line[cursor:])
        self.line.setCursorPosition(start + len(text))

    def path_commands(self) -> tuple[str, ...]:
        """系统 PATH 下的可执行名，带 60 秒缓存。"""
        now = time.monotonic()
        cached_at, cached = self._path_cache
        if cached and now - cached_at < PATH_CACHE_S:
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
