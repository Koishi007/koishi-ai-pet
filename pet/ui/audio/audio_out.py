"""播放通道：常驻 QAudioSink，空闲挂起，发声前恢复。"""

import logging
import threading

from PySide6.QtCore import QIODevice, QObject, QThread, Signal

logger = logging.getLogger(__name__)

SAMPLE_RATE = 22050
_BUFFER_BYTES = SAMPLE_RATE  # 0.5 秒缓冲
_MAX_QUEUED_SECONDS = 3.0    # 积压上限，超过则跳过本次喂入


class _Feeder(QIODevice):
    """队列空时返回静音，sink 不会因断流自行停止。"""

    def __init__(self):
        super().__init__()
        self._queue = bytearray()
        self.open(QIODevice.OpenModeFlag.ReadOnly)

    def feed(self, pcm: bytes) -> None:
        self._queue.extend(pcm)

    def pending_bytes(self) -> int:
        return len(self._queue)

    def clear(self) -> None:
        self._queue.clear()

    def readData(self, maxlen: int) -> bytes:
        if not self._queue:
            return b"\x00\x00" * max(1, maxlen // 2)
        chunk = bytes(self._queue[:maxlen])
        del self._queue[:maxlen]
        return chunk

    def bytesAvailable(self) -> int:
        return max(len(self._queue), 8192)

    def writeData(self, data) -> int:
        return 0


class _AudioWorker(QObject):
    """持有 sink 与 feeder，所有 sink 调用都发生在本对象所属线程。"""

    booted = Signal(bool)
    feed_requested = Signal(bytes)
    clear_requested = Signal()
    suspend_requested = Signal()
    shutdown_requested = Signal()

    def __init__(self):
        super().__init__()
        self._feeder = _Feeder()
        self._sink = None
        self._active = False
        self.feed_requested.connect(self._on_feed)
        self.clear_requested.connect(self._on_clear)
        self.suspend_requested.connect(self._on_suspend)
        self.shutdown_requested.connect(self._on_shutdown)

        self._sink = None
        self._feed_requested = False

    def boot(self) -> None:
        from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            logger.warning("没有可用音频输出设备，语音输出关闭")
            self.booted.emit(False)
            return
        fmt = QAudioFormat()
        fmt.setSampleRate(SAMPLE_RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        try:
            self._sink = QAudioSink(device, fmt)
            self._sink.setBufferSize(_BUFFER_BYTES)
            self._sink.start(self._feeder)
        except Exception:
            logger.exception("音频输出初始化失败，语音输出关闭")
            self._sink = None
            self.booted.emit(False)
            return
        # start 之后立即挂起，等第一份 PCM 到了再恢复
        self._sink.suspend()
        self._active = False
        self.booted.emit(True)
        if self._feed_requested:
            self._feed_requested = False
            self._resume()

    def _on_feed(self, pcm: bytes) -> None:
        if self._sink is None:
            # 设备还在初始化：数据先入队，boot 完成后统一恢复播放
            self._feeder.feed(pcm)
            self._feed_requested = True
            return
        limit = int(SAMPLE_RATE * _MAX_QUEUED_SECONDS) * 2
        if self._feeder.pending_bytes() > limit:
            return
        self._feeder.feed(pcm)
        self._resume()

    def _resume(self) -> None:
        if self._sink is None or self._active:
            return
        self._sink.resume()
        self._active = True

    def _on_clear(self) -> None:
        self._feeder.clear()
        self._on_suspend()

    def _on_suspend(self) -> None:
        if self._sink is not None and self._active:
            self._sink.suspend()
            self._active = False

    def _on_shutdown(self) -> None:
        if self._sink is not None:
            self._sink.stop()
            self._sink = None
        self._active = False


class AudioOut:
    """合成结果交给工作线程播放；设备在首次 feed 时初始化，不阻塞调用方。"""

    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self._thread: QThread | None = None
        self._worker: _AudioWorker | None = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _ensure_worker(self) -> _AudioWorker | None:
        with self._lock:
            if self._worker is not None:
                return self._worker
            thread = QThread()
            worker = _AudioWorker()
            worker.moveToThread(thread)
            thread.started.connect(worker.boot)
            thread.start()
            self._thread, self._worker = thread, worker
            return worker

    def feed(self, pcm: bytes) -> None:
        if not self._enabled or not pcm:
            return
        worker = self._ensure_worker()
        if worker is not None:
            worker.feed_requested.emit(pcm)

    def clear(self) -> None:
        worker = self._worker
        if worker is not None:
            worker.clear_requested.emit()

    def suspend(self) -> None:
        worker = self._worker
        if worker is not None:
            worker.suspend_requested.emit()

    def shutdown(self) -> None:
        with self._lock:
            worker, thread = self._worker, self._thread
            self._worker = self._thread = None
        if worker is not None:
            worker.shutdown_requested.emit()
        if thread is not None:
            thread.quit()
            thread.wait(3000)
