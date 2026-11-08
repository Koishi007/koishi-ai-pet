"""确认卡片：位置分支、标题序号、快捷键、拖动与危险态。"""

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent, QMouseEvent
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
    card = QSize(420, 180)

    def test_side_of_pet(self):
        pet = QRect(800, 500, 200, 200)
        point = card_position(pet, self.screen, self.card)
        assert point == QPoint(pet.right() + 1 + 8, pet.center().y() - 90)

    def test_wider_side_wins(self):
        pet = QRect(1500, 500, 200, 200)
        point = card_position(pet, self.screen, self.card)
        assert point == QPoint(pet.left() - 8 - 420, pet.center().y() - 90)

    def test_below_when_no_room_on_side(self):
        pet = QRect(360, 100, 1200, 200)
        point = card_position(pet, self.screen, self.card)
        assert point.y() == pet.bottom() + 1 + 8

    def test_above_when_no_room_below(self):
        pet = QRect(360, 900, 1200, 180)
        point = card_position(pet, self.screen, self.card)
        assert point.y() == pet.top() - 8 - 180

    def test_offscreen_pet_falls_to_corner(self):
        point = card_position(QRect(3000, 3000, 200, 200), self.screen, self.card)
        assert point.x() == 1920 - 1 - 24 - 420
        assert point.y() == 1080 - 1 - 24 - 180

    def test_docked_left_pet_puts_card_on_right(self):
        pet = QRect(0, 500, 10, 10)
        point = card_position(pet, self.screen, self.card)
        assert point.x() == pet.right() + 1 + 8
        assert point.y() == pet.center().y() - 90

    def test_docked_right_pet_puts_card_on_left(self):
        pet = QRect(1910, 500, 10, 10)
        point = card_position(pet, self.screen, self.card)
        assert point.x() == pet.left() - 8 - 420

    def test_card_stays_inside_screen(self):
        pet = QRect(0, 0, 10, 10)
        point = card_position(pet, self.screen, self.card)
        assert self.screen.contains(QRect(point, self.card))


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


class TestDrag:
    """卡片可拖动；每条请求重新定位到恋恋侧面。"""

    @staticmethod
    def _press(point: QPoint) -> QMouseEvent:
        return QMouseEvent(
            QEvent.Type.MouseButtonPress, QPointF(10, 10), QPointF(point),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier)

    @staticmethod
    def _move(point: QPoint) -> QMouseEvent:
        return QMouseEvent(
            QEvent.Type.MouseMove, QPointF(10, 10), QPointF(point),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier)

    @staticmethod
    def _release(point: QPoint) -> QMouseEvent:
        return QMouseEvent(
            QEvent.Type.MouseButtonRelease, QPointF(10, 10), QPointF(point),
            Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier)

    def _drag_to(self, card: ConfirmCard, target: QPoint) -> None:
        origin = card.frameGeometry().topLeft() + QPoint(10, 10)
        card.mousePressEvent(self._press(origin))
        card.mouseMoveEvent(self._move(origin + target - card.pos()))
        card.mouseReleaseEvent(self._release(origin + target - card.pos()))

    def test_drag_moves_card(self, qt_app):
        card = ConfirmCard()
        card.set_pet_rect_provider(lambda: QRect(100, 100, 200, 200))
        card.show_request(_request())
        area = QGuiApplication.primaryScreen().availableGeometry()
        target = QPoint(area.left() + 10, area.top() + 10)
        self._drag_to(card, target)
        assert card.pos() == target
        card.hide()

    def test_next_request_ignores_previous_position(self, qt_app):
        card = ConfirmCard()
        card.set_pet_rect_provider(lambda: QRect(100, 100, 200, 200))
        card.show_request(_request())
        expected = card.pos()
        area = QGuiApplication.primaryScreen().availableGeometry()
        self._drag_to(card, QPoint(area.left() + 10, area.top() + 10))
        card.show_request(_request())
        assert card.pos() == expected
        card.hide()

    def test_card_width_is_bounded(self, qt_app):
        card = ConfirmCard()
        long_argv = ["echo", "x" * 400]
        card.show_request(_request(command=" ".join(long_argv),
                                   argv=long_argv, routed=long_argv))
        assert card.width() <= confirm_card.CARD_MAX_WIDTH
        card.hide()
