"""波形合成：音节序列 → 22.05 kHz 单声道 int16 PCM。

加法合成：每个谐波的幅度由声门源谱倾斜乘多共振峰包络得出。
五组共振峰覆盖 F1~F5，波形比单声源滤波更平滑，高频毛刺更少。
"""

import math

import numpy as np

from pet.voice.syllable import Syllable

SAMPLE_RATE = 22050

# 五种口型：每种是 F1~F5 五个共振峰频率 Hz
VOWELS = (
    (800, 1200, 2800, 3800, 4800),
    (400, 2200, 3000, 4000, 5000),
    (350, 800, 2600, 3600, 4600),
    (600, 1700, 2800, 3800, 4800),
    (300, 2600, 3100, 4000, 5000),
)

_BANDWIDTH = (90, 110, 140, 180, 220)      # 各共振峰带宽 Hz
_GAIN = (1.0, 0.8, 0.55, 0.35, 0.22)       # 各共振峰权重
_TILT = -6.0                               # 声门源谱倾斜 dB/oct
_MAX_HARMONICS = 60
_NYQUIST_GUARD = 0.45                      # 谐波止于采样率的该比例
_ATTACK = 0.0015
_RELEASE_RATIO = 0.4                       # 收尾占音节时长的比例
_CHIRP = 1.5                               # 字头小滑音，半音数
_CHIRP_DECAY = 30.0
_VIBRATO_RATE = 6.0
_VIBRATO_DEPTH = 0.012


def mtof(midi: float) -> float:
    """MIDI 音高 → 频率 Hz。"""
    return 440.0 * 2 ** ((midi - 69) / 12)


def _formant_gain(frequency: float, vowel: int) -> float:
    """单个频率处的共振峰包络增益，各共振峰按洛伦兹型叠加。"""
    total = 0.0
    for center, bandwidth, gain in zip(VOWELS[vowel % len(VOWELS)],
                                       _BANDWIDTH, _GAIN):
        total += gain / (1.0 + ((frequency - center) / bandwidth) ** 2)
    return total


def _render_voiced(degree: int, duration: float, vowel: int) -> np.ndarray:
    count = int(SAMPLE_RATE * duration)
    if count <= 0:
        return np.zeros(0, dtype=np.float64)
    time = np.arange(count) / SAMPLE_RATE

    pitch = mtof(degree + _CHIRP * np.exp(-time * _CHIRP_DECAY))
    pitch = pitch * (1 + _VIBRATO_DEPTH
                     * np.sin(2 * np.pi * _VIBRATO_RATE * time))
    phase = np.cumsum(2 * np.pi * pitch / SAMPLE_RATE)
    mean_pitch = float(pitch.mean())

    signal = np.zeros(count, dtype=np.float64)
    for order in range(1, _MAX_HARMONICS + 1):
        frequency = mean_pitch * order
        if frequency >= SAMPLE_RATE * _NYQUIST_GUARD:
            break
        source = order ** (_TILT / 6.0206)
        amplitude = source * _formant_gain(frequency, vowel)
        if amplitude < 1e-4:
            continue
        signal += amplitude * np.sin(order * phase)

    release = duration * _RELEASE_RATIO
    envelope = np.minimum(1.0, time / _ATTACK)
    envelope *= np.minimum(1.0, (duration - time) / release)
    return signal * np.clip(envelope, 0.0, 1.0)


def _silence(duration: float) -> np.ndarray:
    return np.zeros(int(SAMPLE_RATE * duration), dtype=np.float64)


def render(syllables: list[Syllable], volume: float = 1.0) -> bytes:
    """音节序列 → PCM。volume 为 0 时输出全零，1 为满量程前的归一化峰值。"""
    if not syllables:
        return b""
    parts = [_render_voiced(s.degree, s.duration, s.vowel) if s.voiced
             else _silence(s.duration) for s in syllables]
    mono = np.concatenate(parts)
    peak = float(np.abs(mono).max()) if mono.size else 0.0
    if peak > 0.0:
        mono = mono / peak * max(0.0, min(1.0, float(volume)))
    return (np.clip(mono, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
