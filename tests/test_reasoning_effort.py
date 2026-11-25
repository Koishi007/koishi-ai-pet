"""推理强度的中性词表、端点预设、方案取值与通道声明。"""

import pytest

from pet.brain.llm_client import (resolve_reasoning_effort,
                                  resolve_reasoning_efforts)
from pet.brain.providers import gemini_provider, openai_provider
from pet.brain.providers.registry import (PROVIDER_SPECS, config_keys,
                                          config_keys_for, reasoning_efforts)
from pet.brain.reasoning import (EFFORT_IDS, effective_efforts, normalize,
                                 parse_efforts, preset_efforts)
from pet.config import _KEY_META, config


@pytest.mark.parametrize("raw, expected", [
    ("high", "high"),
    (" HIGH ", "high"),
    ("minimal", "minimal"),
    ("", ""),
    (None, ""),
    ("ultra", ""),
])
def test_normalize_keeps_only_declared_ids(raw, expected):
    assert normalize(raw) == expected


def test_key_meta_enums_match_vocabulary():
    expected = [""] + list(EFFORT_IDS)
    assert _KEY_META["LLM_REASONING_EFFORT"]["enum"] == expected
    assert _KEY_META["LLM_REASONING_EFFORT_ALT"]["enum"] == expected


@pytest.mark.parametrize("name", list(PROVIDER_SPECS))
def test_declared_efforts_are_neutral_ids(name):
    assert set(reasoning_efforts(name)) <= set(EFFORT_IDS)


def test_provider_wire_tables_match_registry():
    assert set(openai_provider.EFFORT_WIRE) == set(reasoning_efforts("openai"))
    assert set(gemini_provider.EFFORT_WIRE) == set(reasoning_efforts("gemini"))


def test_gemini_offers_four_levels():
    assert set(reasoning_efforts("gemini")) == {"minimal", "low", "medium", "high"}


def test_openai_offers_xhigh():
    assert "xhigh" in reasoning_efforts("openai")


@pytest.mark.parametrize("name", list(PROVIDER_SPECS))
def test_effort_keys_belong_to_every_channel(name):
    assert "LLM_REASONING_EFFORT" in config_keys(name)
    assert "LLM_REASONING_EFFORT_ALT" in config_keys_for(name, "alternative")


def test_alternative_effort_inherits_primary(monkeypatch):
    monkeypatch.setattr(config, "LLM_REASONING_EFFORT", "high")
    monkeypatch.setattr(config, "LLM_REASONING_EFFORT_ALT", "")
    assert resolve_reasoning_effort("primary") == "high"
    assert resolve_reasoning_effort("alternative") == "high"


def test_each_profile_keeps_its_own_effort(monkeypatch):
    monkeypatch.setattr(config, "LLM_REASONING_EFFORT", "high")
    monkeypatch.setattr(config, "LLM_REASONING_EFFORT_ALT", "low")
    assert resolve_reasoning_effort("primary") == "high"
    assert resolve_reasoning_effort("alternative") == "low"


def test_unknown_effort_resolves_to_empty(monkeypatch):
    monkeypatch.setattr(config, "LLM_REASONING_EFFORT", "ultra")
    monkeypatch.setattr(config, "LLM_REASONING_EFFORT_ALT", " ULTRA ")
    assert resolve_reasoning_effort("primary") == ""
    assert resolve_reasoning_effort("alternative") == ""


@pytest.mark.parametrize("model, expected", [
    ("mimo-v2.6-flash", ("low", "medium", "high")),
    ("gpt-5.2-chat-latest", ("low", "medium", "high", "xhigh")),
    ("gpt-5.1", ("low", "medium", "high", "xhigh")),
    ("gpt-5-mini", ("minimal", "low", "medium", "high")),
    ("o3-mini", ("low", "medium", "high")),
    ("deepseek-flash", ("low", "high", "max")),
    ("glm-5.3", ("low", "high", "max")),
    ("glm-5.2", ("high", "max")),
    ("glm-4.6", ()),
    ("qwen4-max", ("low", "high", "xhigh")),
    ("qwen3.8-max", ("low", "medium", "xhigh")),
    ("qwen-plus", ()),
    ("llama3.2", None),
])
def test_preset_efforts_by_model(model, expected):
    assert preset_efforts(model) == expected


def test_parse_efforts_accepts_chinese_comma_and_drops_unknown():
    assert parse_efforts(" low，HIGH , ultra ") == ("low", "high")
    assert parse_efforts("") is None
    assert parse_efforts(None) is None


def test_override_wins_over_preset():
    assert effective_efforts(EFFORT_IDS, "low,high", "mimo-v2.6-flash") == ("low", "high")


def test_preset_used_when_override_blank():
    assert effective_efforts(EFFORT_IDS, "", "deepseek-flash") == ("low", "high", "max")


def test_declared_used_when_no_preset():
    assert effective_efforts(("minimal", "low"), None, "unknown-model") == ("minimal", "low")


def test_result_follows_channel_order():
    assert effective_efforts(("low", "high"), "max,high,low", "x") == ("low", "high")


def test_preset_cannot_add_levels_outside_channel():
    assert effective_efforts(("minimal", "low", "medium", "high"), "",
                             "gpt-5.2") == ("low", "medium", "high")


def test_resolve_drops_effort_outside_preset(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "LLM_MODEL", "mimo-v2.6-flash")
    monkeypatch.setattr(config, "LLM_REASONING_EFFORT", "xhigh")
    monkeypatch.setattr(config, "LLM_REASONING_EFFORTS", "")
    assert resolve_reasoning_efforts("primary") == ("low", "medium", "high")
    assert resolve_reasoning_effort("primary") == ""


def test_resolve_accepts_effort_inside_override(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "LLM_MODEL", "mimo-v2.6-flash")
    monkeypatch.setattr(config, "LLM_REASONING_EFFORT", "xhigh")
    monkeypatch.setattr(config, "LLM_REASONING_EFFORTS", "xhigh, low")
    assert resolve_reasoning_efforts("primary") == ("low", "xhigh")
    assert resolve_reasoning_effort("primary") == "xhigh"
