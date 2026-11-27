"""通道登记表与配置真源的一致性。

PROVIDER_SPECS 是新增的配置派生根，它声明了哪些键属于某个通道；
键本身仍以 pet/config.py 的 _KEY_META 为真源。两张表不一致时给出可读失败。
"""

import pytest

from pet.brain.llm_errors import ProviderUnavailable
from pet.brain.providers.registry import (PROVIDER_SPECS, build_provider,
                                          config_keys, config_keys_for,
                                          provider_names, shared_keys)
from pet.brain.providers import LLMConnection
from pet.config import _KEY_META


def test_every_config_key_exists_in_key_meta():
    missing = []
    for name, entry in PROVIDER_SPECS.items():
        for key in entry["config_keys"]:
            if key not in _KEY_META:
                missing.append(f"{name}:{key}")
    assert not missing, "PROVIDER_SPECS 声明了 _KEY_META 里不存在的键：" + "、".join(missing)


def test_every_spec_has_target_and_label():
    for name, entry in PROVIDER_SPECS.items():
        assert entry["name"] == name
        assert entry["label"]
        module_name, class_name = entry["target"]
        assert module_name.startswith("pet.brain.providers.")
        assert class_name.endswith("Provider")


def test_provider_names_follow_table_order():
    assert provider_names() == tuple(PROVIDER_SPECS)


def test_channel_enum_matches_registry():
    """设置页的通道下拉由登记表派生，配置枚举与它同源。

    pet/config.py 无法 import registry：registry 所在的 pet.brain 包会连带
    导入 behavior 与 llm_client，再 import config 即成环。枚举只能手写，
    一致性靠这条用例守住。
    """
    expected = list(PROVIDER_SPECS)
    assert _KEY_META["LLM_PROVIDER"]["enum"] == expected
    assert _KEY_META["LLM_PROVIDER_ALT"]["enum"] == expected


def test_alt_keys_are_renamed_for_alternative_profile():
    """方案键换成备选键名，共用键原样保留。"""
    assert config_keys_for("openai", "alternative") == (
        "LLM_URL_ALT", "LLM_KEY_ALT", "LLM_MODEL_ALT", "LLM_REASONING_EFFORT_ALT")
    assert config_keys_for("gemini", "alternative") == (
        "LLM_URL_ALT", "LLM_KEY_ALT", "LLM_MODEL_ALT", "LLM_REASONING_EFFORT_ALT")


@pytest.mark.parametrize("name", ["openai", "gemini"])
def test_every_profile_key_has_an_alt_name(name):
    """config_keys 里每一项都是方案键，备选侧逐个换成 _ALT 结尾。"""
    assert config_keys_for(name, "alternative") == tuple(
        key + "_ALT" for key in config_keys(name))


def test_gemini_allows_blank_url():
    """Gemini 留空地址走官方端点，OpenAI 兼容端点必须填地址。"""
    from pet.brain.providers.registry import required_fields, url_optional

    assert url_optional("gemini") is True
    assert url_optional("openai") is False
    assert url_optional("nope") is False
    assert required_fields("gemini") == ("key", "model")
    assert required_fields("openai") == ("key", "url", "model")


def test_channel_choices_have_no_blank_value():
    """通道下拉只有具体通道，两套方案各自选各自的。"""
    from pet.brain.providers.registry import channel_choices

    assert all(value for _label, value in channel_choices())


def test_shared_keys_are_empty():
    """当前没有两套方案共用的通道键。"""
    assert shared_keys("gemini") == ()
    assert shared_keys("openai") == ()


def test_both_channels_read_url():
    """Gemini 也支持自定义端点，两个通道都读地址。"""
    assert "LLM_URL" in config_keys("openai")
    assert "LLM_URL" in config_keys("gemini")


def test_alt_keys_exist_in_key_meta():
    missing = [k for name in PROVIDER_SPECS
               for k in config_keys_for(name, "alternative") if k not in _KEY_META]
    assert not missing, "备选键不在 _KEY_META：" + "、".join(missing)


def test_primary_keys_unchanged_for_primary_profile():
    assert config_keys_for("openai") == config_keys("openai")


def test_config_keys_unknown_provider_is_empty():
    assert config_keys("nope") == ()


def test_build_provider_unknown_name_raises():
    with pytest.raises(ProviderUnavailable):
        build_provider(LLMConnection(provider="nope"))


def test_build_provider_openai_is_available():
    provider = build_provider(LLMConnection(provider="openai", key="k",
                                            url="http://127.0.0.1:1/v1", model="m"))
    assert provider is not None
    assert provider.name == "openai"
    assert provider.capabilities.native_tools is True
    provider.close()


def test_build_provider_missing_sdk_raises(monkeypatch):
    """供应商 SDK 缺失时抛出可读错误，调用方据此降级或提示。"""
    import builtins
    real_import = builtins.__import__

    def deny(name, *args, **kwargs):
        if name.startswith("google"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", deny)
    with pytest.raises(ProviderUnavailable):
        build_provider(LLMConnection(provider="gemini", key="k",
                                     model="gemini-2.5-flash"))