"""像素画窗口：载荷渲染与用户关窗后的抑制逻辑。"""

from __future__ import annotations

import pytest

from pet.tools.context import TOOL_CTX

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

PAYLOAD = {
    "canvas_id": "1",
    "name": "苹果",
    "size": 16,
    "revision": 2,
    "changed_cells": 8,
    "palette_size": 16,
    "png": b"",
}


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def test_render_updates_status(qt_app):
    from pet.ui.pixel_art_window import PixelArtWindow

    window = PixelArtWindow()
    window.render({**PAYLOAD, "force_show": True})
    window._apply()
    try:
        assert "苹果" in window._title.text()
        assert "revision 2" in window._status.text()
        assert window.isVisible()
    finally:
        window.hide()


def test_close_suppresses_auto_show(qt_app):
    from pet.ui.pixel_art_window import PixelArtWindow

    window = PixelArtWindow()
    window.render({**PAYLOAD, "force_show": True})
    window._apply()
    window._close_by_user()

    window.render(PAYLOAD)
    window._apply()
    try:
        assert not window.isVisible()

        window.render({**PAYLOAD, "force_show": True})
        window._apply()
        assert window.isVisible()
    finally:
        window.hide()


def test_status_shows_preset(qt_app):
    from pet.ui.pixel_art_window import PixelArtWindow

    window = PixelArtWindow()
    window.render({**PAYLOAD, "preset": "gameboy", "force_show": True})
    window._apply()
    try:
        assert "gameboy" in window._status.text()
    finally:
        window.hide()


def test_empty_payload_shows_placeholder(qt_app):
    from pet.ui.pixel_art_window import PixelArtWindow

    window = PixelArtWindow()
    window.render({**PAYLOAD, "force_show": True})
    window._apply()
    window.render({"empty": True, "force_show": True})
    window._apply()
    try:
        assert window.isVisible()
        assert "还没有画布" in window._status.text()
        assert not window._export_btn.isEnabled()
    finally:
        window.hide()


def test_export_button_uses_current_canvas(qt_app, monkeypatch, tmp_path):
    from pet.ui import pixel_art_window as module

    calls = {}

    def fake_export(canvas_id=None, format=None):
        calls["canvas_id"] = canvas_id
        calls["format"] = format
        return {"data": {"path": str(tmp_path / "a.png")}}

    monkeypatch.setattr(module.art_core, "export", fake_export)
    window = module.PixelArtWindow()
    window.render({**PAYLOAD, "force_show": True})
    window._apply()
    window._export_png()
    try:
        assert calls == {"canvas_id": "1", "format": "png"}
        assert "a.png" in window._status.text()
    finally:
        window.hide()


def test_export_button_reports_failure(qt_app, monkeypatch):
    from pet.ui import pixel_art_window as module
    from pet.tools.pixel_art.canvas import PixelArtError

    def boom(canvas_id=None, format=None):
        raise PixelArtError("export_failed", "磁盘已满")

    monkeypatch.setattr(module.art_core, "export", boom)
    window = module.PixelArtWindow()
    window.render({**PAYLOAD, "force_show": True})
    window._apply()
    window._export_png()
    try:
        assert "导出失败" in window._status.text()
        assert "磁盘已满" in window._status.text()
    finally:
        window.hide()


def test_show_canvas_without_agent_is_silent():
    TOOL_CTX.show_canvas({"canvas_id": "1"})
