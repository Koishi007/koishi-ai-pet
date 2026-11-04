"""通用控件：下拉框禁用滚轮，界面层不直接实例化裸 QComboBox。"""

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from pet.ui.widgets import NoWheelComboBox

_UI_DIR = Path(__file__).resolve().parent.parent / "pet" / "ui"


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _wheel() -> QWheelEvent:
    return QWheelEvent(
        QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )


def test_wheel_does_not_change_selection(qt_app):
    combo = NoWheelComboBox()
    combo.addItems(["a", "b"])
    combo.setCurrentIndex(0)
    event = _wheel()

    combo.wheelEvent(event)

    assert combo.currentIndex() == 0
    assert event.isAccepted() is False


def test_ui_layer_has_no_plain_combobox():
    offenders = [p.name for p in _UI_DIR.glob("*.py")
                 if "QComboBox(" in p.read_text(encoding="utf-8")]
    assert offenders == []
