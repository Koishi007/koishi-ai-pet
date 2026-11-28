"""发声门面：按当前配置把文本合成音节并交给播放通道。"""

import logging

from pet.config import config
from pet.voice import syllable, waveform
from pet.ui.audio.audio_out import AudioOut

logger = logging.getLogger(__name__)

_ASK_CHARS = tuple(syllable.ASK_CHARS)
_SHOUT_CHARS = tuple(syllable.SHOUT_CHARS)


class Vocalizer:
    """静音态只存内存：初值取自「允许发出声音」，设置改了跟着变，右键菜单可覆盖。

    语气状态跨 speak() 调用保留，clear() 复位。
    """

    def __init__(self, audio: AudioOut | None = None):
        self._audio = audio if audio is not None else AudioOut(True)
        self._enabled_seen = config.VOICE_OUTPUT_ENABLED
        self._muted = not self._enabled_seen
        self._shift = 0
        self._applied = syllable.Tone()

    @property
    def audio(self) -> AudioOut:
        return self._audio

    @property
    def muted(self) -> bool:
        self._sync_from_config()
        return self._muted

    @property
    def tone(self) -> syllable.Tone:
        """最近一次实际用于合成的语气，供用例断言。"""
        return self._applied

    def _sync_from_config(self) -> None:
        """设置页改了「允许发出声音」时，内存静音态跟着改。"""
        enabled = bool(config.VOICE_OUTPUT_ENABLED)
        if enabled == self._enabled_seen:
            return
        self._enabled_seen = enabled
        self._muted = not enabled
        if self._muted:
            self._audio.clear()
        logger.info(f"[Vocalizer] 跟随设置静音={self._muted}")

    def set_muted(self, muted: bool) -> None:
        """右键菜单的临时静音：开启时丢弃已排队的音节。"""
        self._enabled_seen = bool(config.VOICE_OUTPUT_ENABLED)
        self._muted = bool(muted)
        if self._muted:
            self._audio.clear()
        logger.info(f"[Vocalizer] muted={self._muted}")

    def _track_tone(self, text: str) -> syllable.Tone:
        """按本字符携带的标点给出语气；整段升降跨字符累积，句尾滑音不累积。"""
        stripped = text.rstrip()
        if any(char in text for char in _SHOUT_CHARS):
            self._shift = syllable.SHOUT_SHIFT
        elif stripped.endswith(syllable.ELLIPSIS_MARKS):
            self._shift = syllable.SAD_SHIFT
        glide = 0
        if stripped.endswith(_ASK_CHARS):
            glide = syllable.ASK_GLIDE
        elif stripped.endswith(syllable.ELLIPSIS_MARKS):
            glide = syllable.SAD_GLIDE
        return syllable.Tone(self._shift, glide)

    def speak(self, text: str) -> None:
        """合成并播放一段文本；静音时清空已排队的音节。"""
        self._sync_from_config()
        if self._muted:
            self._audio.clear()
            return
        if not text:
            return
        try:
            tone = self._track_tone(text)
            self._applied = tone
            syllables = syllable.split(text, config.VOICE_OUTPUT_PRESET, tone=tone)
            if not syllables:
                return
            pcm = waveform.render(syllables, config.VOICE_OUTPUT_VOLUME / 100)
        except Exception:
            logger.exception("语音合成失败，跳过本次发声")
            return
        self._audio.feed(pcm)

    def clear(self) -> None:
        """作废已排队的音节，并复位本段语气。"""
        self._reset_tone()
        self._audio.clear()

    def _reset_tone(self) -> None:
        self._shift = 0
        self._applied = syllable.Tone()

    def shutdown(self) -> None:
        self._audio.shutdown()
