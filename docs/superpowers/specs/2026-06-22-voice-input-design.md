# Voice Input for DeskPet

## 概述

为 DeskPet 增加语音输入功能，用户可通过全局快捷键（默认 F8）或点击聊天气泡的麦克风按钮进行语音输入，识别文字实时显示在输入框中，按 Enter 后发送。

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│                    HotkeyManager                            │
│  keyboard.hook() 监听全局按键                               │
│  按下 → VoiceSession.start()                                │
│  松开 → VoiceSession.stop()                                 │
└──────────┬──────────────────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────────────┐
│                     VoiceSession                            │
│  ├── MicCapture    (pyaudio → 16kHz 16bit PCM)             │
│  └── XunfeiSTT     (WebSocket → 流式推帧 → 拼接结果)      │
│                                                             │
│  每收到讯飞返回的文字 → signal: partial_text(str)           │
│  停止后 → signal: transcription_done(str)                    │
└──────────┬──────────────────────────────────────────────────┘
           │ partial_text / transcription_done
           ▼
┌─────────────────────────────────────────────────────────────┐
│                    ChatBubble                                │
│  set_voice_text(text) → _input.setText(text)                │
│  松开后保留文字在输入框，用户可编辑                         │
│  Enter → chat_submitted(text) → 走原有 chat 流程            │
└─────────────────────────────────────────────────────────────┘
```

## 文件清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `pet/voice/__init__.py` | 空 |
| `pet/voice/mic_capture.py` | 麦克风 PCM 采集 |
| `pet/voice/xunfei_stt.py` | 讯飞 WebSocket 流式 STT |
| `pet/voice/hotkey_manager.py` | 全局键盘钩子管理 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `pet/ui/chat_bubble.py` | +麦克风按钮、+set_voice_text()、+voice_mode 状态 |
| `config.py` | +5 个配置键 |
| `pet/ui/settings_window.py` | 行为 tab 加讯飞配置区 |
| `main.py` | 初始化 HotkeyManager |

### 依赖

- `sounddevice` — 麦克风 PCM 采集（`pip install sounddevice`）
- `pynput` — 全局键盘钩子（`pip install pynput`）
- `websocket-client` — 已安装

## 组件设计

### MicCapture (`pet/voice/mic_capture.py`)

```
class MicCapture(QObject):
    audio_chunk = Signal(bytes)  # 每帧 PCM 数据

    def start(self):       # 打开麦克风，启动采集线程
    def stop(self):        # 停止采集，关闭麦克风
```

- 格式：PCM 16kHz, 16bit, mono
- 帧大小：8000 字节/帧（对应讯飞标准帧）
- 使用 `sounddevice` 库采集（API 更现代，无需 VC++ 编译），退而求其次为 `PyAudio`
- 采集线程通过 `sounddevice.InputStream` 读取
- 每帧通过 `audio_chunk` 信号发出

### XunfeiSTT (`pet/voice/xunfei_stt.py`)

```
class XunfeiSTT(QObject):
    partial_result = Signal(str)   # 中间识别结果
    transcription_done = Signal(str)  # 最终完整结果

    def start(self):       # 建立 WebSocket 连接，发送第一帧
    def send_audio(self, data: bytes):  # 推送中间帧
    def stop(self):        # 发送最后一帧，等待最终结果，关闭连接
```

实现步骤：

1. 构造函数读取 `config.XF_APPID`、`XF_API_KEY`、`XF_API_SECRET`
2. `start()` 按讯飞 API 生成鉴权 URL、建立 WebSocket 连接
3. `send_audio()` 发 base64 编码的 PCM 帧
4. 收到讯飞 JSON 消息 → 解析 `data.result.ws[]` 拼接文字 → 发出 `partial_result`
5. `stop()` 发 STATUS_LAST_FRAME 帧，等待最终结果后关闭 ws
6. 连接异常 → 发出 `error` 信号

### VoiceSession (`pet/voice/voice_session.py`)

协调整 MicCapture 和 XunfeiSTT 的生命周期。

```
class VoiceSession(QObject):
    partial_text = Signal(str)
    transcription_done = Signal(str)
    error = Signal(str)

    def start(self):
    def stop(self):
```

编排 MicCapture 和 XunfeiSTT 的调用顺序：

1. `start()` → XunfeiSTT.start() → MicCapture.start()
2. MicCapture 每帧 → XunfeiSTT.send_audio()
3. XunfeiSTT partial_result → partial_text
4. `stop()` → MicCapture.stop() → XunfeiSTT.stop() → transcription_done

### HotkeyManager (`pet/voice/hotkey_manager.py`)

```
class HotkeyManager(QObject):
    voice_start = Signal()
    voice_stop = Signal()

    def start(self):     # 注册全局钩子
    def stop(self):      # 注销全局钩子
```

- 使用 `pynput` 库监听全局按键（无需管理员权限，支持 press/release 区分）
- 检测到热键（默认 F8）按下 → `voice_start`
- 检测到热键释放 → `voice_stop`
- 热键值从 `config.VOICE_HOTKEY` 读取
- `main.py` 初始化后调用 `start()`

### ChatBubble 改动

**新增 UI 元素：**

- 麦克风按钮 `_voice_btn`：32x32，在输入框右侧
- 录音中样式：按钮背景变红色、图标换为录音图标
- 放置于输入框右侧（RTL 布局时在左侧）

**新增方法：**

```
def show_voice_input(self):
    """自动展开输入框"""
    self.show_bubble()
    self._expand()

def set_voice_text(self, text: str):
    """实时更新识别文字"""
    self._input.setText(text)

def set_recording(self, recording: bool):
    """切换录音状态 UI"""
    if recording:
        self._input.setPlaceholderText("正在聆听...")
        self._btn.setIcon(QIcon(...))  # 换为红色录音图标
    else:
        self._input.setPlaceholderText("说点什么...")
        self._btn.setIcon(QIcon(...))  # 恢复聊天图标
```

### 配置项 (`config.py`)

```python
"XF_APPID":              ("str",  "",       "connection", False),
"XF_API_KEY":            ("str",  "",       "connection", False),
"XF_API_SECRET":         ("str",  "",       "connection", False),
"VOICE_INPUT_ENABLED":   ("bool", False,    "behavior",   False),
"VOICE_HOTKEY":          ("str",  "F8",     "behavior",   False),
```

### main.py 变更

在创建 ChatBubble 和 PetAgent 之后：

```python
if config.VOICE_INPUT_ENABLED and config.XF_APPID:
    from pet.voice.hotkey_manager import HotkeyManager
    from pet.voice.xunfei_stt import VoiceSession

    voice_session = VoiceSession()
    hotkey_mgr = HotkeyManager()

    # 热键 → 语音
    hotkey_mgr.voice_start.connect(voice_session.start)
    hotkey_mgr.voice_stop.connect(voice_session.stop)

    # 语音 → 气泡
    voice_session.transcription_done.connect(lambda t: chat_bubble.set_voice_text(t))
    voice_session.partial_text.connect(lambda t: chat_bubble.set_voice_text(t))

    # 热键 → 气泡 UI
    hotkey_mgr.voice_start.connect(chat_bubble.show_voice_input)
    hotkey_mgr.voice_start.connect(lambda: chat_bubble.set_recording(True))
    hotkey_mgr.voice_stop.connect(lambda: chat_bubble.set_recording(False))

    hotkey_mgr.start()
```

### 设置 UI

在「行为」tab 底部新增一栏「语音输入」：

```
[X] 启用语音输入
  热键: [F8        ]
  APPID: [          ]
  API Key: [        ]
  API Secret: [     ]
```

## 错误处理

- 麦克风设备不可用 → 发出 `error` 信号，`set_recording(False)` 恢复 UI
- 讯飞 WebSocket 连接失败 → 报错，恢复 UI，输入框显示"语音识别连接失败"
- 热键冲突 → 不处理，交给 `pynput` 库，建议用户更换热键
- 录音中再次按热键 → 忽略（幂等判断）


