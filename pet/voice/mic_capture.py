"""麦克风 PCM 采集模块"""

import logging
import numpy as np

import sounddevice as sd
from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

# 讯飞要求: PCM 16kHz, 16bit, mono
SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"
FRAME_SIZE = 8000  # 字节，每帧约 0.25s


class MicCapture(QObject):
    """麦克风采集，输出 16kHz 16bit mono PCM 数据流。"""

    audio_chunk = Signal(bytes)   # 每帧 PCM 数据
    started = Signal()
    stopped = Signal()
    error_occurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stream: sd.InputStream | None = None
        self._running = False

    def start(self):
        """打开麦克风，启动采集。"""
        if self._running:
            return
        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=FRAME_SIZE // 2,  # 16bit = 2 bytes per sample
                callback=self._on_audio,
            )
            self._stream.start()
            self._running = True
            self.started.emit()
            logger.info("[MicCapture] started")
        except Exception as e:
            logger.error(f"[MicCapture] start failed: {e}")
            self.error_occurred.emit(str(e))

    def stop(self):
        """停止采集，关闭麦克风。"""
        if not self._running:
            return
        self._running = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self.stopped.emit()
        logger.info("[MicCapture] stopped")

    def _on_audio(self, indata: np.ndarray, frames: int, _time, _status):
        """sounddevice 回调：将 int16 numpy 转为 bytes 发出。"""
        if _status:
            logger.warning(f"[MicCapture] stream status: {_status}")
        if not self._running:
            return
        data = indata.tobytes()  # int16 → bytes
        self.audio_chunk.emit(data)

    @property
    def is_running(self) -> bool:
        return self._running
