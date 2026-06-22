"""语音会话编排：长连接 + 热键触发识别。"""

import logging

from PySide6.QtCore import QObject, Signal

from pet.voice.mic_capture import MicCapture
from pet.voice.xunfei_stt import XunfeiSTT

logger = logging.getLogger(__name__)


class VoiceSession(QObject):
    """编排麦克风采集和讯飞识别。

    connect() — 启动时建立 WS 长连接
    start_recording() / stop_recording() — 每轮识别
    disconnect() — 清理
    """

    partial_text = Signal(str)
    transcription_done = Signal(str)
    recording_started = Signal()
    recording_stopped = Signal()
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mic = MicCapture(self)
        self._stt = XunfeiSTT(self)
        self._recording = False

        self._mic.audio_chunk.connect(self._on_audio_chunk)
        self._mic.error_occurred.connect(self.error.emit)
        self._stt.partial_result.connect(self.partial_text.emit)
        self._stt.done.connect(self._on_stt_done)
        self._stt.error.connect(self.error.emit)

    def connect(self):
        """启动时建立 WS 长连接。"""
        self._stt.connect()

    def disconnect(self):
        """关闭 WS 连接，停止一切。"""
        self._mic.stop()
        self._stt.disconnect()

    def start_recording(self):
        """开始录音+识别。"""
        if self._recording:
            return
        self._recording = True
        self._stt.start_recording()
        self._mic.start()
        self.recording_started.emit()
        logger.info("[VoiceSession] recording started")

    def _on_audio_chunk(self, data: bytes):
        if not self._recording:
            return
        self._stt.send_audio(data)

    def stop_recording(self):
        """结束录音，等待识别结果。"""
        if not self._recording:
            return
        self._recording = False
        self._mic.stop()
        self._stt.stop_recording()
        self.recording_stopped.emit()
        logger.info("[VoiceSession] recording stopped")

    def _on_stt_done(self, text: str):
        self.transcription_done.emit(text)

    def test_connection(self, app_id="", api_key="", api_secret="") -> bool:
        return self._stt.test_connection(app_id, api_key, api_secret)

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def is_connected(self) -> bool:
        return self._stt.is_connected
