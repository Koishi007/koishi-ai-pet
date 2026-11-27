"""推理强度下拉的读写：显示文案与存储值分离，选项按通道声明过滤。"""

import pytest
from PySide6.QtWidgets import QApplication

from pet.brain.reasoning import EFFORT_IDS
from pet.ui.settings_fields import SettingsForm
from pet.ui.settings_spec import EFFORT_CHOICES, EFFORT_DEFAULT_LABEL, effort_items

KEYS = ("LLM_REASONING_EFFORT", "LLM_REASONING_EFFORT_ALT")


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(params=KEYS)
def form_and_key(qt_app, request):
    form = SettingsForm()
    form.combo_with_data(request.param, list(EFFORT_CHOICES))
    return form, request.param


def test_choices_cover_vocabulary():
    values = [value for _label, value in EFFORT_CHOICES]
    assert values == [""] + list(EFFORT_IDS)


def test_items_drop_undeclared_efforts():
    values = [value for _label, value in effort_items(("low", "high"), EFFORT_DEFAULT_LABEL)]
    assert values == ["", "low", "high"]


def test_collect_returns_stored_value_not_label(form_and_key):
    form, key = form_and_key
    widget = form.fields[key]
    widget.setCurrentIndex(widget.findData("high"))
    values, invalid = form.collect()
    assert invalid == []
    assert values[key] == "high"


def test_collect_empty_value_means_no_field(form_and_key):
    form, key = form_and_key
    widget = form.fields[key]
    widget.setCurrentIndex(widget.findData(""))
    assert form.collect()[0][key] == ""


def test_load_selects_item_by_value(form_and_key):
    form, key = form_and_key

    class Config:
        pass

    setattr(Config, key, "low")
    form.load(Config())
    assert form.fields[key].currentData() == "low"


def test_load_unknown_value_falls_back_to_first(form_and_key):
    form, key = form_and_key

    class Config:
        pass

    setattr(Config, key, "ultra")
    form.load(Config())
    assert form.fields[key].currentData() == ""


def test_snapshot_uses_stored_value(form_and_key):
    form, key = form_and_key
    widget = form.fields[key]
    widget.setCurrentIndex(widget.findData("medium"))
    form.take_snapshot()
    assert form.snapshot[key] == "medium"
    assert form.is_dirty() is False
    widget.setCurrentIndex(widget.findData(""))
    assert form.is_dirty() is True
