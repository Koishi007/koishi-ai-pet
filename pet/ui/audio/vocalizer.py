"""发声门面：按当前配置把文本合成音节并交给播放通道。"""

import logging

from pet.config import config
from pet.voice import animalese, phoneme
from pet.ui.audio.audio_out import AudioOut

logger = logging.getLogger(__name__)


class Vocalizer:
    """静音态只存内存：初值取自「允许发出声音」，设置改了跟着变，右键菜单可覆盖。"""

    def __init__(self, audio: AudioOut | None = None):
        self._audio = audio if audio is not None else AudioOut(True)
        self._enabled_seen = config.VOICE_OUTPUT_ENABLED
        self._muted = not self._enabled_seen

    @property
    def audio(self) -> AudioOut:
        return self._audio

    @property
    def muted(self) -> bool:
        self._sync_from_config()
        return self._muted

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

    def speak(self, text: str) -> None:
        """合成并播放一段文本；静音时清空已排队的音节。"""
        self._sync_from_config()
        if self._muted:
            self._audio.clear()
            return
        if not text:
            return
        try:
            syllables = phoneme.split(text, config.VOICE_OUTPUT_PRESET)
            if not syllables:
                return
            pcm = animalese.render(syllables, config.VOICE_OUTPUT_VOLUME / 100)
        except Exception:
            logger.exception("语音合成失败，跳过本次发声")
            return
        self._audio.feed(pcm)

    def clear(self) -> None:
        """作废已排队的音节。"""
        self._audio.clear()

    def shutdown(self) -> None:
        self._audio.shutdown()
