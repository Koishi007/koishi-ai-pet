"""抓取状态守卫：左键释放事件丢失时补结算，避免桌宠停在半空。"""

import pytest
from PySide6.QtCore import QDateTime, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from pet.ui.pet_window import PetWindow, _FlatMenuBase


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qt_app):
    w = PetWindow()
    geo = (w.screen() or QApplication.primaryScreen()).availableGeometry()
    w.move(geo.center().x() - w.width() // 2, geo.top() + 100)  # 离开贴边判定区
    yield w
    w.shutdown()
    w.deleteLater()


def _left_release() -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(1.0, 1.0), QPointF(1.0, 1.0),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


class _FixedClock:
    NOW = 10_000

    @classmethod
    def currentMSecsSinceEpoch(cls) -> int:
        return cls.NOW


class TestGrabGuard:

    def test_guard_ends_grab_when_button_already_up(self, window, monkeypatch):
        window._start_drag()
        assert window._grab_local is not None
        assert window._grab_guard.isActive()

        monkeypatch.setattr(QApplication, "mouseButtons",
                            staticmethod(lambda: Qt.MouseButton.NoButton))
        window._grab_guard.timeout.emit()

        assert window._grab_local is None
        assert not window._grab_guard.isActive()
        assert window.pet_actions.gravity._enabled
        assert not window.action_queue._paused

    def test_guard_keeps_grab_while_button_held(self, window, monkeypatch):
        window._start_drag()
        monkeypatch.setattr(QApplication, "mouseButtons",
                            staticmethod(lambda: Qt.MouseButton.LeftButton))
        window._grab_guard.timeout.emit()

        assert window._grab_local is not None
        assert window._grab_guard.isActive()

    def test_release_event_ends_grab(self, window):
        window._start_drag()
        window.mouseReleaseEvent(_left_release())

        assert window._grab_local is None
        assert not window._grab_guard.isActive()
        assert window.pet_actions.gravity._enabled

    def test_context_menu_ends_grab(self, window, monkeypatch):
        window._start_drag()
        monkeypatch.setattr(_FlatMenuBase, "exec", lambda self, *args, **kwargs: None)

        window._show_context_menu(QPoint(0, 0))

        assert window._grab_local is None
        assert not window._grab_guard.isActive()
        assert window.pet_actions.gravity._enabled
        assert not window.action_queue._paused

    def test_shutdown_stops_guard(self, qt_app):
        w = PetWindow()
        try:
            w._start_drag()
            w.shutdown()
            assert not w._grab_guard.isActive()
        finally:
            w.deleteLater()


class TestReleaseVelocity:

    def test_uses_only_samples_within_150ms(self, window, monkeypatch):
        monkeypatch.setattr("pet.ui.pet_window.QDateTime", _FixedClock)
        window._drag_history = [
            (QPoint(0, 0), _FixedClock.NOW - 300),
            (QPoint(0, 0), _FixedClock.NOW - 100),
            (QPoint(100, 0), _FixedClock.NOW),
        ]

        vx, vy = window._release_velocity()

        assert vx == pytest.approx(1000.0)
        assert vy == pytest.approx(0.0)

    def test_single_sample_is_zero(self, window):
        window._drag_history = [(QPoint(0, 0), QDateTime.currentMSecsSinceEpoch())]
        assert window._release_velocity() == (0.0, 0.0)
