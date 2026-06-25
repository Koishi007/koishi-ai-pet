# DeskPet 🐾

> 基于 PySide6 + LLM 的 Windows 桌面 AI 虚拟宠物，能感知屏幕、与窗口互动、与你对话。

## ✨ 特性

- **LLM 自主决策**：自主决定行走、跳跃、坐下、睡觉等动作，通过多轮对话保持上下文连贯
- **视觉感知**：多模态截图 + Win32 窗口探测，理解屏幕上正在发生什么
- **物理交互**：模拟重力下落，可站立在其他窗口顶部；拖拽、点击、投喂均有响应
- **流式对话**：回复以打字机效果实时呈现，支持语音输入
- **持久记忆**：SQLite 向量记忆库，三级分级（L1/L2/L3），记住用户的偏好与重要事件
- **心理状态**：好感度、愉悦度、理智值动态变化，影响宠物语气和行为
- **工具系统**：OpenAI function calling，内置浏览器、天气、待办、文件操作、系统监控等工具
- **多 LLM 后端**：支持 OpenAI 兼容 API、Ollama 本地模型、纯 local fallback

## 📦 项目结构

```
DeskPet/
├── main.py                    # 入口
├── config.py                  # 全局配置
├── requirements.txt
├── assets/actions/            # 帧动画素材（idle、walk、sit、sleep…）
└── pet/
    ├── action/                # 动作系统：注册、ActionQueue、重力模拟
    ├── agent/                 # 调度层：PetAgent、Scheduler、StateMachine
    ├── brain/                 # LLM 集成：Behavior、prompts、memory、window_detector
    ├── pulse/                 # 心理数值引擎：Mood（好感/愉悦/理智）、Vitals（饱食/精力）
    ├── tools/                 # 工具系统：Registry、Executor、内置工具
    ├── ui/                    # Qt 界面：宠物窗口、气泡、聊天框、托盘、设置
    └── voice/                 # 语音输入：麦克风采集、讯飞 STT
```

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

所有配置通过 `settings.json` 管理，存储在 `%APPDATA%/DeskPet/settings.json`。
首次启动自动生成默认配置，也可通过托盘右键 →「设置」图形化编辑。

主要配置项：

```json
{
  "BRAIN": "api",
  "LLM_MODEL": "gpt-4o-mini",
  "LLM_KEY": "sk-xxx",
  "LLM_URL": "https://api.openai.com/v1",
  "VISION_ENABLED": true,
  "TOOLS_ENABLED": ["*"],
  "PET_PERSONALITY": "你是一只活泼好动的Q版小猫，充满好奇心，喜欢在窗口之间跳来跳去。"
}
```

| 配置项 | 说明 | 默认 |
|--------|------|------|
| `BRAIN` | LLM 后端：`local` / `api` / `ollama` | `local` |
| `LLM_MODEL` | 模型名称 | `gpt-4o-mini` |
| `LLM_KEY` | API Key | （空） |
| `LLM_URL` | API 地址 | `https://api.openai.com/v1` |
| `VISION_ENABLED` | 启用视觉截图分析 | `false` |
| `SCHEDULER_MID_MS` | 自主决策间隔（毫秒） | `30000` |
| `TOOLS_ENABLED` | 启用的工具插件（`["*"]` = 全部） | `["*"]` |
| `PET_PERSONALITY` | 宠物人格描述 | （空） |

### 3. 启动

```bash
python main.py
```

## 🧩 内置工具

| 工具 | 功能 |
|------|------|
| `browser` | 打开 URL、截图网页 |
| `web_search` | 关键词搜索、深度搜索 |
| `weather` | 查询当前天气和预报 |
| `todo` | 待办事项管理（pending / done） |
| `file_ops` | 列目录、读写文件（限桌面/文档目录） |
| `system_monitor` | CPU、内存、磁盘、电池、Top 进程 |

## 🔨 工具开发指南

### 工具骨架

在 `pet/tools/` 下创建新目录，包含 `__init__.py` 和 `core.py`：

```
pet/tools/my_tool/
├── __init__.py    # 注册入口
└── core.py        # 业务实现
```

`__init__.py` 模板：

```python
from pet.tools.my_tool.core import do_something

TOOL_NAME = "my_tool"
TOOL_DESCRIPTION = "一句话描述工具用途"


def register(registry):
    registry.register(TOOL_NAME, TOOL_DESCRIPTION)

    registry.add_method(
        TOOL_NAME, "do",
        "执行某操作",
        handler=do_something,
        args={
            "target": {"type": "str", "required": True, "desc": "目标名称"},
            "mode": {"type": "str", "required": False, "default": "fast",
                     "desc": "执行模式", "enum": ["fast", "slow"]},
        },
        timeout=15.0,  # 可选：超时秒数，默认 30s
    )
```

### 参数定义

`args` 字典中每个参数支持以下字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | `str` | 是 | 参数类型：`str` / `int` / `float` / `bool` |
| `required` | `bool` | 否 | 是否必填，默认 `False` |
| `default` | 同 type | 否 | 默认值 |
| `desc` | `str` | 否 | 参数描述（写入 LLM function schema） |
| `enum` | `list` | 否 | 枚举可选值 |

参数会自动转换为 OpenAI function calling 格式，LLM 通过 `tool_calls` 调用。

### 返回值

```python
def do_something(target: str, mode: str = "fast") -> dict:
    return {
        "summary": "操作成功的简短描述",   # LLM 优先读取
        "data": {"result": "..."},        # 结构化数据
    }
```

| 返回类型 | LLM 看到的内容 |
|----------|----------------|
| `dict` 含 `summary` | summary 文本 + JSON |
| `dict` 不含 `summary` | JSON 字符串 |
| `str` | 原始字符串 |

返回值会经由 `ToolExecutor._normalize` 统一为文本，插入下一轮 LLM 调用。

### 图片注入（多模态）

```python
import base64

def capture() -> dict:
    return {
        "summary": "截图完成",
        "__image__": base64.b64encode(img_bytes).decode(),  # 约定键名
    }
```

系统自动将 `__image__` 提取为多模态消息，需要模型支持视觉。

### 主动调用宠物能力

```python
from pet.tools.context import TOOL_CTX

def alert() -> dict:
    TOOL_CTX.speech("注意！", duration=3000)
    TOOL_CTX.action("bounce", kwargs={"dx": 0, "dy": -200})
    return {"summary": "已提醒"}
```

`TOOL_CTX` 可用方法：`speech`、`action`、`add_context`、`notify`、`request_interact`、`register_tick`、`register_alarm`。

### 启用工具

在 `settings.json` 中配置：

```json
"TOOLS_ENABLED": ["my_tool", "weather"]
```

`["*"]` 启用全部，`[]` 全部禁用。未启用的工具不会出现在 LLM 可见的工具列表中。

### 注意事项

- **参数名严格匹配**：`add_method` 的 `args` 键名必须与 handler 参数名一致
- **超时保护**：handler 在线程池中执行，超时后自动终止并返回错误
- **异常隔离**：handler 异常会被捕获，译为错误信息传给 LLM，不影响主流程
- **序列化友好**：返回的 dict 必须能被 `json.dumps(ensure_ascii=False)` 序列化

## 📝 日志

应用日志按天切分，保留 3 天，输出至 `logs/deskpet.log`。日志级别由设置中的 `LOG_LEVEL` 控制。

## 📜 许可

GPL-3.0
