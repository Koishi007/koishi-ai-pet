"""音节切分：台词文本 → 音节序列，音高只由字符决定，音区由预设给出。"""

from dataclasses import dataclass

# 音高预设：id 是配置取值与用例断言的契约标识，界面文案在 pet/ui/settings_spec.py
PRESETS: dict[str, tuple[float, float]] = {
    "deep": (240.0, 360.0),
    "soft": (290.0, 430.0),
    "bright": (330.0, 480.0),
    "clear": (380.0, 620.0),
}

DEFAULT_PRESET = "soft"

# 句读：占一个不发声的音节，时长按停顿倍率放大
PAUSE_CHARS = "，。！？、；：…ー~～,.!?;:"

# 拉丁字母与数字的音高在区间内的相对位置，按字母表、数字表顺序
_LETTER_PITCH = (0.52, 0.28, 0.40, 0.35, 0.60, 0.46, 0.38, 0.55, 0.66, 0.32,
                 0.44, 0.50, 0.30, 0.42, 0.64, 0.36, 0.58, 0.48, 0.62, 0.34,
                 0.54, 0.45, 0.33, 0.41, 0.56, 0.31)

_MIN_DURATION = 0.030
_MAX_DURATION = 0.060
_DURATION_RATIO = 0.9
_PAUSE_MULTIPLIER = 2


@dataclass(frozen=True)
class Syllable:
    pitch: float      # 基频 Hz
    duration: float   # 秒
    voiced: bool      # False 表示停顿，只占时间不发声


def normalize_preset(preset: str | None) -> str:
    """未知或空值回落到默认预设。"""
    key = (preset or "").strip().lower()
    return key if key in PRESETS else DEFAULT_PRESET


def syllable_duration(type_interval_ms: float) -> float:
    """音节时长由打字间隔派生，略短于字间隔以免播放队列持续积压。"""
    return min(_MAX_DURATION, max(_MIN_DURATION, type_interval_ms / 1000 * _DURATION_RATIO))


def _hash_unit(text: str) -> float:
    """字符 → [0, 1) 的稳定位置，同一字符恒得同一个值。"""
    value = 0
    for byte in text.encode("utf-8"):
        value = (value * 131 + byte) & 0xFFFFFFFF
    return (value % 1000) / 1000


def _is_han(char: str) -> bool:
    code = ord(char)
    return (0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF
            or 0xF900 <= code <= 0xFAFF)


def _is_digit(char: str) -> bool:
    return "0" <= char <= "9"


def _pitch_for(char: str, low: float, high: float) -> float:
    span = high - low
    if _is_han(char):
        return low + span * _hash_unit(char)
    lower = char.lower()
    if "a" <= lower <= "z":
        ratio = _LETTER_PITCH[ord(lower) - ord("a")]
        return min(high, max(low, low + span * ratio))
    if _is_digit(char):
        ratio = _LETTER_PITCH[ord(char) - ord("0")]
        return min(high, max(low, low + span * ratio))
    return low + span * _hash_unit(char)


def split(text: str, preset: str = DEFAULT_PRESET,
          type_interval_ms: float = 35) -> list[Syllable]:
    """台词 → 音节序列。空白与其他不可发音字符跳过。"""
    low, high = PRESETS[normalize_preset(preset)]
    duration = syllable_duration(type_interval_ms)
    pause = duration * _PAUSE_MULTIPLIER
    syllables: list[Syllable] = []
    for char in text or "":
        if char in PAUSE_CHARS:
            syllables.append(Syllable(0.0, pause, False))
        elif char.isspace():
            continue
        elif _is_han(char) or _is_digit(char) or char.isalpha():
            syllables.append(Syllable(_pitch_for(char, low, high), duration, True))
    return syllables
