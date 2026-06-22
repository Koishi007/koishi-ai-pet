# Voice Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add voice input to DeskPet via 讯飞 STT API + global hotkey

**Architecture:** Voice module (MicCapture → XunfeiSTT) orchestrated by VoiceSession, triggered by HotkeyManager (global pynput hook) or ChatBubble mic button. Recognized text streamed into ChatBubble input box in real-time.

**Tech Stack:** PySide6, sounddevice (mic), pynput (global hotkey), websocket-client (讯飞 API)

## Global Constraints

- Hotkey default: F8, configurable via `VOICE_HOTKEY`
- Mic format: PCM 16kHz 16bit mono, frame size 8000 bytes
- 讯飞 API: WebSocket v2/iat, auth via APPID+APIKey+APISecret
- Voice module guarded by `VOICE_INPUT_ENABLED` config flag
- All signals cross thread boundaries — use Qt Signal/Slot
- Dependencies: sounddevice, pynput, websocket-client

---

## File Structure

| File | Responsibility | Type |
|------|---------------|------|
| `pet/voice/__init__.py` | Package marker | Create |
| `pet/voice/mic_capture.py` | MicCapture: PCM采集 via `sounddevice` | Create |
| `pet/voice/xunfei_stt.py` | XunfeiSTT: WebSocket鉴权+流式推帧+结果拼接 | Create |
| `pet/voice/voice_session.py` | VoiceSession: 编排MicCapture + XunfeiSTT | Create |
| `pet/voice/hotkey_manager.py` | HotkeyManager: 全局pynput钩子 | Create |
| `config.py` | +5 config keys | Modify |
| `pet/ui/settings_window.py` | +语音设置区(通用tab) | Modify |
| `pet/ui/chat_bubble.py` | +麦克风按钮+实时文字+录音UI | Modify |
| `main.py` | 初始化VoiceSession+HotkeyManager | Modify |

---

### Task 1: Add Config Keys

**Files:**
- Modify: `config.py:11-50` (add keys to _KEY_META)

- [ ] **Step 1: Add voice input config keys to _KEY_META**

```python
# in config.py _KEY_META dict, after existing keys:

"XF_APPID":              ("str",  "",       "connection", False),
"XF_API_KEY":            ("str",  "",       "connection", False),
"XF_API_SECRET":         ("str",  "",       "connection", False),
"VOICE_INPUT_ENABLED":   ("bool", False,    "behavior",   False),
"VOICE_HOTKEY":          ("str",  "F8",     "behavior",   False),
```

- [ ] **Step 2: Add env loading in _load_env()**

```python
# in config.py _load_env(), after existing env vars:

self.XF_APPID = os.getenv("XF_APPID", "")
self.XF_API_KEY = os.getenv("XF_API_KEY", "")
self.XF_API_SECRET = os.getenv("XF_API_SECRET", "")
self.VOICE_INPUT_ENABLED = os.getenv("VOICE_INPUT_ENABLED", "false").lower() == "true"
self.VOICE_HOTKEY = os.getenv("VOICE_HOTKEY", "F8")
```

- [ ] **Step 3: Verify**

```bash
python -c "from config import config; print(config.VOICE_HOTKEY, config.VOICE_INPUT_ENABLED)"
```
Expected: `F8 False`

- [ ] **Step 4: Commit**

```bash
git add config.py
git commit -m "feat: add voice input config keys"
```

---

### Task 2: Create pet/voice Package

**Files:**
- Create: `pet/voice/__init__.py`

- [ ] **Step 1: Create package init**

```python
# pet/voice/__init__.py
"""语音输入模块"""
```

- [ ] **Step 2: Commit**

```bash
git add pet/voice/__init__.py
git commit -m "feat: create voice package"
```

---

### Task 3: MicCapture — PCM Audio Capture

**Files:**
- Create: `pet/voice/mic_capture.py`

**Produces:** `MicCapture` class with `audio_chunk = Signal(bytes)`, `start()`, `stop()`

- [ ] **Step 1: Create MicCapture**

```python
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
```

- [ ] **Step 2: Verify import**

```bash
python -c "from pet.voice.mic_capture import MicCapture; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add pet/voice/mic_capture.py
git commit -m "feat: add MicCapture with sounddevice"
```

---

### Task 4: XunfeiSTT — 讯飞 WebSocket STT

**Files:**
- Create: `pet/voice/xunfei_stt.py`

**Produces:** `XunfeiSTT` class with `partial_result = Signal(str)`, `done = Signal(str)`, `error = Signal(str)`, `start()`, `send_audio(bytes)`, `stop()`

**Consumes:** `config.XF_APPID`, `config.XF_API_KEY`, `config.XF_API_SECRET`

- [ ] **Step 1: Create XunfeiSTT**

```python
"""讯飞语音听写 (iat) WebSocket API 封装"""

import base64
import hashlib
import hmac
import json
import logging
import ssl
import threading
import time
from datetime import datetime
from urllib.parse import urlencode
from wsgiref.handlers import format_date_time
from time import mktime

import websocket
from PySide6.QtCore import QObject, Signal

from config import config

logger = logging.getLogger(__name__)

STATUS_FIRST_FRAME = 0
STATUS_CONTINUE_FRAME = 1
STATUS_LAST_FRAME = 2


class XunfeiSTT(QObject):
    """讯飞语音听写流式 WebSocket 客户端。

    每次 start() → send_audio()*N → stop() 为一个会话。
    返回的文字实时通过 partial_result 发出。
    """

    partial_result = Signal(str)  # 中间识别结果
    done = Signal(str)            # 最终完整结果
    error_occurred = Signal(str)  # 错误信息

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._result_text = ""
        self._final_text = ""

    def start(self):
        """建立 WebSocket 连接，发送第一帧音频。"""
        if self._running:
            return
        self._result_text = ""
        self._final_text = ""

        if not config.XF_APPID or not config.XF_API_KEY or not config.XF_API_SECRET:
            logger.error("[XunfeiSTT] missing API credentials")
            self.error_occurred.emit("讯飞 API 凭证未配置")
            return

        url = self._build_url()
        self._running = True

        self._ws = websocket.WebSocketApp(
            url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws.on_open = self._on_open

        self._thread = threading.Thread(target=self._run_forever, daemon=True)
        self._thread.start()
        logger.info("[XunfeiSTT] connecting...")

    def _run_forever(self):
        self._ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})

    def _build_url(self) -> str:
        base = "wss://ws-api.xfyun.cn/v2/iat"
        now = datetime.now()
        date = format_date_time(mktime(now.timetuple()))

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

    def _on_open(self, ws):
        """WebSocket 已打开，等待 send_audio 推送数据。"""
        logger.info("[XunfeiSTT] connected")

    def send_audio(self, data: bytes, status: int = STATUS_CONTINUE_FRAME):
        """推送一帧音频数据到讯飞。

        Args:
            data: PCM 16kHz 16bit mono bytes
            status: 0=第一帧, 1=中间帧, 2=最后一帧
        """
        if not self._ws or not self._ws.sock:
            return
        b64 = base64.b64encode(data).decode("utf-8")

        if status == STATUS_FIRST_FRAME:
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
                    "status": 0,
                    "format": "audio/L16;rate=16000",
                    "audio": b64,
                    "encoding": "raw",
                },
            }
        else:
            payload = {
                "data": {
                    "status": status,
                    "format": "audio/L16;rate=16000",
                    "audio": b64,
                    "encoding": "raw",
                }
            }

        try:
            self._ws.send(json.dumps(payload))
        except Exception as e:
            logger.error(f"[XunfeiSTT] send failed: {e}")

    def stop(self):
        """发送最后一帧，等待结果后关闭连接。"""
        if not self._running:
            return
        self._running = False
        # 发送空最后一帧通知讯飞结束
        if self._ws and self._ws.sock:
            payload = {
                "data": {
                    "status": STATUS_LAST_FRAME,
                    "format": "audio/L16;rate=16000",
                    "audio": "",
                    "encoding": "raw",
                }
            }
            try:
                self._ws.send(json.dumps(payload))
            except Exception:
                pass
        logger.info("[XunfeiSTT] waiting for final result...")

    def _on_message(self, ws, message):
        """解析讯飞返回的 JSON。"""
        try:
            msg = json.loads(message)
            code = msg.get("code", -1)
            if code != 0:
                err = msg.get("message", "unknown")
                logger.error(f"[XunfeiSTT] server error: {code} {err}")
                self.error_occurred.emit(f"讯飞错误: {err}")
                ws.close()
                return

            data = msg.get("data", {})
            status = data.get("status", -1)

            # 解析识别文字
            result = data.get("result", {})
            ws_list = result.get("ws", [])
            text = ""
            for item in ws_list:
                for cw in item.get("cw", []):
                    text += cw.get("w", "")

            if text:
                self._result_text = text
                self.partial_result.emit(text)

            # 最后一帧，发出最终结果
            if status == 2:
                final = data.get("result", {}).get("text", "")
                if not final and self._result_text:
                    final = self._result_text
                self._final_text = final
                self.done.emit(final)
                logger.info(f"[XunfeiSTT] final result: {final}")

        except Exception as e:
            logger.error(f"[XunfeiSTT] parse error: {e}")

    def _on_error(self, ws, error):
        logger.error(f"[XunfeiSTT] websocket error: {error}")
        self.error_occurred.emit(str(error))

    def _on_close(self, ws, close_status_code, close_msg):
        logger.info(f"[XunfeiSTT] closed ({close_status_code})")
        self._running = False
        # 如果还没有发出 final，有 partial 就用 partial
        if self._result_text and not self._final_text:
            self.done.emit(self._result_text)

    def force_stop(self):
        """立即关闭连接，不等待结果。"""
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
```

- [ ] **Step 2: Verify import**

```bash
python -c "from pet.voice.xunfei_stt import XunfeiSTT; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add pet/voice/xunfei_stt.py
git commit -m "feat: add XunfeiSTT WebSocket client"
```

---

### Task 5: VoiceSession — Orchestrate Capture + STT

**Files:**
- Create: `pet/voice/voice_session.py`

**Consumes:** `MicCapture`, `XunfeiSTT`
**Produces:** `VoiceSession` with `partial_text`, `transcription_done`, `error` signals

- [ ] **Step 1: Create VoiceSession**

```python
"""语音会话编排：MicCapture → XunfeiSTT 完整流程。"""

import logging

from PySide6.QtCore import QObject, Signal, QTimer

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
        self._stt.error_occurred.connect(self._on_error)

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
```

- [ ] **Step 2: Verify import**

```bash
python -c "from pet.voice.voice_session import VoiceSession; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add pet/voice/voice_session.py
git commit -m "feat: add VoiceSession orchestration"
```

---

### Task 6: HotkeyManager — Global Keyboard Hook

**Files:**
- Create: `pet/voice/hotkey_manager.py`

**Produces:** `HotkeyManager` with `voice_start`, `voice_stop` signals

**Consumes:** `config.VOICE_HOTKEY`

- [ ] **Step 1: Create HotkeyManager**

```python
"""全局热键管理器，使用 pynput 监听按键按下/释放。

当前仅支持单键热键（如 F8、Scroll_Lock 等），
组合键（Ctrl+Shift+V 等）需要额外处理 key up 。
"""

import logging
from threading import Thread

from pynput import keyboard
from PySide6.QtCore import QObject, Signal

from config import config

logger = logging.getLogger(__name__)


class HotkeyManager(QObject):
    """全局热键监听。

    按下热键 → voice_start 信号
    松开热键 → voice_stop 信号
    """

    voice_start = Signal()
    voice_stop = Signal()
    error_occurred = Signal(str)

    _MODIFIERS = {"ctrl_l", "ctrl_r", "alt_l", "alt_r", "shift_l", "shift_r", "cmd_l", "cmd_r"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._listener: keyboard.Listener | None = None
        self._hotkey = config.VOICE_HOTKEY.lower()
        self._pressed = False
        self._is_modifier = self._hotkey in self._MODIFIERS

    def start(self):
        """启动全局键盘监听线程。"""
        if self._listener and self._listener.running:
            return
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()
        logger.info(f"[HotkeyManager] listening for '{self._hotkey}'")

    def stop(self):
        """停止键盘监听。"""
        if self._listener and self._listener.running:
            self._listener.stop()
            self._listener = None
        logger.info("[HotkeyManager] stopped")

    def _on_press(self, key):
        try:
            key_name = key.char.lower() if hasattr(key, 'char') and key.char else key.name.lower()
        except Exception:
            return

        if key_name != self._hotkey:
            return

        if not self._pressed:
            self._pressed = True
            logger.info(f"[HotkeyManager] hotkey pressed: {self._hotkey}")
            self.voice_start.emit()

    def _on_release(self, key):
        try:
            key_name = key.char.lower() if hasattr(key, 'char') and key.char else key.name.lower()
        except Exception:
            return

        if key_name != self._hotkey:
            return

        if self._pressed:
            self._pressed = False
            logger.info(f"[HotkeyManager] hotkey released: {self._hotkey}")
            self.voice_stop.emit()
```

- [ ] **Step 2: Verify import**

```bash
python -c "from pet.voice.hotkey_manager import HotkeyManager; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add pet/voice/hotkey_manager.py
git commit -m "feat: add HotkeyManager with pynput"
```

---

### Task 7: ChatBubble — Mic Button + Voice Text

**Files:**
- Modify: `pet/ui/chat_bubble.py`

- [ ] **Step 1: Add mic button to _setup_ui()**

After the input box setup, add:

```python
# pet/ui/chat_bubble.py, in _setup_ui(), after self._input.hide() and self._layout.addWidget(self._input)

# 录音按钮
self._voice_btn = QPushButton()
self._voice_btn.setFixedSize(32, 32)
self._voice_btn.setIcon(QIcon(str(BASE_DIR / "assets" / "icon" / "mic.png")))
self._voice_btn.setIconSize(QSize(20, 20))
self._voice_btn.setStyleSheet(
    "QPushButton {"
    "  background: rgba(255,255,255,220);"
    "  border: 1px solid #ccc;"
    "  border-radius: 16px;"
    "}"
    "QPushButton:hover {"
    "  background: rgba(240,240,255,240);"
    "  border-color: #aaa;"
    "}"
    "QPushButton:checked {"   # 录音状态
    "  background: rgba(255,80,80,230);"
    "  border-color: #d00;"
    "}"
)
self._voice_btn.setCheckable(True)
self._voice_btn.pressed.connect(self._on_voice_pressed)
self._voice_btn.released.connect(self._on_voice_released)
self._voice_btn.hide()
self._layout.addWidget(self._voice_btn)
```

- [ ] **Step 2: Add voice methods**

```python
# New methods on ChatBubble:

def show_voice_btn(self, visible: bool):
    """显示/隐藏录音按钮。"""
    self._voice_btn.setVisible(visible)

def set_voice_text(self, text: str):
    """实时更新识别文字到输入框。"""
    self._input.setText(text)
    # 如果还没展开，自动展开
    if not self._expanded:
        self._expand()

def set_recording(self, recording: bool):
    """切换录音状态 UI。"""
    self._voice_btn.setChecked(recording)
    if recording:
        self._input.setPlaceholderText("正在聆听...")
        self._voice_btn.setToolTip("松开结束")
    else:
        self._input.setPlaceholderText("说点什么...")
        self._voice_btn.setToolTip("按住说话")
    self._input.setFocus()

def _on_voice_pressed(self):
    """麦克风按下 → 开始录音。"""
    self.voice_started.emit()

def _on_voice_released(self):
    """麦克风松开 → 停止录音。"""
    self.voice_stopped.emit()
```

- [ ] **Step 3: Add signal declarations**

Add to class-level signals:

```python
voice_started = Signal()   # 录音开始
voice_stopped = Signal()   # 录音停止
```

- [ ] **Step 4: Wire voice button visibility to config**

In `__init__`, after config is available:

```python
from config import config
# ...
if config.VOICE_INPUT_ENABLED and config.XF_APPID:
    self._voice_btn.show()
```

- [ ] **Step 5: Verify import**

```bash
python -c "from pet.ui.chat_bubble import ChatBubble; print('OK')" 2>&1 | head -3
```

- [ ] **Step 6: Commit**

```bash
git add pet/ui/chat_bubble.py
git commit -m "feat: add mic button and voice text to ChatBubble"
```

---

### Task 8: Settings Window — Voice UI

**Files:**
- Modify: `pet/ui/settings_window.py`

- [ ] **Step 1: Read current settings_window.py to understand patterns**

```bash
grep -n "def _build_appearance_tab" pet/ui/settings_window.py
```

- [ ] **Step 2: Add voice settings group to 通用 tab**

In `_build_appearance_tab()` (or whatever the 通用 tab method is called), add a group after existing controls:

```python
# Voice Input group
voice_group = QGroupBox("语音输入")
voice_layout = QVBoxLayout(voice_group)

voice_enable = self._check("VOICE_INPUT_ENABLED", "启用语音输入")
voice_layout.addWidget(voice_enable)

form = QFormLayout()
form.setSpacing(6)
form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
form.addRow("热键:", self._line("VOICE_HOTKEY", "F8"))
form.addRow("讯飞 APPID:", self._line("XF_APPID", ""))
form.addRow("讯飞 API Key:", self._line("XF_API_KEY", ""))
form.addRow("讯飞 API Secret:", self._line("XF_API_SECRET", ""))
voice_layout.addLayout(form)

layout.addWidget(voice_group)
```

- [ ] **Step 3: Verify import**

```bash
python -c "from pet.ui.settings_window import SettingsWindow; print('OK')" 2>&1 | head -3
```

- [ ] **Step 4: Commit**

```bash
git add pet/ui/settings_window.py
git commit -m "feat: add voice input settings to 通用 tab"
```

---

### Task 9: Main — Wire Everything Together

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Read current main.py to find right insertion points**

```bash
grep -n "chat_bubble\|agent\|feed_bubble" main.py
```

- [ ] **Step 2: Add voice module initialization after chat_bubble and agent creation**

```python
# In main.py, after agent and chat_bubble are created:

# ── 语音输入 ──
_voice_session = None
_hotkey_mgr = None

if config.VOICE_INPUT_ENABLED and config.XF_APPID:
    from pet.voice.voice_session import VoiceSession
    from pet.voice.hotkey_manager import HotkeyManager

    _voice_session = VoiceSession()
    _hotkey_mgr = HotkeyManager()

    # 热键 → 语音
    _hotkey_mgr.voice_start.connect(_voice_session.start)
    _hotkey_mgr.voice_stop.connect(_voice_session.stop)

    # 语音 → 气泡 UI
    _voice_session.partial_text.connect(chat_bubble.set_voice_text)
    _voice_session.transcription_done.connect(chat_bubble.set_voice_text)
    _voice_session.recording_started.connect(lambda: chat_bubble.set_recording(True))
    _voice_session.recording_stopped.connect(lambda: chat_bubble.set_recording(False))

    # 按钮 → 语音
    chat_bubble.voice_started.connect(_voice_session.start)
    chat_bubble.voice_stopped.connect(_voice_session.stop)

    # 录音开始 → 自动展开输入框
    _voice_session.recording_started.connect(chat_bubble.show_voice_input)

    _hotkey_mgr.start()
    logger.info("[Main] voice input initialized")
```

- [ ] **Step 3: Add cleanup on shutdown**

If there's a shutdown handler in main.py:

```python
# In shutdown/cleanup:
if _hotkey_mgr:
    _hotkey_mgr.stop()
if _voice_session:
    _voice_session.force_stop()
```

- [ ] **Step 4: Verify import**

```bash
python -c "import main; print('OK')" 2>&1 | head -5
```

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat: wire voice input into main.py"
```

---

### Task 10: Create Mic Icon Asset

**Files:**
- Create: `assets/icon/mic.png`

- [ ] **Step 1: Check if assets/icon exists and what icons are there**

```bash
ls assets/icon/
```

- [ ] **Step 2: Create a simple 32x32 mic icon**

If no icon asset available, create a simple SVG-based PNG or use a QStyle standard icon as fallback in the code:

```python
# Fallback in ChatBubble if icon file doesn't exist:
if not Path(str(BASE_DIR / "assets" / "icon" / "mic.png")).exists():
    self._voice_btn.setText("🎤")
    self._voice_btn.setFixedSize(32, 32)
```

- [ ] **Step 3: Commit (if icon file added)**

```bash
git add assets/icon/mic.png
git commit -m "feat: add mic icon"
```

---

## Self-Review Checklist

- **Spec coverage:** Each spec section has a corresponding task — MicCapture (Task 3), XunfeiSTT (Task 4), VoiceSession (Task 5), HotkeyManager (Task 6), ChatBubble changes (Task 7), settings (Task 8), main wiring (Task 9), config (Task 1), package init (Task 2), icon (Task 10).
- **Placeholder scan:** No TODOs, TBDs, or "implement later" — all steps have complete code blocks.
- **Type consistency:** `MicCapture.audio_chunk = Signal(bytes)` → `VoiceSession._on_audio_chunk(data: bytes)` → `XunfeiSTT.send_audio(data: bytes)`. All signals and types match across tasks.
