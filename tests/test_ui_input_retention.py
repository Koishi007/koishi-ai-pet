"""对话/喂食输入框：收起与忙态保留草稿，只有提交才清空。"""

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

from pet.ui.chat_bubble import ChatBubble
from pet.ui.feed_bubble import FeedBubble


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


class _WindowStub:
    def geometry(self) -> QRect:
        return QRect(100, 100, 64, 64)

    def screen(self):
        return None


@pytest.mark.parametrize("factory", [ChatBubble, FeedBubble], ids=["chat", "feed"])
def test_collapse_keeps_draft(factory, qt_app):
    bubble = factory(_WindowStub())
    bubble._input.setText("还没写完的一句话")
    bubble._expand()

    bubble._collapse()
    bubble._on_collapse_done()

    assert bubble._input.text() == "还没写完的一句话"
    assert bubble._input.isHidden()

    bubble._expand()
    assert bubble._input.text() == "还没写完的一句话"


@pytest.mark.parametrize(
    "factory, submit, signal",
    [(ChatBubble, "submit", "chat_submitted"), (FeedBubble, "_on_submit", "feed_submitted")],
    ids=["chat", "feed"],
)
def test_submit_clears_input(factory, submit, signal, qt_app):
    bubble = factory(_WindowStub())
    seen: list[str] = []
    getattr(bubble, signal).connect(seen.append)
    bubble._input.setText("  待提交内容  ")

    getattr(bubble, submit)()

    assert seen == ["待提交内容"]
    assert bubble._input.text() == ""


def test_busy_submit_keeps_draft(qt_app):
    bubble = ChatBubble(_WindowStub())
    seen: list[str] = []
    bubble.chat_submitted.connect(seen.append)
    bubble.set_busy(True)
    bubble._input.setText("等一会儿再发")

    bubble.submit()

    assert seen == []
    assert bubble._input.text() == "等一会儿再发"


def test_escape_releases_enter_intercept(qt_app):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    bubble = ChatBubble(_WindowStub())
    seen: list[bool] = []
    bubble.enter_intercept.connect(seen.append)
    bubble._expand()
    bubble.finalize_voice_text("语音草稿")

    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                      Qt.KeyboardModifier.NoModifier)
    assert bubble.eventFilter(bubble._input, event) is True
    assert seen[-1] is False
    assert bubble._input.text() == "语音草稿"
