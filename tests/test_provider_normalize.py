"""通道名归一化与不可用原因。

空值与大小写归一到登记表的键；未知值不静默回退，避免填错时悄悄走默认通道。
"""

import pytest

from pet.brain.llm_errors import ProviderUnavailable
from pet.brain.providers import LLMConnection
from pet.brain.providers.registry import (DEFAULT_PROVIDER, PROVIDER_SPECS,
                                          build_provider, normalize_provider)


@pytest.mark.parametrize("raw", [None, "", "   ", "\t\n"])
def test_empty_falls_back_to_default(raw):
    assert normalize_provider(raw) == DEFAULT_PROVIDER


@pytest.mark.parametrize("raw, expected", [
    ("OPENAI", "openai"),
    (" Gemini ", "gemini"),
    ("gemini", "gemini"),
])
def test_case_and_whitespace_insensitive(raw, expected):
    assert normalize_provider(raw) == expected


def test_unknown_is_preserved():
    """未知通道不归到默认值，由 build_provider 判为不可用并记日志。"""
    assert normalize_provider("vertex") == "vertex"


def test_default_provider_exists_in_table():
    from pet.brain.providers.registry import PROVIDER_SPECS
    assert DEFAULT_PROVIDER in PROVIDER_SPECS


def test_build_empty_provider_yields_openai():
    provider = build_provider(LLMConnection(provider="", key="k",
                                            url="http://127.0.0.1:1/v1", model="m"))
    assert provider is not None
    assert provider.name == "openai"
    provider.close()


def test_build_unknown_provider_raises():
    """未知通道不静默回落：抛出的错误带上出错的值与可选项。"""
    with pytest.raises(ProviderUnavailable) as info:
        build_provider(LLMConnection(provider="vertex", key="k"))
    message = str(info.value)
    assert "vertex" in message
    for name in PROVIDER_SPECS:
        assert name in message


def test_unavailable_reason_names_the_dependency(monkeypatch):
    """缺依赖时错误里带上该通道声明的依赖名，用户据此安装。"""
    import builtins
    real_import = builtins.__import__

    def deny(name, *args, **kwargs):
        if name.startswith("google"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", deny)
    with pytest.raises(ProviderUnavailable) as info:
        build_provider(LLMConnection(provider="gemini", key="k",
                                     model="gemini-3-flash"))
    assert "google-genai" in str(info.value)
