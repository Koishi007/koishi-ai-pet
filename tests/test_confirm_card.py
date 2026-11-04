"""确认卡片：位置分支、标题序号、快捷键与危险态。"""

import pytest
from PySide6.QtCore import QEvent, QRect, QSize, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from pet.terminal.confirm import ConfirmRequest
from pet.ui import confirm_card
from pet.ui.confirm_card import ALLOW_DANGER_TEXT, ConfirmCard, card_position


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _request(**kwargs) -> ConfirmRequest:
    data = {
        "command": "dir",
        "argv": ["dir"],
        "routed": ["cmd", "/d", "/s", "/c", "dir"],
        "cwd": "C:/work",
        "timeout_s": 60,
        "sequence": 2,
        "total": 3,
    }
    data.update(kwargs)
    return ConfirmRequest(**data)


def _key(key) -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)


class TestPosition:

    screen = QRect(0, 0, 1920, 1080)

    def test_above_pet(self):
        pet = QRect(800, 500, 200, 200)
        point = card_position(pet, self.screen, QSize(420, 180))
        assert point.x() == pet.center().x() - 210
        assert point.y() == pet.top() - 8 - 180

    def test_below_when_no_room_above(self):
        pet = QRect(800, 100, 200, 200)
        point = card_position(pet, self.screen, QSize(420, 180))
        assert point.y() == pet.bottom() + 8

    def test_offscreen_pet_falls_to_corner(self):
        point = card_position(QRect(3000, 3000, 200, 200), self.screen, QSize(420, 180))
        assert point.x() == 1920 - 1 - 24 - 420
        assert point.y() == 1080 - 1 - 24 - 180

    def test_clamped_into_screen(self):
        point = card_position(QRect(0, 500, 10, 10), self.screen, QSize(420, 180))
        assert point.x() == 0


class TestCard:

    def test_counter_uses_sequence(self, qt_app):
        card = ConfirmCard()
        card.show_request(_request())
        assert card._counter.text() == "2/3"
        card.hide()

    def test_escape_rejects_and_advances(self, qt_app):
        card = ConfirmCard()
        advanced = []
        card.set_advance(lambda: advanced.append(True))
        request = _request()
        card.show_request(request)
        card.keyPressEvent(_key(Qt.Key.Key_Escape))
        assert request.result is False
        assert request.event.is_set()
        assert advanced == [True]

    def test_danger_enter_does_not_allow(self, qt_app):
        card = ConfirmCard()
        request = _request(matched_patterns=["删除"])
        card.show_request(request)
        assert card._allow_btn.text() == ALLOW_DANGER_TEXT
        assert card._allow_btn.text() != confirm_card.ALLOW_TEXT
        card.keyPressEvent(_key(Qt.Key.Key_Return))
        assert request.event.is_set() is False
        card.hide()

    def test_enter_allows_regular_command(self, qt_app):
        card = ConfirmCard()
        request = _request()
        card.show_request(request)
        card.keyPressEvent(_key(Qt.Key.Key_Return))
        assert request.result is True
        assert request.event.is_set()

    def test_close_equals_reject(self, qt_app):
        card = ConfirmCard()
        request = _request()
        card.show_request(request)
        card.close()
        assert request.result is False

    def test_countdown_zero_rejects(self, qt_app):
        card = ConfirmCard()
        request = _request(timeout_s=1)
        card.show_request(request)
        card._tick()
        assert request.result is False
        assert request.event.is_set()

    def test_reject_all_clears_queue(self, qt_app):
        card = ConfirmCard()
        calls = []
        card.set_reject_all(lambda: calls.append(True))
        request = _request()
        card.show_request(request)
        card._deny_all()
        assert request.result is False
        assert calls == [True]
