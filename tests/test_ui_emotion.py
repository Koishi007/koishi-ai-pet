"""情绪名白名单：未知名字直接丢弃，不在气泡上显示原文。"""

from pet.brain.output import EMOTION_NAMES
from pet.ui.emotion import EMOTION_MAP, known_emotions


def test_ui_map_covers_all_protocol_emotions():
    assert set(EMOTION_MAP) == set(EMOTION_NAMES)


def test_known_emotions_filters_unknown_names():
    assert known_emotions("happy, hungy，shy") == ["happy", "shy"]


def test_known_emotions_returns_empty_for_unknown():
    assert known_emotions("hungy") == []


def test_known_emotions_maps_emoji_to_name():
    assert known_emotions("😊, shy") == ["happy", "shy"]
