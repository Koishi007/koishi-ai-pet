"""语音会话编排：MicCapture 麦克风采集 → XunfeiSTT 讯飞识别 完整流程。"""

import logging

from PySide6.QtCore import QObject, Signal

from pet.voice.mic_capture import MicCapture
from pet.voice.xunfei_stt import XunfeiSTT

logger = logging.getLogger(__name__)


class VoiceSession(QObject):
    """编排麦克风采集和讯飞识别。

    调用 start() 开始录音+识别，stop() 结束。
    识别文字实时通过 partial_text 发出。
    """

    partial_text = Signal(str)        # 实时中间结果
    transcription_done = Signal(str)  # 结束后的完整文字
    recording_started = Signal()
    recording_stopped = Signal()
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mic = MicCapture(self)
        self._stt = XunfeiSTT(self)
        self._recording = False
        self._mic_started = False

        # 连接信号
        self._mic.audio_chunk.connect(self._on_audio_chunk)
        self._mic.started.connect(self._on_mic_started)
        self._mic.error_occurred.connect(self._on_error)
        self._stt.partial_result.connect(self.partial_text.emit)
        self._stt.done.connect(self._on_stt_done)
        self._stt.error.connect(self._on_error)

    def start(self):
        """开始录音+识别。"""
        if self._recording:
            return
        self._recording = True
        self._mic_started = False
        self._mic.start()
        self._stt.start()
        self.recording_started.emit()
        logger.info("[VoiceSession] start")

    def _on_mic_started(self):
        self._mic_started = True

    def _on_audio_chunk(self, data: bytes):
        """Mic 每帧 PCM 数据 → 推给讯飞。"""
        if not self._recording:
            return
        self._stt.send_audio(data)

    def _on_stt_done(self, text: str):
        """讯飞识别完成。"""
        self.transcription_done.emit(text)
        self._cleanup()

    def _on_error(self, msg: str):
        logger.error(f"[VoiceSession] error: {msg}")
        self.error.emit(msg)
        self._cleanup()

    def stop(self):
        """结束录音+识别。"""
        if not self._recording:
            return
        self._recording = False
        self._mic.stop()
        self._stt.stop()
        self.recording_stopped.emit()
        logger.info("[VoiceSession] stop")

    def force_stop(self):
        """立即停止，不等待结果。"""
        self._recording = False
        self._mic.stop()
        self._stt.force_stop()
        self.recording_stopped.emit()

    def _cleanup(self):
        self._recording = False

    @property
    def is_recording(self) -> bool:
        return self._recording
