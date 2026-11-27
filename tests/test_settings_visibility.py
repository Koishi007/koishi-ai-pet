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


def _values(combo):
    return [combo.itemData(i) for i in range(combo.count())]


def _own_alt(tabs, model: str = "mimo-v2.6-flash") -> None:
    """把备选填成独立方案：地址、Key 与模型名都要有。"""
    w = tabs.widgets
    w.alt_url_edit.setText("https://alt.example.com/v1")
    w.alt_key_edit.setText("sk-alt")
    w.alt_model_edit.setText(model)


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


@pytest.mark.parametrize("mode,api_visible,ollama_visible,block_visible", [
    ("api", True, False, True),
    ("ollama", False, True, True),
    ("local", False, False, False),
])
def test_mode_change_renders_matching_section(tabs, mode, api_visible,
                                              ollama_visible, block_visible):
    """固定区块按调用模式渲染对应的连接段；local 档整块不渲染。"""
    w = tabs.widgets
    w.brain_combo.setCurrentText(mode)

    assert w.api_section.isHidden() is not api_visible
    assert w.ollama_section.isHidden() is not ollama_visible
    assert w.mode_block.isHidden() is not block_visible
    assert w.local_hint.isHidden() is block_visible


def test_mode_combo_writes_config_value(tabs):
    """调用模式下拉直接写回 BRAIN。"""
    w = tabs.widgets
    w.brain_combo.setCurrentText("ollama")

    values, _invalid = tabs.form.collect()
    assert values["BRAIN"] == "ollama"


def test_alt_channel_selects_its_own_value(tabs, monkeypatch):
    """备选通道按自己的配置选中，不跟首选联动。"""
    from pet.config import config
    w = tabs.widgets
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "LLM_PROVIDER_ALT", "openai")
    tabs.form.load(config)
    assert w.provider_combo.currentData() == "gemini"
    assert w.provider_alt_combo.currentData() == "openai"


def test_alt_channel_does_not_follow_primary(tabs):
    """切换首选通道不改备选通道。"""
    w = tabs.widgets
    _select(tabs, "openai", alt=True)
    _select(tabs, "gemini")
    assert w.provider_alt_combo.currentData() == "openai"
    assert tabs._alt_channel() == "openai"


def test_alt_effort_editable_while_unconfigured(tabs):
    """备选是独立配置：连接没填齐也能先选档位，设值不依赖连接状态。"""
    w = tabs.widgets
    w.brain_combo.setCurrentText("api")
    _select(tabs, "openai")
    _select(tabs, "openai", alt=True)
    for edit in (w.alt_url_edit, w.alt_key_edit, w.alt_model_edit):
        edit.setText("")

    assert w.alt_effort_combo.isEnabled() is True
    # 模型名未知时列通道声明的全部档位，不因连接未填而收窄
    assert _values(w.alt_effort_combo) == [""] + list(reasoning_efforts("openai"))
    assert w.alt_effort_combo.toolTip() == ""


def test_alt_effort_independent_of_primary_connection(tabs):
    """首选配齐不改变备选档位状态，两侧各按自己的通道与模型算。"""
    w = tabs.widgets
    w.brain_combo.setCurrentText("api")
    _select(tabs, "openai")
    _select(tabs, "gemini", alt=True)
    w.model_edit.setText("mimo-v2.6-flash")
    w.alt_model_edit.setText("gemini-2.5-flash")

    assert _values(w.effort_combo) == ["", "low", "medium", "high"]
    assert _values(w.alt_effort_combo) == ["", "minimal", "low", "medium", "high"]


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
    _own_alt(tabs, "unknown-model")
    assert _values(tabs.widgets.alt_effort_combo) == [""] + list(reasoning_efforts(name))


@pytest.mark.parametrize("missing", ["alt_url_edit", "alt_key_edit",
                                     "alt_model_edit"])
def test_alt_effort_keeps_options_when_connection_incomplete(tabs, missing):
    """备选缺项只影响能否发起调用，档位选项不受连接状态影响。"""
    w = tabs.widgets
    w.brain_combo.setCurrentText("api")
    _select(tabs, "openai")
    _own_alt(tabs)
    assert w.alt_effort_combo.isEnabled() is True
    assert _values(w.alt_effort_combo) == ["", "low", "medium", "high"]

    getattr(w, missing).clear()
    assert w.alt_effort_combo.isEnabled() is True
    assert w.alt_effort_combo.toolTip() == ""
    # 选项只随通道与模型名变化：清掉模型名后列通道声明的全量档位
    expected = ([""] + list(reasoning_efforts("openai")) if missing == "alt_model_edit"
                else ["", "low", "medium", "high"])
    assert _values(w.alt_effort_combo) == expected


def test_effort_combos_disabled_when_thinking_off(tabs):
    """关闭思考时档位不下发，两个下拉一起禁用并给出提示。"""
    w = tabs.widgets
    w.brain_combo.setCurrentText("api")
    _own_alt(tabs)
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
