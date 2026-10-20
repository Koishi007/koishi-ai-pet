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
