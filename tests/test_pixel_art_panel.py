"""像素画面板：载荷渲染与用户关面板后的抑制逻辑。"""

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

# 导出失败用例使用的 PixelArtError.code
_EXPORT_FAILED = "export_failed"


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def test_render_applies_payload(qt_app):
    from pet.tools.pixel_art.panel import PixelArtPanel

    payload = {**PAYLOAD, "force_show": True}
    panel = PixelArtPanel()
    panel.render(payload)
    panel._apply()
    try:
        assert panel._canvas_id == payload["canvas_id"]
        assert panel._revision == payload["revision"]
        assert panel._preset
        assert panel._status_base
        assert panel._export_btn.isEnabled()
        assert panel.isVisible()
    finally:
        panel.hide()


def test_close_suppresses_auto_show(qt_app):
    from pet.tools.pixel_art.panel import PixelArtPanel

    panel = PixelArtPanel()
    panel.render({**PAYLOAD, "force_show": True})
    panel._apply()
    panel._close_by_user()

    panel.render(PAYLOAD)
    panel._apply()
    try:
        assert not panel.isVisible()

        panel.render({**PAYLOAD, "force_show": True})
        panel._apply()
        assert panel.isVisible()
    finally:
        panel.hide()


def test_preset_payload_is_recorded(qt_app):
    from pet.tools.pixel_art.panel import PixelArtPanel

    payload = {**PAYLOAD, "preset": "gameboy", "force_show": True}
    panel = PixelArtPanel()
    panel.render(payload)
    panel._apply()
    try:
        assert panel._preset == payload["preset"]
        assert panel._status_base
    finally:
        panel.hide()


def test_place_near_pet_uses_actual_size(qt_app, monkeypatch):
    """边界判断用 sizeHint 得到的实际尺寸。"""
    from pet.tools.pixel_art.panel import PixelArtPanel

    avail = qt_app.primaryScreen().availableGeometry()
    pet_rect = {"x": avail.x() + 10, "y": avail.y() + 10,
                "width": 120, "height": 120}
    available = {"x": avail.x(), "y": avail.y(),
                 "width": avail.width(), "height": avail.height()}
    monkeypatch.setattr(
        TOOL_CTX, "pet_geometry",
        lambda: {"visible": True, "rect": pet_rect, "available": available})
    panel = PixelArtPanel()
    panel._place_near_pet()
    try:
        assert panel.pos().x() == pet_rect["x"] + pet_rect["width"] - 1 + 10
        assert panel.pos().y() == pet_rect["y"]
        assert panel.width() < 640
    finally:
        panel.hide()


def test_empty_payload_disables_export(qt_app):
    from pet.tools.pixel_art.panel import PixelArtPanel

    panel = PixelArtPanel()
    panel.render({**PAYLOAD, "force_show": True})
    panel._apply()
    panel.render({"empty": True, "force_show": True})
    panel._apply()
    try:
        assert panel.isVisible()
        assert panel._canvas_id is None
        assert panel._revision is None
        assert panel._preset is None
        assert panel._status_base
        assert not panel._export_btn.isEnabled()
    finally:
        panel.hide()


def test_export_button_uses_current_canvas(qt_app, monkeypatch, tmp_path):
    from pet.tools.pixel_art import panel as module

    calls = {}

    def fake_export(canvas_id=None, format=None):
        calls["canvas_id"] = canvas_id
        calls["format"] = format
        return {"data": {"path": str(tmp_path / "a.png")}}

    monkeypatch.setattr(module.art_core, "export", fake_export)
    panel = module.PixelArtPanel()
    panel.render({**PAYLOAD, "force_show": True})
    panel._apply()
    panel._export_png()
    try:
        assert calls == {"canvas_id": PAYLOAD["canvas_id"], "format": "png"}
        assert panel._last_error_code is None
        assert panel._flash_timer.isActive()
        assert panel._status.toolTip()
    finally:
        panel.hide()


def test_export_flash_does_not_widen(qt_app, monkeypatch, tmp_path):
    from pet.tools.pixel_art import panel as module

    long_path = tmp_path / ("很长的目录名" * 8) / "a.png"
    monkeypatch.setattr(
        module.art_core, "export",
        lambda canvas_id=None, format=None: {"data": {"path": str(long_path)}})
    panel = module.PixelArtPanel()
    panel.render({**PAYLOAD, "force_show": True})
    panel._apply()
    before = panel.width()
    panel._export_png()
    try:
        assert panel.width() == before
        assert panel._flash_timer.isActive()
        assert panel._status.toolTip()
        assert panel._status.text() != panel._status.toolTip()
    finally:
        panel.hide()


def test_export_button_reports_failure(qt_app, monkeypatch):
    from pet.tools.pixel_art import panel as module
    from pet.tools.pixel_art.canvas import PixelArtError

    def boom(canvas_id=None, format=None):
        raise PixelArtError(_EXPORT_FAILED, "磁盘已满")

    monkeypatch.setattr(module.art_core, "export", boom)
    panel = module.PixelArtPanel()
    panel.render({**PAYLOAD, "force_show": True})
    panel._apply()
    panel._export_png()
    try:
        assert panel._last_error_code == _EXPORT_FAILED
        assert panel._flash_timer.isActive()
        assert panel._status.toolTip()
    finally:
        panel.hide()


def test_push_view_drives_panel(qt_app):
    """写入画面经工具私有桥落到主线程面板；面板按需创建。"""
    from pet.tools.pixel_art import panel as module

    module._current = None
    try:
        module.push_view({**PAYLOAD, "force_show": True})
        qt_app.processEvents()
        panel = module._current
        assert panel is not None
        panel._apply()
        assert panel.isVisible()
        assert panel._canvas_id == PAYLOAD["canvas_id"]
    finally:
        if module._current is not None:
            module._current.hide()
            module._current = None


def test_bridge_lands_on_main_thread(qt_app, monkeypatch):
    """工具线程的写入经桥排到主线程，面板只在主线程创建。"""
    import threading
    import time

    from pet.tools.pixel_art import panel as module

    module._current = None
    monkeypatch.setattr(module, "_bridge", None)
    created_in = []
    real_panel = module.PixelArtPanel

    def spy(*args, **kwargs):
        created_in.append(threading.current_thread())
        return real_panel(*args, **kwargs)

    monkeypatch.setattr(module, "PixelArtPanel", spy)
    worker = threading.Thread(
        target=lambda: module.push_view({**PAYLOAD, "force_show": True}))
    worker.start()
    worker.join()
    deadline = time.monotonic() + 2
    while module._current is None and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)
    try:
        panel = module._current
        assert panel is not None
        assert module._bridge.thread() is qt_app.thread()
        assert created_in == [threading.main_thread()]
        assert panel.thread() is qt_app.thread()
    finally:
        if module._current is not None:
            module._current.hide()
            module._current = None
