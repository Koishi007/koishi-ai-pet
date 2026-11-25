"""通道登记表：有哪些通道、每个通道读哪些配置键。"""

import importlib
import logging
from contextlib import contextmanager
from typing import Callable

from pet.brain.llm_errors import ProviderUnavailable
from pet.brain.providers import LLMConnection

logger = logging.getLogger(__name__)

# 每行七个字段：界面显示名、延迟导入的目标、随方案换算的配置键、方案无关的共用键、
# 可选依赖、地址占位符、该通道提供的推理强度档位。键的类型与默认值仍在 pet/config.py 的 _KEY_META。
PROVIDER_SPECS: dict = {
    "openai": {
        "name": "openai",
        "label": "OpenAI 兼容格式",
        "url_hint": "https://api.example.com/v1",
        "target": ("pet.brain.providers.openai_provider", "OpenAIProvider"),
        "config_keys": ("LLM_URL", "LLM_KEY", "LLM_MODEL", "LLM_REASONING_EFFORT"),
        "shared_keys": (),
        "optional_deps": (),
        "reasoning_efforts": ("minimal", "low", "medium", "high", "xhigh", "max"),
    },
    "gemini": {
        "name": "gemini",
        "label": "Google Gemini",
        "url_hint": "https://generativelanguage.googleapis.com",
        "target": ("pet.brain.providers.gemini_provider", "GeminiProvider"),
        "config_keys": ("LLM_URL", "LLM_KEY", "LLM_MODEL", "LLM_REASONING_EFFORT"),
        "shared_keys": (),
        "optional_deps": ("google-genai>=3.6",),
        "reasoning_efforts": ("minimal", "low", "medium", "high"),
    },
}


DEFAULT_PROVIDER = "openai"


def provider_names() -> tuple:
    """通道名，顺序即界面下拉顺序。"""
    return tuple(PROVIDER_SPECS)


def channel_choices() -> tuple:
    """通道下拉的 (显示文案, 值) 列表。"""
    return tuple((entry["label"], name) for name, entry in PROVIDER_SPECS.items())


def url_placeholder(name: str) -> str:
    """通道的 API 地址占位符；未知通道用默认通道的。"""
    entry = PROVIDER_SPECS.get(name) or PROVIDER_SPECS[DEFAULT_PROVIDER]
    return entry["url_hint"]


def normalize_provider(name: str | None) -> str:
    """通道名归一化：只做去空白与转小写，空值回落到默认通道。

    未知值原样返回，由 build_provider 判为不可用，避免填错时静默走默认通道。
    """
    key = (name or "").strip().lower()
    return key or DEFAULT_PROVIDER


def config_keys(name: str) -> tuple:
    """指定通道的配置键；未知通道返回空。"""
    entry = PROVIDER_SPECS.get(name) or {}
    return tuple(entry.get("config_keys") or ()) + tuple(entry.get("shared_keys") or ())


def shared_keys(name: str) -> tuple:
    """方案无关的共用键：备选方案复用同一份取值。"""
    entry = PROVIDER_SPECS.get(name) or {}
    return tuple(entry.get("shared_keys") or ())


def reasoning_efforts(name: str) -> tuple:
    """该通道提供的推理强度档位；未知通道返回空。"""
    entry = PROVIDER_SPECS.get(name) or {}
    return tuple(entry.get("reasoning_efforts") or ())


# 备选方案的键名与首选不同，按同一通道归属换算
_ALT_SUFFIX = {
    "LLM_URL": "LLM_URL_ALT",
    "LLM_KEY": "LLM_KEY_ALT",
    "LLM_MODEL": "LLM_MODEL_ALT",
    "LLM_REASONING_EFFORT": "LLM_REASONING_EFFORT_ALT",
}


def config_keys_for(name: str, profile: str = "primary") -> tuple:
    """指定方案下的配置键；方案键换成备选键名，共用键原样。"""
    entry = PROVIDER_SPECS.get(name) or {}
    keys = tuple(entry.get("config_keys") or ())
    if profile == "alternative":
        keys = tuple(_ALT_SUFFIX.get(key, key) for key in keys)
    return keys + tuple(entry.get("shared_keys") or ())


def _load(module_name: str, class_name: str):
    """延迟导入 provider 类，打断 config 到 providers 的回环。"""
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


@contextmanager
def temporary_provider(connection: LLMConnection, timeout: float | None = None,
                       reasoning_effort: str = ""):
    """构建一次性 provider，退出时关闭；调用方不必自己写 try/finally。"""
    provider = build_provider(connection, timeout, reasoning_effort=reasoning_effort)
    try:
        yield provider
    finally:
        provider.close()


def build_provider(connection: LLMConnection, timeout: float | None = None,
                   reasoning_effort: str = ""):
    """按登记表构建 provider。

    未知通道或依赖缺失时抛 ProviderUnavailable，消息里给出该通道的修复方式。
    """
    name = normalize_provider(connection.provider)
    entry = PROVIDER_SPECS.get(name)
    if entry is None:
        raise ProviderUnavailable(
            f"未知通道 {name!r}，可选：{', '.join(PROVIDER_SPECS)}")
    module_name, class_name = entry["target"]
    try:
        factory: Callable = _load(module_name, class_name)
        return factory(connection, timeout, reasoning_effort=reasoning_effort)
    except ImportError as exc:
        deps = ", ".join(entry["optional_deps"]) or "未声明"
        raise ProviderUnavailable(
            f"通道 {name!r} 缺少依赖 {deps}：{exc}") from exc
