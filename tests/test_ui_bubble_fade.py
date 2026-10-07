"""气泡复用时被打断的淡出：chat / feed / music / file 四处共一套 show/hide 结构。

只构造气泡本身，宿主窗口用几何桩替代；断言「再次显示后透明度与跟随定时器复原」，
不依赖真实桌面。
"""

import time

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

from pet.file_intake import FileRef
from pet.ui.chat_bubble import ChatBubble
from pet.ui.feed_bubble import FeedBubble
from pet.ui.file_bubble import FileBubble
from pet.ui.music_bubble import MusicBubble


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


class _WindowStub:
    """只提供气泡定位所需的几何信息。"""

    def __init__(self, rect: QRect):
        self._rect = rect

    def geometry(self) -> QRect:
        return self._rect

    def screen(self):
        return None


@pytest.fixture
def pet_window():
    return _WindowStub(QRect(100, 100, 64, 64))


def _pump(qt_app, condition, timeout_s: float = 3.0) -> bool:
    """驱动事件循环直到条件成立或超时，用于等动画走过一段。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def _ref(name: str = "a.txt") -> FileRef:
    return FileRef(path=f"C:/tmp/{name}", name=name, suffix="", size=10, kind="text")


def _show(bubble) -> None:
    """按各自的公开入口显示：文件气泡走 show_files，其余走 show_bubble。"""
    if isinstance(bubble, FileBubble):
        bubble.show_files((_ref(),))
    else:
        bubble.show_bubble()


def _interrupted_show(bubble, qt_app) -> float:
    """显示 → 等淡出走一段 → 中途再次显示，返回再次显示后的透明度。"""
    _show(bubble)
    assert _pump(qt_app, lambda: bubble.windowOpacity() > 0.95), "淡入未完成"
    bubble.hide_bubble()
    assert _pump(qt_app, lambda: bubble.windowOpacity() < 0.9), "淡出未启动"
    _show(bubble)
    return bubble.windowOpacity()


@pytest.mark.parametrize("factory", [
    ChatBubble,
    FeedBubble,
    MusicBubble,
    FileBubble,
], ids=["chat", "feed", "music", "file"])
def test_interrupted_fade_restores_bubble(factory, pet_window, qt_app):
    bubble = factory(pet_window)
    assert _interrupted_show(bubble, qt_app) == 1.0
    assert bubble._follow_timer.isActive()
