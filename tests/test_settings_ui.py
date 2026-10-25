"""设置页：单位换算与可见项覆盖。

界面按秒输入、配置按毫秒存储；hidden=False 的配置项必须在页面上有入口，
否则用户只能手写 settings.json。
"""

import re
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QApplication, QLineEdit

from pet.config import _KEY_META
from pet.ui import settings_window as sw

_SRC = Path(sw.__file__).read_text(encoding="utf-8")
# 循环注册的交互提示词不在调用字面量里
_LOOP_KEYS = {"INTERACT_GRABBED_PROMPT", "INTERACT_RELEASED_PROMPT",
              "INTERACT_WINDOW_DISAPPEARED_PROMPT"}


def _registered_keys() -> set[str]:
    keys = set(re.findall(r'_fields\[\s*"([A-Z_0-9]+)"\s*\]', _SRC))
    keys |= set(re.findall(
        r'self\._(?:line|check|combo|text_area|secret_row)\(\s*"([A-Z_0-9]+)"', _SRC))
    return keys | _LOOP_KEYS


def test_mid_interval_displays_seconds():
    assert sw._display_value("SCHEDULER_MID_MS", 600000) == 600


def test_mid_interval_stores_milliseconds():
    assert sw._stored_value("SCHEDULER_MID_MS", 600) == 600000


def test_other_keys_keep_units():
    assert sw._display_value("LLM_TIMEOUT", 20.0) == 20.0
    assert sw._stored_value("LLM_TIMEOUT", 20.0) == 20.0


def test_visible_keys_have_controls():
    """hidden=False 的项都能在页面上改；LLM_ACTIVE_PROFILE 由切换按钮负责。"""
    visible = {k for k, m in _KEY_META.items() if not m["hidden"]}
    missing = visible - _registered_keys() - {"LLM_ACTIVE_PROFILE"}
    assert missing == set(), f"设置页缺少控件：{sorted(missing)}"


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _collect(edit: QLineEdit):
    """只提供 _fields 的替身：_collect_values 不依赖其它实例状态。"""
    fake = SimpleNamespace(_fields={"SCHEDULER_MID_MS": edit})
    return sw.SettingsWindow._collect_values(fake)


def test_out_of_range_input_is_rejected(qt_app):
    """validator 的越界值只是 Intermediate，保存时要拦下来。"""
    edit = QLineEdit()
    edit.setValidator(QIntValidator(60, 3600))
    edit.setText("6")

    values, invalid = _collect(edit)

    assert invalid == ["SCHEDULER_MID_MS"]
    assert values == {}


def test_valid_input_is_converted_to_ms(qt_app):
    edit = QLineEdit()
    edit.setValidator(QIntValidator(60, 3600))
    edit.setText("600")

    values, invalid = _collect(edit)

    assert invalid == []
    assert values == {"SCHEDULER_MID_MS": 600000}


def _hint_double(key: str, widget, labels: dict):
    fake = SimpleNamespace(_fields={key: widget}, _label_cache=labels)
    fake._field_label = MethodType(sw.SettingsWindow._field_label, fake)
    return sw.SettingsWindow._invalid_hint(fake, key)


def test_invalid_hint_uses_ui_label_and_range(qt_app):
    edit = QLineEdit()
    edit.setValidator(QIntValidator(60, 3600))

    hint = _hint_double("SCHEDULER_MID_MS", edit, {"SCHEDULER_MID_MS": "自主行动间隔(秒)"})

    assert hint == "自主行动间隔(秒)（允许 60–3600）"


def test_invalid_hint_falls_back_to_description(qt_app):
    hint = _hint_double("LLM_MODEL", QLineEdit(), {})

    assert hint == f"{_KEY_META['LLM_MODEL']['description']}（不能为空）"
