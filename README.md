# DeskPet 🐾

> 基于 PySide6 + LLM 的 Windows 桌面 AI 虚拟宠物，能感知屏幕、与窗口互动、与你对话。

## ✨ 特性

- **智能行为决策**：通过 LLM 自主决定行走、跳跃、坐下、睡觉、张望等动作
- **视觉感知**：多模态截图询问 LLM，理解屏幕上正在发生什么
- **物理交互**：模拟重力下落，可站立在其他窗口顶部
- **流式对话**：悬停宠物显示聊天气泡，回复以打字机效果实时呈现
- **持久记忆**：SQLite 长期记忆库，记住主人的偏好与重要事件
- **插件化技能**：自动发现技能插件，目前内置系统监控、浏览器、文件操作
- **多 LLM 后端**：支持 OpenAI 兼容 API、Ollama 本地模型、纯 local fallback

## 📦 项目结构

```
DeskPet/
├── main.py                    # 入口
├── config.py                  # 全局配置
├── .env.example               # 环境变量模板
├── requirements.txt
├── assets/actions/            # 帧动画素材（idle、walk、sit、sleep…）
└── pet/
    ├── action/                # 动作系统：动作注册、ActionQueue、重力模拟
    ├── agent/                 # 调度层：PetAgent、Scheduler、StateMachine、记忆存储
    ├── brain/                 # LLM 集成：Behavior 决策、prompts、流式解析
    ├── skills/                # 技能插件：注册表、执行器、内置插件
    └── ui/                    # Qt 界面：宠物窗口、气泡、聊天框、托盘
```

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境

复制 `.env.example` 为 `.env`，按需修改：

```env
BRAIN=llm                # local / llm / ollama
LLM_MODEL=gpt-4o-mini    # 模型名
LLM_KEY=sk-xxx           # API Key
LLM_URL=https://api.openai.com/v1

VISION_ENABLED=true      # 启用视觉模式（需多模态模型）
SKILLS_ENABLED=*         # * = 启用所有插件

PET_PERSONALITY=你是一只活泼好动的Q版小猫，充满好奇心，喜欢在窗口之间跳来跳去。
```

### 3. 启动

```bash
python main.py
```

## ⚙️ 配置详解

| 配置项 | 说明 | 默认 |
|--------|------|------|
| `BRAIN` | LLM 后端：`local`/`llm`/`ollama` | `local` |
| `VISION_ENABLED` | 是否启用视觉截图分析 | `false` |
| `VISION_SCALE` | 截图缩放比例（下限锁 1536px） | `1` |
| `SCHEDULER_AUTO_START_FAST` | 是否自动启动 fast_tick（精力回复） | `true` |
| `SCHEDULER_AUTO_START_MID` | 是否自动启动 mid_tick（自主决策） | `true` |
| `SCHEDULER_AUTO_START_SLOW` | 是否自动启动 slow_tick（pulse 持久化） | `true` |
| `SCHEDULER_MID_MS` | 自主决策间隔（毫秒） | `30000` |
| `SKILLS_ENABLED` | 启用的技能插件 | （空=全禁用） |
| `PET_PERSONALITY` | 宠物人格描述（注入 prompt） | （空） |

## 🧩 内置技能

| 技能 | 功能 |
|------|------|
| `system_monitor` | CPU、内存、磁盘、电池、Top 进程查询 |
| `browser` | 打开 URL、关键词搜索 |
| `file` | 列目录、读文件、写笔记（限桌面/文档目录） |

编写自定义技能：在 `pet/skills/plugins/` 下新建 `.py` 文件，暴露 `SKILL_NAME`、`SKILL_DESCRIPTION` 和 `register(registry)` 函数即可被自动加载。

## 🔨 插件开发指南

### 插件骨架

在 `pet/skills/plugins/` 下创建一个新的 `.py` 文件，按以下骨架实现：

```python
"""插件说明：一句话描述该插件能做什么。"""

import logging

logger = logging.getLogger(__name__)

SKILL_NAME = "my_skill"                  # 插件唯一名（在 LLM prompt 中被调用）
SKILL_DESCRIPTION = "插件功能总描述"   # 插件扫描后写入 prompt


def _hello(name: str = "world") -> dict:
    """具体方法实现。参数名需与 register 中声明一致。"""
    msg = f"Hello, {name}!"
    return {
        "message": msg,
        "summary": msg,   # 可选：LLM 优先读 summary
    }


def register(registry):
    """插件接口 — 由 SkillLoader 调用。"""
    registry.register(SKILL_NAME, SKILL_DESCRIPTION)

    registry.add_method(
        SKILL_NAME, "hello",
        "向指定名字打招呼",
        handler=_hello,
        args={"name": "被问候者名字(str, 默认 world)"},
    )
```

### 调用格式

LLM 输出中以下行会被检测为技能调用：

```
Skill: {"name": "my_skill.hello", "args": {"name": "恫恫"}}
```

执行流程：检测 Skill 行 → 路由到 `_hello(name="恫恫")` → 结果作为上下文传入二次 LLM 调用。

### 返回值规范

| 类型 | 推荐场景 | LLM 看到的内容 |
|------|----------|----------------|
| `dict` 含 `summary` 键 | **推荐**：同时提供人类可读摘要和结构化数据 | `summary` 文本 + JSON |
| `dict` 不含 `summary` | 只需结构化数据 | JSON 字符串 |
| `str` | 纯文本返回 | 原始字符串 |
| `int` / `float` | 单一数值 | `str(value)` |

插件的返回值会经由 `SkillExecutor._normalize` 统一注释成文本后插入二次 LLM 调用，插件无需自行序列化。

### 图片注入（多模态）

插件可以在返回值里附带 base64 截图，系统会自动将其构建为多模态消息传给下一轮 LLM：

```python
import base64

def _take_screenshot() -> dict:
    # 截图并编码为 base64
    with open("screenshot.png", "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    return {
        "summary": "截图完成，请观察图片内容",
        "__image__": img_b64,   # 约定键名，系统自动提取
    }
```

**注意**：需要模型支持视觉（`VISION_ENABLED=true`），否则图片会被丢弃仅保留文字结果。

### 启用插件

在 `.env` 中配置：

```env
SKILLS_ENABLED=my_skill,system_monitor   # 逗号分隔多个
SKILLS_ENABLED=*                         # 启用全部
SKILLS_ENABLED=                          # 留空 = 全部禁用
```

未启用的插件文件会被加载器扫描到但不注册，不会出现在 LLM 可见的技能列表中。

### 主动调用桌宠能力（可选）

插件如需主动让桌宠说话或执行动作（而不是被动返回数据），可以导入全局上下文 `SKILL_CTX`：

```python
from pet.skills.context import SKILL_CTX

def _alarm() -> dict:
    SKILL_CTX.speech("危险！", duration=3000)
    SKILL_CTX.action("bounce", kwargs={"dx": 0, "dy": -200})
    return {"summary": "已报警"}
```

### 注意事项

- **参数名严格匹配**：`add_method` 的 `args` 键名必须与 handler 函数参数名一致（会以 `**kwargs` 展开）。
- **异常隔离**：handler 抛出的异常会被 Executor 捕获，译为 `[✗ skill] 失败: ...` 传给 LLM，不会崩溃主流程。
- **避免耗时操作**：Skill 在主决策线程（后台 BrainWorker）中同步执行，过长会阻塞二次 LLM 调用。有 IO/网络调用请设置超时。
- **安全边界**：涉及文件/网络访问的插件请参考 `file_ops.py` 的路径限制实现，避免提供任意路径访问。
- **序列化友好**：返回的 dict 必须能被 `json.dumps(ensure_ascii=False)` 序列化。

## 🏗️ 核心架构

```
┌─────────────────────────────────────────┐
│           PetAgent（编排层）             │
│  Scheduler → StateMachine → BrainWorker │
└──────┬──────────────┬──────────────┬────┘
       │              │              │
       ▼              ▼              ▼
┌──────────┐   ┌──────────┐   ┌──────────┐
│  Brain   │   │  Action  │   │   Skill  │
│ (LLM 决策)│   │(动作队列) │   │ (插件系统)│
│ Behavior │   │ Queue +  │   │ Registry │
│          │   │ Gravity  │   │ Executor │
└────┬─────┘   └─────┬────┘   └──────────┘
     │               │
     │               ▼
     │         ┌──────────┐
     └────────▶│    UI    │
               │ PetWindow│
               │ + Bubble │
               └──────────┘
```

**决策流水线（mid_tick）**：

```
Scheduler.mid_tick(30s)
    → StateMachine.try_transition(TALKING)   # 原子状态切换
    → 截图 + Win32 窗口探测
    → Behavior.decide_stream(context, image, on_chunk)
    → 流式推送 Speech 到气泡
    → 解析 Action 序列 emit 到 ActionQueue
    → 队列依次执行（带超时保护）
```

**对话流水线（chat）**：

```
ChatBubble.chat_submitted("…")
    → PetAgent._trigger_chat → 状态切换 INTERACTING
    → 清空动作队列 + 播放 thinking
    → Behavior.chat_decide_stream(message, context, image)
    → 流式 speech + action + 可选 skill 二次调用
```

## 🛠️ 依赖

- **GUI**：PySide6 6.11.1
- **AI**：openai >= 1.0
- **截图**：mss >= 9.0
- **系统监控**：psutil >= 5.9
- **重试**：tenacity >= 8.2
- **分词**：jieba >= 0.42

完整列表见 [requirements.txt](requirements.txt)。

## 📝 日志

应用日志按天切分，保留 7 天，输出至 `logs/deskpet.log`。日志级别由 `.env` 中 `LOG_LEVEL` 控制。

## 📜 许可

MIT
