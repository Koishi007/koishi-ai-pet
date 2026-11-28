"""音节切分：台词文本 → 音节序列，音高与口型只由字符决定，音区由预设给出。

音高与口型的选法参考 bibibobo（MIT）。许可证全文见 LICENSES/bibibobo-MIT.txt。
"""

from dataclasses import dataclass

# 音高预设：id 是配置取值与用例断言的契约标识，界面文案在 pet/ui/settings_spec.py。
# shift 为半音偏移，scale 是 D 小调五声音阶的 MIDI 级数。
# 基准整体低于 D 小调七度，使基频落在 196~349 Hz，低于多数口型的 F1。
PRESETS: dict[str, tuple[int, tuple[int, ...]]] = {
    "deep": (-14, (62, 65, 67, 69, 72)),
    "soft": (-7, (62, 65, 67, 69, 72)),
}

DEFAULT_PRESET = "soft"

# 句读：占一个不发声的音节，时长按停顿倍率放大
PAUSE_CHARS = "，。！？、；：…ー~～,.!?;:"

# 语气标记：整段升降与句尾滑音
SHOUT_CHARS = "！!"
ELLIPSIS_MARKS = ("……", "...", "…")
ASK_CHARS = "？?"
SHOUT_SHIFT = 3
SAD_SHIFT = -3
ASK_GLIDE = 5
SAD_GLIDE = -4

_DURATION = 0.110        # 单个音节秒数
_PAUSE_DURATION = 0.220  # 停顿音节秒数


@dataclass(frozen=True)
class Syllable:
    degree: int       # MIDI 音高
    duration: float   # 秒
    vowel: int        # 口型序号，索引进 waveform.VOWELS
    voiced: bool      # False 表示停顿，只占时间不发声


def normalize_preset(preset: str | None) -> str:
    """未知或空值回落到默认预设。"""
    key = (preset or "").strip().lower()
    return key if key in PRESETS else DEFAULT_PRESET


def syllable_duration() -> float:
    """单个发声的音节时长，与打字间隔无关。"""
    return _DURATION


def _hash(text: str) -> int:
    """字符 → 稳定整数，同一字符恒得同一个值。"""
    value = 7
    for char in text:
        value = (value * 31 + ord(char)) % 9973
    return value


def _is_han(char: str) -> bool:
    code = ord(char)
    return (0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF
            or 0xF900 <= code <= 0xFAFF)


def _is_digit(char: str) -> bool:
    return "0" <= char <= "9"


def _pronounceable(char: str) -> bool:
    return _is_han(char) or _is_digit(char) or char.isalpha()


@dataclass(frozen=True)
class Tone:
    """一段台词的语气：整段半音偏移与句尾滑音。"""

    shift: int = 0
    glide: int = 0


def tone_of(text: str) -> Tone:
    """按标点判定语气。感叹号整段升，省略号整段降且句尾下滑，问号句尾上扬。"""
    tail = (text or "").rstrip()
    shout = any(char in text for char in SHOUT_CHARS)
    sad = tail.endswith(ELLIPSIS_MARKS)
    shift = SHOUT_SHIFT if shout else (SAD_SHIFT if sad else 0)
    if tail.endswith(tuple(ASK_CHARS)):
        return Tone(shift, ASK_GLIDE)
    if sad:
        return Tone(shift, SAD_GLIDE)
    return Tone(shift, 0)


def split(text: str, preset: str = DEFAULT_PRESET,
          tone: Tone | None = None) -> list[Syllable]:
    """台词 → 音节序列。空白与其他不可发音字符跳过。

    tone 为 None 时按 text 自身的标点判定。
    """
    shift, scale = PRESETS[normalize_preset(preset)]
    applied = tone if tone is not None else tone_of(text)
    total = shift + applied.shift
    syllables: list[Syllable] = []
    for char in text or "":
        if char in PAUSE_CHARS:
            # 连续标点只留一个停顿，省略号一次送来时不会连成 2 倍静默
            if syllables and not syllables[-1].voiced:
                continue
            syllables.append(Syllable(0, _PAUSE_DURATION, 0, False))
        elif char.isspace():
            continue
        elif _pronounceable(char):
            seed = _hash(char)
            degree = scale[seed % len(scale)] + total
            syllables.append(Syllable(degree, _DURATION, seed % 5, True))
    if applied.glide:
        _apply_glide(syllables, applied.glide)
    return syllables


def _apply_glide(syllables: list[Syllable], glide: int) -> None:
    """句尾滑音落在最后一个发声的音节上。"""
    for index in range(len(syllables) - 1, -1, -1):
        syllable = syllables[index]
        if syllable.voiced:
            syllables[index] = Syllable(syllable.degree + glide,
                                        syllable.duration, syllable.vowel, True)
            return
