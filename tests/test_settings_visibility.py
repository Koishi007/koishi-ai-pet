"""通道专属键的显隐与推理强度选项：控件与标签必须一起隐藏。

QFormLayout 的标签是独立 QLabel，只对控件 setVisible 会留下孤立的标签。
页面未显示时 isVisibleTo 不可靠，这里断言 isHidden。
切换通道走下拉的信号，覆盖「改了选项就自动重算」这条用户可见契约。
"""

import pytest
from PySide6.QtWidgets import QApplication

from pet.brain.providers.registry import (PROVIDER_SPECS, config_keys,
                                          config_keys_for, reasoning_efforts,
                                          url_placeholder)
from pet.ui.settings_fields import SettingsForm
from pet.ui.settings_tabs import SettingsTabs


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tabs(qt_app):
    form = SettingsForm()
    built = SettingsTabs(form)
    built.pages = built.build()
    built.bind(_NoopActions())
    # 打开设置页时按当前调用模式跑一次初始显隐
    built.on_mode_changed(built.widgets.brain_combo.currentText())
    return built


class _NoopActions:
    """tabs.bind 需要动作对象；显隐用例不触发任何动作。"""

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _select(tabs, provider: str, alt: bool = False) -> None:
    """按存储值选中通道；下拉显示的是中文文案，不能用 findText。"""
    combo = tabs.widgets.provider_alt_combo if alt else tabs.widgets.provider_combo
    combo.setCurrentIndex(combo.findData(provider))


def _pairs(combo):
    return [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]


def _values(combo):
    return [combo.itemData(i) for i in range(combo.count())]


def test_effort_row_visible_for_supported_channel(tabs):
    _select(tabs, "gemini")
    w = tabs.widgets
    assert w.effort_combo.isHidden() is False
    assert w.effort_label.isHidden() is False


def test_gemini_keeps_url_for_custom_endpoint(tabs):
    """Gemini 也支持自定义端点，地址字段保持可见。"""
    _select(tabs, "gemini")
    w = tabs.widgets
    assert w.url_edit.isHidden() is False
    assert w.url_label.isHidden() is False


def test_url_placeholder_follows_channel(tabs):
    """切换通道时占位符换成该通道登记行声明的端点示例。"""
    w = tabs.widgets
    for name in PROVIDER_SPECS:
        _select(tabs, name)
        assert w.url_edit.placeholderText() == url_placeholder(name)


def test_switching_back_restores_url(tabs):
    _select(tabs, "gemini")
    _select(tabs, "openai")
    w = tabs.widgets
    assert w.url_edit.isHidden() is False
    assert w.url_label.isHidden() is False


def test_channel_combos_share_choices(tabs):
    """首选与备选通道的选项与顺序完全一致。"""
    w = tabs.widgets
    assert _pairs(w.provider_alt_combo) == _pairs(w.provider_combo)


@pytest.mark.parametrize("alt_name", list(PROVIDER_SPECS))
def test_alt_url_stays_visible_for_every_channel(tabs, alt_name):
    """两个通道都读地址，备选分组不因通道不同而隐藏地址。"""
    _select(tabs, "openai")
    _select(tabs, alt_name, alt=True)
    w = tabs.widgets
    assert w.alt_url_edit.isHidden() is False
    assert w.alt_url_label.isHidden() is False


@pytest.mark.parametrize("alt_name", list(PROVIDER_SPECS))
def test_alt_placeholder_follows_alt_channel(tabs, alt_name):
    _select(tabs, alt_name, alt=True)
    assert tabs.widgets.alt_url_edit.placeholderText() == url_placeholder(alt_name)


def test_channel_combo_has_no_bare_value_labels(tabs):
    """选项显示的必须是文案，不能是配置值本身。"""
    combo = tabs.widgets.provider_combo
    labels = [combo.itemText(i) for i in range(combo.count())]
    for name in PROVIDER_SPECS:
        assert name not in labels


@pytest.mark.parametrize("name", list(PROVIDER_SPECS))
def test_registry_declares_effort_for_every_channel(name):
    assert "LLM_REASONING_EFFORT" in config_keys(name)
    assert "LLM_REASONING_EFFORT_ALT" in config_keys_for(name, "alternative")


@pytest.mark.parametrize("name", list(PROVIDER_SPECS))
def test_effort_options_follow_primary_channel(tabs, name):
    _select(tabs, name)
    assert _values(tabs.widgets.effort_combo) == [""] + list(reasoning_efforts(name))


@pytest.mark.parametrize("name", list(PROVIDER_SPECS))
def test_alt_effort_options_follow_alt_channel(tabs, name):
    _select(tabs, "openai")
    _select(tabs, name, alt=True)
    assert _values(tabs.widgets.alt_effort_combo) == [""] + list(reasoning_efforts(name))


def test_effort_combos_disabled_when_thinking_off(tabs):
    """关闭思考时档位不下发，两个下拉一起禁用并给出提示。"""
    w = tabs.widgets
    w.brain_combo.setCurrentText("api")
    w.thinking_check.setChecked(False)
    assert w.effort_combo.isEnabled() is True
    assert w.effort_combo.toolTip() == ""
    assert w.alt_effort_combo.isEnabled() is True
    w.thinking_check.setChecked(True)
    assert w.effort_combo.isEnabled() is False
    assert w.effort_combo.toolTip() != ""
    assert w.alt_effort_combo.isEnabled() is False


def test_effort_options_follow_model_preset(tabs):
    w = tabs.widgets
    _select(tabs, "openai")
    w.model_edit.setText("mimo-v2.6-flash")
    assert _values(w.effort_combo) == ["", "low", "medium", "high"]


def test_effort_options_follow_hidden_endpoint_config(tabs, monkeypatch):
    """端点档位是高级配置，只从 settings.json 生效。"""
    from pet.config import config
    monkeypatch.setattr(config, "LLM_REASONING_EFFORTS", "low,high")
    w = tabs.widgets
    _select(tabs, "openai")
    w.model_edit.setText("mimo-v2.6-flash")
    assert _values(w.effort_combo) == ["", "low", "high"]


def test_effort_row_greyed_when_endpoint_has_no_levels(tabs):
    """端点没有档位时保留整行，灰置并给出提示。"""
    w = tabs.widgets
    _select(tabs, "openai")
    w.brain_combo.setCurrentText("api")
    w.model_edit.setText("mimo-v2.6-flash")
    assert w.effort_combo.isEnabled() is True
    assert w.effort_combo.toolTip() == ""

    w.model_edit.setText("glm-4.6")
    assert w.effort_combo.isHidden() is False
    assert w.effort_combo.isEnabled() is False
    assert w.effort_combo.toolTip() != ""
    assert w.effort_combo.count() == 1
