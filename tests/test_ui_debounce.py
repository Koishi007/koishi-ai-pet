"""防抖与气泡按钮的重复触发保护。"""

import pytest
from PySide6.QtWidgets import QApplication

from pet.file_intake import FileRef
from pet.tools.registry import TOOL_REGISTRY
from pet.ui.debounce import Debounce
from pet.ui.file_bubble import FileBubble


class TestDebounce:
    def test_first_call_passes_and_repeat_is_blocked(self):
        guard = Debounce(window=0.05, clock=lambda: 100.0)
        assert guard.ready() is True
        assert guard.ready() is False

    def test_passes_again_after_window(self):
        ticks = iter([100.0, 100.06])
        guard = Debounce(window=0.05, clock=lambda: next(ticks))
        assert guard.ready() is True
        assert guard.ready() is True


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def bubble(qt_app, monkeypatch):
    monkeypatch.setattr(TOOL_REGISTRY, "file_actions", lambda: [])
    return FileBubble(pet_window=None)


def test_double_click_emits_once(bubble):
    seen = []
    bubble.action_chosen.connect(lambda action_id, refs: seen.append(action_id))
    bubble._refs = (FileRef(path="C:/tmp/a.txt", name="a.txt", suffix=".txt",
                            size=1, kind="text"),)

    bubble._on_action("taste")
    bubble._on_action("taste")

    assert seen == ["taste"]
