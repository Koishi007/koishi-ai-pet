"""自绘对话框契约：按钮角色、关闭与回车落默认角色。"""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pet.ui.ink_dialog import Answer, InkDialog
from pet.ui.qss import BUTTON_DANGER_QSS, BUTTON_PRIMARY_QSS, BUTTON_QSS


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _dialog(default: Answer = Answer.REJECT) -> InkDialog:
    return InkDialog(
        None, title="t", text="x",
        buttons=[(Answer.ACCEPT, "a", BUTTON_PRIMARY_QSS),
                 (Answer.DESTRUCTIVE, "d", BUTTON_DANGER_QSS),
                 (Answer.REJECT, "r", BUTTON_QSS)],
        default=default)


def test_each_button_reports_its_role(qt_app):
    for role in (Answer.ACCEPT, Answer.DESTRUCTIVE, Answer.REJECT):
        dlg = _dialog()
        dlg.button(role).click()
        assert dlg.answer() is role


def test_escape_returns_default_role(qt_app):
    dlg = _dialog(default=Answer.DESTRUCTIVE)
    QTest.keyClick(dlg, Qt.Key.Key_Escape)
    assert dlg.answer() is Answer.DESTRUCTIVE


def test_enter_returns_default_role(qt_app):
    dlg = _dialog(default=Answer.REJECT)
    QTest.keyClick(dlg, Qt.Key.Key_Return)
    assert dlg.answer() is Answer.REJECT


def test_button_row_is_right_aligned(qt_app):
    dlg = _dialog()
    assert dlg._button_row.itemAt(0).spacerItem() is not None


def test_reject_returns_default_role(qt_app):
    dlg = _dialog(default=Answer.REJECT)
    dlg.reject()
    assert dlg.answer() is Answer.REJECT


def test_enter_ignores_focused_role_button(qt_app):
    dlg = _dialog(default=Answer.REJECT)
    accept = dlg.button(Answer.ACCEPT)
    accept.setFocus()
    QTest.keyClick(accept._inner, Qt.Key.Key_Return)
    assert dlg.answer() is Answer.REJECT
