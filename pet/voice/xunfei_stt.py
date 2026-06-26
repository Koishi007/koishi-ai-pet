"""讯飞语音听写 (iat) WebSocket API 封装"""

import base64
import calendar
import hashlib
import hmac
import json
import logging
import threading
from datetime import datetime
from urllib.parse import urlencode
from wsgiref.handlers import format_date_time

import websocket
from PySide6.QtCore import QObject, Signal

from pet.config import config

logger = logging.getLogger(__name__)

STATUS_FIRST_FRAME = 0
STATUS_CONTINUE_FRAME = 1
STATUS_LAST_FRAME = 2

# WebSocket 连接超时（秒）
_WS_CONNECT_TIMEOUT = 5


class XunfeiSTT(QObject):
    """讯飞语音听写流式 WebSocket 客户端。

    connect_ws() → websocket 连接
    start_recording() / send_audio() / stop_recording() → 每轮识别
    close() → 关闭连接
    """

    partial_result = Signal(str)
    done = Signal(str)
    connected = Signal()
    disconnected = Signal()
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._connected = False
        self._recording = False
        self._result_text = ""
        self._done_emitted = False
        self._first_frame_sent = False
        self._send_lock = threading.Lock()

    # ── 连接管理 ──

    def connect_ws(self):
        """建立 WebSocket 连接。"""
        if self._connected:
            return
        if not self._check_credentials():
            return

        url = self._build_url()

        self._ws = websocket.WebSocketApp(
            url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws.on_open = self._on_open

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("[XunfeiSTT] connecting...")

    def _run(self):
        self._ws.run_forever()

    def _check_credentials(self) -> bool:
        if not config.XF_APPID or not config.XF_API_KEY or not config.XF_API_SECRET:
            self.error.emit("讯飞 API 凭证未配置")
            return False
        return True

    def close(self):
        """关闭连接。"""
        self._connected = False
        self._recording = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info("[XunfeiSTT] closed")

    def _on_open(self, ws):
        self._connected = True
        self.connected.emit()
        logger.info("[XunfeiSTT] connected")

        # 若识别已在等待连接，连接成功后补发第一帧（含 business 配置）
        if self._recording and not self._first_frame_sent:
            self._send_first_frame()

    def _send_first_frame(self):
        payload = {
            "common": {"app_id": config.XF_APPID},
            "business": {
                "domain": "iat",
                "language": "zh_cn",
                "accent": "mandarin",
                "vinfo": 1,
                "vad_eos": 10000,
            },
            "data": {
                "status": STATUS_FIRST_FRAME,
                "format": "audio/L16;rate=16000",
                "audio": "",
                "encoding": "raw",
            },
        }
        self._first_frame_sent = True
        self._send(json.dumps(payload))

    def _on_close(self, ws, close_status_code, close_msg):
        self._connected = False
        was_recording = self._recording
        self._recording = False
        self._first_frame_sent = False
        logger.info(f"[XunfeiSTT] closed ({close_status_code})")

        # 异常断开时不要发送残缺文本
        if was_recording and not self._done_emitted:
            if self._result_text:
                self.error.emit(f"识别未完成: {self._result_text}")
            else:
                self.error.emit("连接异常断开")

        self.disconnected.emit()

    def _on_error(self, ws, error):
        logger.error(f"[XunfeiSTT] error: {error}")

    # ── 识别会话 ──

    def start_recording(self):
        """开始一轮识别：连接（按需）+ 发送第一帧。"""
        if self._recording:
            return
        self._recording = True
        self._result_text = ""
        self._done_emitted = False
        self._first_frame_sent = False

        # 按需建立连接；第一帧在 _on_open 里发，避免握手未完成就 send
        if not self._connected:
            self.connect_ws()
        else:
            self._send_first_frame()

    def send_audio(self, data: bytes):
        """推送一帧 PCM 音频数据。"""
        if not self._recording or not self._connected:
            return
        b64 = base64.b64encode(data).decode("utf-8")
        payload = {
            "data": {
                "status": STATUS_CONTINUE_FRAME,
                "format": "audio/L16;rate=16000",
                "audio": b64,
                "encoding": "raw",
            }
        }
        self._send(json.dumps(payload))

    def stop_recording(self):
        """结束本轮识别：发送最后一帧，等待结果。"""
        if not self._recording:
            return
        self._recording = False
        payload = {
            "data": {
                "status": STATUS_LAST_FRAME,
                "format": "audio/L16;rate=16000",
                "audio": "",
                "encoding": "raw",
            }
        }
        self._send(json.dumps(payload))
        logger.info("[XunfeiSTT] waiting for final result...")

    def _send(self, text: str):
        with self._send_lock:
            if self._ws and self._ws.sock:
                try:
                    self._ws.send(text)
                except Exception as e:
                    logger.warning(f"[XunfeiSTT] send failed: {e}")
            else:
                logger.warning("[XunfeiSTT] cannot send, socket not ready")

    def _on_message(self, ws, message):
        try:
            msg = json.loads(message)
            code = msg.get("code", -1)
            if code != 0:
                err = msg.get("message", "unknown")
                logger.error(f"[XunfeiSTT] server error: {code} {err}")
                self.error.emit(f"讯飞错误: {err}")
                return

            data = msg.get("data", {})
            status = data.get("status", -1)
            result = data.get("result", {})

            for item in result.get("ws", []):
                for cw in item.get("cw", []):
                    self._result_text += cw.get("w", "")

            if self._result_text:
                self.partial_result.emit(self._result_text)

            if status == 2:
                self._done_emitted = True
                final = data.get("result", {}).get("text", "")
                text = final if final else self._result_text
                self.done.emit(text)
                logger.info(f"[XunfeiSTT] result: {text}")
                # 主动断开避免服务端空闲超时
                try:
                    ws.close()
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"[XunfeiSTT] parse error: {e}")

    # ── 辅助 ──

    def _build_url(self) -> str:
        base = "wss://ws-api.xfyun.cn/v2/iat"
        now = datetime.utcnow()
        date = format_date_time(calendar.timegm(now.utctimetuple()))

        sig_str = f"host: ws-api.xfyun.cn\ndate: {date}\nGET /v2/iat HTTP/1.1"
        sig = hmac.new(
            config.XF_API_SECRET.encode("utf-8"),
            sig_str.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sig_b64 = base64.b64encode(sig).decode("utf-8")

        auth = f'api_key="{config.XF_API_KEY}", algorithm="hmac-sha256", headers="host date request-line", signature="{sig_b64}"'
        auth_b64 = base64.b64encode(auth.encode("utf-8")).decode("utf-8")

        params = {"authorization": auth_b64, "date": date, "host": "ws-api.xfyun.cn"}
        return base + "?" + urlencode(params)

    def test_connection(self, app_id: str = "", api_key: str = "", api_secret: str = "") -> bool:
        """快速同步测试连接（使用短超时，不阻塞 UI 线程）。"""
        app_id = app_id or config.XF_APPID
        api_key = api_key or config.XF_API_KEY
        api_secret = api_secret or config.XF_API_SECRET

        if not app_id or not api_key or not api_secret:
            return False

        try:
            url = self._build_url_custom(app_id, api_key, api_secret)
            ws = websocket.create_connection(url, timeout=_WS_CONNECT_TIMEOUT)
            ws.close()
            return True
        except Exception as e:
            logger.warning(f"[XunfeiSTT] test_connection failed: {e}")
            return False

    @staticmethod
    def _build_url_custom(app_id: str, api_key: str, api_secret: str) -> str:
        """使用自定义凭证构建鉴权 URL（用于连接测试）。"""
        base = "wss://ws-api.xfyun.cn/v2/iat"
        now = datetime.utcnow()
        date = format_date_time(calendar.timegm(now.utctimetuple()))
        sig_str = f"host: ws-api.xfyun.cn\ndate: {date}\nGET /v2/iat HTTP/1.1"
        sig = hmac.new(
            api_secret.encode("utf-8"),
            sig_str.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sig_b64 = base64.b64encode(sig).decode("utf-8")
        auth = f'api_key="{api_key}", algorithm="hmac-sha256", headers="host date request-line", signature="{sig_b64}"'
        auth_b64 = base64.b64encode(auth.encode("utf-8")).decode("utf-8")
        params = {"authorization": auth_b64, "date": date, "host": "ws-api.xfyun.cn"}
        return base + "?" + urlencode(params)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_recording(self) -> bool:
        return self._recording
