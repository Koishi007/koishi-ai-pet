"""宠物大小热应用：保存挡位后淡出、改尺寸、淡入。"""

import pytest
from PySide6.QtWidgets import QApplication

from pet.config import config
from pet.ui.pet_window import PetWindow


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _set_size(monkeypatch, size: int) -> None:
    monkeypatch.setattr(config, "PET_WIDTH", size)
    monkeypatch.setattr(config, "PET_HEIGHT", size)


def _capture_fade(window, monkeypatch, calls: list) -> dict:
    """替换淡出/淡入，拿回淡出结束回调以便手动触发。"""
    captured = {}
    monkeypatch.setattr(window.pet_actions, "fade_out",
                        lambda duration=300, callback=None: captured.update(cb=callback))
    monkeypatch.setattr(window.pet_actions, "fade_in",
                        lambda duration=300: calls.append("in"))
    return captured


def test_apply_scale_resizes_after_fade_out(qt_app, monkeypatch):
    window = PetWindow()
    window.show()
    try:
        initial = window.width()
        center = window.geometry().center()
        frames, calls = [], []
        monkeypatch.setattr(window.pet_anim, "rebuild_frames", lambda: frames.append(1))
        captured = _capture_fade(window, monkeypatch, calls)
        _set_size(monkeypatch, 250)

        window.apply_scale()
        assert window.width() == initial          # 淡出期间不动尺寸
        assert captured["cb"] is not None

        captured["cb"]()
        assert (window.width(), window.height()) == (250, 250)
        assert frames == [1]
        assert calls == ["in"]
        assert abs(window.geometry().center().x() - center.x()) <= 1
        assert abs(window.geometry().center().y() - center.y()) <= 1
    finally:
        window.shutdown()
        window.deleteLater()


def test_apply_scale_keeps_dock_side(qt_app, monkeypatch):
    window = PetWindow()
    window.show()
    try:
        geo = (window.screen() or QApplication.primaryScreen()).availableGeometry()
        window.dock_to("left")
        calls = []
        captured = _capture_fade(window, monkeypatch, calls)
        _set_size(monkeypatch, 250)

        window.apply_scale()
        captured["cb"]()

        assert window.width() == 250
        assert window.x() == geo.left()
        assert not window.mask().isEmpty()
        assert calls == ["in"]
    finally:
        window.undock()
        window.shutdown()
        window.deleteLater()


def test_hidden_window_resizes_without_fade(qt_app, monkeypatch):
    window = PetWindow()
    try:
        window.hide()
        calls = []
        monkeypatch.setattr(window.pet_actions, "fade_out",
                            lambda *args, **kwargs: calls.append("out"))
        _set_size(monkeypatch, 250)

        window.apply_scale()

        assert window.width() == 250
        assert calls == []
    finally:
        window.shutdown()
        window.deleteLater()
