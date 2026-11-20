"""UI 令牌与样式护栏：色值来源、QSS 可解析、工具样式无硬编码、图标染色。"""

import re
from pathlib import Path

import pytest
from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtWidgets import QApplication

from pet import theme
from pet.ui.icons import tinted_icon
from pet.ui.qss import app_stylesheet

_ROOT = Path(__file__).resolve().parent.parent
_HEX_VALUE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_HEX_LITERAL = re.compile(r"#[0-9A-Fa-f]{6}\b")
_TOOL_STYLES = sorted((_ROOT / "pet" / "tools").glob("*/style.py"))


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def test_color_tokens_are_hex():
    tokens = {
        name: value for name, value in vars(theme).items()
        if name.isupper() and isinstance(value, str)
        and not name.endswith("_CSS")
    }
    assert tokens
    invalid = [f"{name}={value}" for name, value in tokens.items()
               if not _HEX_VALUE.match(value)]
    assert not invalid, invalid


def test_compat_layer_keeps_old_names():
    from pet.ui import styles

    for name in ("_COLOR_BG", "_COLOR_TEXT_TITLE", "_COLOR_ACCENT",
                 "BUTTON_QSS", "INPUT_QSS", "TAB_BAR_QSS",
                 "WINDOW_RADIUS", "bubble_column_y", "make_close_button"):
        assert hasattr(styles, name), name
    assert styles._COLOR_BG == theme.PAPER


def test_app_stylesheet_parses(qt_app):
    messages = []
    previous = qInstallMessageHandler(
        lambda mode, context, message: messages.append(message))
    try:
        qt_app.setStyleSheet(app_stylesheet())
    finally:
        qt_app.setStyleSheet("")
        qInstallMessageHandler(previous)
    problems = [m for m in messages
                if "Could not parse" in m or "Unknown property" in m]
    assert not problems, problems


def test_tool_style_files_have_no_hex_literals():
    assert _TOOL_STYLES, "未找到任何工具的 style.py"
    offenders = []
    for path in _TOOL_STYLES:
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1):
            if _HEX_LITERAL.search(line):
                offenders.append(f"{path.relative_to(_ROOT).as_posix()}:{lineno}")
    assert not offenders, offenders


def test_tinted_icon_reuses_cache(qt_app):
    path = str(_ROOT / "assets" / "icon" / "chat.png")
    tinted_icon.cache_clear()
    first = tinted_icon(path, theme.INK, 20)
    second = tinted_icon(path, theme.INK, 20)
    assert not first.isNull()
    assert not second.isNull()
    assert tinted_icon.cache_info().hits == 1


def test_radius_for_is_half_height():
    assert theme.radius_for(32) == 16
    assert theme.radius_for(34) == 17
    assert theme.radius_for(0) == 1
