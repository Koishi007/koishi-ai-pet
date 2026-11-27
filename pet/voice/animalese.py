"""波形合成：音节序列 → 22.05 kHz 单声道 int16 PCM。"""

import numpy as np

from pet.voice.phoneme import Syllable

SAMPLE_RATE = 22050
_HARMONICS = (0.5, 0.25, 0.12)
_DECAY = 2.2
_FADE_SECONDS = 0.002


def _render_voiced(pitch: float, duration: float) -> np.ndarray:
    count = int(SAMPLE_RATE * duration)
    if count <= 0:
        return np.zeros(0, dtype=np.float32)
    time = np.arange(count, dtype=np.float32) / SAMPLE_RATE
    signal = np.zeros(count, dtype=np.float32)
    for order, amplitude in enumerate(_HARMONICS, start=1):
        signal += amplitude * np.sin(2 * np.pi * pitch * order * time)
    signal *= np.exp(-_DECAY * time / duration).astype(np.float32)
    fade = min(max(1, int(SAMPLE_RATE * _FADE_SECONDS)), count // 2)
    if fade > 0:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        signal[:fade] *= ramp
        signal[-fade:] *= ramp[::-1]
    return signal


def _silence(duration: float) -> np.ndarray:
    return np.zeros(int(SAMPLE_RATE * duration), dtype=np.float32)


def render(syllables: list[Syllable], volume: float = 1.0) -> bytes:
    """音节序列 → PCM。volume 为 0 时输出全零，1 为满量程前的归一化峰值。"""
    if not syllables:
        return b""
    parts = [_render_voiced(s.pitch, s.duration) if s.voiced else _silence(s.duration)
             for s in syllables]
    mono = np.concatenate(parts)
    peak = float(np.abs(mono).max()) if mono.size else 0.0
    if peak > 0.0:
        mono = mono / peak * max(0.0, min(1.0, float(volume)))
    return (np.clip(mono, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
