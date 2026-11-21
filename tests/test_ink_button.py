"""墨影按钮：按下下沉时按钮面位移，命中判定仍覆盖原按下位置。"""

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from pet.ui.ink_button import InkButton


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_press_near_top_edge_still_clicks(qt_app):
    btn = InkButton("x")
    btn.setFixedSize(32, 32)
    seen: list[bool] = []
    btn.clicked.connect(lambda: seen.append(True))
    btn.show()
    try:
        QTest.mousePress(btn._inner, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, QPoint(16, 1))
        btn._on_anim(1.0)
        assert btn._inner.pos() == QPoint(2, 3)
        QTest.mouseRelease(btn._inner, Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier, QPoint(14, -2))
    finally:
        btn.hide()
    assert seen == [True]


def test_release_far_outside_still_cancels(qt_app):
    btn = InkButton("x")
    btn.setFixedSize(32, 32)
    seen: list[bool] = []
    btn.clicked.connect(lambda: seen.append(True))
    btn.show()
    try:
        QTest.mousePress(btn._inner, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, QPoint(16, 16))
        btn._on_anim(1.0)
        QTest.mouseRelease(btn._inner, Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier, QPoint(60, 60))
    finally:
        btn.hide()
    assert seen == []
