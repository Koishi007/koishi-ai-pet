"""音量滑块渲染：黄绿斜纹填充，斜度与方向对齐渲染稿。"""

import pytest
from PySide6.QtWidgets import QApplication

from pet import theme
from pet.ui.volume_slider import VolumeSlider

_WIDTH = 240
_HEIGHT = 20
_BAND_TOP = 6
_BAND_BOTTOM = 11
_REFERENCE = ((theme.ACCENT, "a"), (theme.ACCENT_HOVER, "h"))
_TOLERANCE = 6


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _image(value: int):
    slider = VolumeSlider()
    slider.resize(_WIDTH, _HEIGHT)
    slider.setRange(0, 100)
    slider.setValue(value)
    image = slider.grab().toImage()
    slider.deleteLater()
    return image


def _classify(x: int, y: int, image) -> str:
    """把像素归到填充底色、斜纹色或其它，抗锯齿边缘不算。"""
    color = image.pixelColor(x, y)
    rgb = (color.red(), color.green(), color.blue())
    for hex_value, token in _REFERENCE:
        target = tuple(int(hex_value[i:i + 2], 16) for i in (1, 3, 5))
        if all(abs(a - b) <= _TOLERANCE for a, b in zip(rgb, target)):
            return token
    return "o"


def _band_edges(value: int) -> dict[int, set[int]]:
    image = _image(value)
    edges: dict[int, set[int]] = {}
    for y in range(_BAND_TOP, _BAND_BOTTOM + 1):
        previous = None
        row: set[int] = set()
        for x in range(15, _WIDTH // 2):
            current = _classify(x, y, image)
            if previous in ("a", "h") and current in ("a", "h") and current != previous:
                row.add(x)
            if current in ("a", "h"):
                previous = current
        edges[y] = row
    return edges


def test_fill_has_two_tone_stripes(qt_app):
    image = _image(100)
    tones = {_classify(x, y, image)
             for y in range(_BAND_TOP, _BAND_BOTTOM + 1)
             for x in range(15, _WIDTH - 15)}
    assert {"a", "h"} <= tones, f"缺少两种色带：{tones}"


@pytest.mark.parametrize("value", (100, 70, 40))
def test_stripes_slant_down_right(qt_app, value):
    """CSS 45deg 的斜纹向右下倾斜：相邻两行的色带边界逐行右移一格。"""
    edges = _band_edges(value)
    rows = sorted(edges)
    assert sum(len(edges[y]) for y in rows) >= 4, "色带边界太少，本用例已失去检出能力"
    right = left = 0
    for upper, lower in zip(rows, rows[1:]):
        for x in edges[upper]:
            right += x + 1 in edges[lower]
            left += x - 1 in edges[lower]
    assert right > 0, "未找到任何向右下延续的色带边界"
    assert left == 0, f"出现向左下倾斜的色带边界：{left}"


def test_fill_grows_with_value(qt_app):
    def fill_columns(value: int) -> int:
        image = _image(value)
        return sum(1 for x in range(15, _WIDTH - 15)
                   if _classify(x, 8, image) in ("a", "h"))

    assert fill_columns(100) > fill_columns(50) > fill_columns(0)


def test_handle_tracks_value(qt_app):
    def handle_center_x(value: int) -> float:
        image = _image(value)
        target = tuple(int(theme.SURFACE_WARM[i:i + 2], 16) for i in (1, 3, 5))
        hits = []
        for y in range(_BAND_TOP - 2, _BAND_BOTTOM + 2):
            for x in range(_WIDTH):
                color = image.pixelColor(x, y)
                rgb = (color.red(), color.green(), color.blue())
                if all(abs(a - b) <= _TOLERANCE for a, b in zip(rgb, target)):
                    hits.append(x)
        assert hits, "滑块本体缺失"
        return sum(hits) / len(hits)

    assert handle_center_x(100) - handle_center_x(0) > _WIDTH // 2
