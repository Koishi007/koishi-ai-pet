# Koishi AI Pet 

![image1](image1forReadMe.png)
![image2](image2forReadMe.png)

> 基于 PySide6 + LLM 的桌面 AI 虚拟宠物，形象来自东方Project的古明地恋，能感知屏幕、与窗口互动、与你对话。

## 项目功能

- **自主行动**：通过模型调用控制桌宠行动
- **视觉感知**：截图分析 + 窗口探测，理解屏幕上正在发生什么
- **物理交互**：模拟重力下落，可站立在其他窗口顶部；可拖拽、点击
- **主动对话**：可以键盘输入、语音输入（需要配置讯飞API），与桌宠对话
- **持久记忆**：使用SQLite实现持久记忆
- **宠物状态**：有生理和心理参数，会影响桌宠行为
- **工具系统**：内置浏览器、天气、待办、文件操作、系统监控等工具，参照指南可以自行拓展

## 项目结构

```
KoishiAI/
├── pyproject.toml              # 项目配置 & 依赖
├── assets/actions/             # 帧动画素材（idle、walk、sit、sleep…）
└── pet/
    ├── app.py                  # 主入口
    ├── config.py               # 全局配置
    ├── action/                 # 动作系统：注册、ActionQueue、重力模拟
    ├── agent/                  # 调度层：PetAgent、Scheduler、StateMachine
    ├── brain/                  # LLM 集成：Behavior、prompts、memory、window_detector
    ├── pulse/                  # 心理数值引擎：Mood（好感/愉悦/理智）、Vitals（饱食/精力）
    ├── tools/                  # 工具系统：Registry、Executor、内置工具
    ├── ui/                     # Qt 界面：宠物窗口、气泡、聊天框、托盘、设置
    └── voice/                  # 语音输入：麦克风采集、讯飞 STT
```

## 快速开始

### Windows

1. 安装 Python 3.11~3.13：[python.org/downloads](https://www.python.org/downloads/)（勾选 **"Add Python to PATH"**）
2. 在右侧下载**最新**的release版本
3. **双击 `setup.bat`**，自动完成安装和桌面快捷方式创建
4. 双击桌面 **"Koishi AI Pet"** 快捷方式启动

### macOS / Linux

```bash
# 一键安装
chmod +x setup.sh && ./setup.sh

# 或手动安装
python3 -m venv venv
source venv/bin/activate
pip install -e .

# 启动
./venv/bin/koishi
# 或
python -m pet
```

### API 是什么？

桌宠的"智商"来自大模型（LLM）。模型通过 **API**（理解为"网络接口"）接收你的消息和屏幕截图，思考后返回对话和动作指令。

你需要做的就是：在模型供应商注册账号 → 获取 **API Key**（一串密钥）→ 填到桌宠设置中。API 按使用量计费，1 万次对话大约几块钱。

### 三种调用模式

在设置的「LLM 调用模式」中可选：

| 模式 | 说明 | 适合 |
|------|------|------|
| `local` | 离线模式，不调用 LLM，随机执行预设动作 + 固定台词 | 无需 API 配置，快速体验桌宠的交互和动作 |
| `api` | 调用 LLM，连接 OpenAI 兼容接口（填 `LLM_URL` + `LLM_KEY`） | 正常使用，选购第三方 API（Mimo、硅基流动、DeepSeek 等） |
| `ollama` | 调用 LLM，连接本机 Ollama 服务（填 `OLLAMA_BASE_URL`，默认 `http://localhost:11434/v1`） | 已有 Ollama 本地部署的用户 |

> 想有完整的对话和智能交互，选 `api` 或 `ollama`。只想看桌宠跑起来的样子，选 `local` 无需任何配置。

### 配置步骤

1. 启动桌宠，打开托盘菜单 → **设置**
2. **「连接」页签**：填入 **Base URL**（API 地址）和 **API Key**（密钥），模型名设为供应商对应的模型名，如 `mimo-v2.5`
3. 点「测试连接」验证，成功后右下角保存

> 推荐方案：**Mimo v2.5** — 原生多模态、价格便宜（查看 [Mimo 官网](https://mimo.xn--6kr4l1a.xyz/) 获取 API 信息）
>
> 记忆系统推荐 **智谱 embedding-3** — 便宜且快速；不配置也能用基础的关键词匹配记忆

### 桌宠基本设置

- **「提示词」页签**：设置角色人格，参考项目目录下的 `预设人格提示词.md`（抓起、释放、窗口消失等提示词有默认值，可选填）
- **「语音」页签**：如需语音输入对话，配置讯飞 API

> 模型兼容 OpenAI 格式接口，硅基流动、DeepSeek 等均可直接填入。Ollama 本地部署理论上也支持。

## 更新

项目提供一键更新脚本，会自动从 GitHub 下载最新 Release 源码并更新依赖，**保留你的虚拟环境、配置和数据**。
> 请保证你可以正常访问 GitHub，推荐使用Watt Toolkit加速
### Windows

双击 `update.bat` 即可。脚本会：

1. 对比本地版本与最新 Release 版本，已是最新则跳过
2. 下载最新 Release 源码包并同步到项目目录
3. 激活虚拟环境，更新依赖

### macOS / Linux

```bash
chmod +x update.sh && ./update.sh
```

> 如果本地有未提交的源码改动，更新脚本会以 Release 源码覆盖同名文件；但 `venv`、`logs`、`config.json`、数据库等用户数据不会被删除。

## 内置工具

| 分组 | 工具 | 方法 | 说明 |
|------|------|------|------|
| `web` | `browser` | `search` / `read_url` / `screenshot_url` | 多引擎网页搜索（Bing/百度/搜狗/DuckDuckGo）、读取网页正文（分页）、截图 |
| `web` | `web_search` | `search` / `deep_search` | SearXNG / Bing API 搜索，深度搜索自动抓取全文 |
| `info` | `weather` | `get_current` / `get_forecast` | 基于 Open-Meteo 的免费天气查询 |
| `info` | `system_monitor` | `get_overview` / `get_top_processes` / `get_memory_detail` / `get_network` | CPU、内存、磁盘、电池、进程 |
| `file` | `file_ops` | `list_dir` / `read_file` / `write_note` / `write_file` | 列目录、读写文件（限桌面/文档） |
| `productivity` | `timer` | `set` / `list` / `cancel` / `cancel_all` | 倒计时定时器，到时宠物主动提醒 |
| `productivity` | `todo` | `add` / `list` / `toggle` / `delete` / `update` | 待办事项管理 |
| `memory` | `knowledge` | `search` / `list` | RAG 知识库：语义检索、导入 txt/md |

### browser

内嵌 Playwright + playwright-stealth 无头浏览器，每次调用独立实例，用完自动回收。

**前置依赖**：

```bash
pip install playwright playwright-stealth
playwright install chromium
```

Playwright 及浏览器二进制缺失时，工具加载直接报错。`playwright-stealth` 缺失仅告警（反爬能力受限），不会阻塞加载。

**反爬措施**：
- `playwright-stealth` 全覆盖：`navigator.webdriver`、`plugins`、`languages`、`permissions`、`WebGL`、`media codecs`、`chrome.runtime` 等
- 自定义桌面 Chrome UA + `--disable-blink-features=AutomationControlled` 启动参数
- Bing/百度/搜狗使用「模拟真人」搜索（进首页 → 填搜索框 → 敲回车），避免直接 URL 跳转触发反爬

**安全限制**：`read_url` / `screenshot_url` 仅接受 `http://` 或 `https://` 开头的 URL，拒绝 `file://` 等协议。

**配置**（`pet/tools/browser/config.json`）：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `headless` | bool | `true` | 无头模式；`false` 可见窗口便于调试 |
| `search_engine` | str | `"bing"` | 搜索引擎：`"bing"` / `"baidu"` / `"sogou"` / `"duckduckgo"` |
| `user_agent` | str | Chrome 桌面 UA | 自定义 UA，用于反爬 |

### web_search

通过 SearXNG 或 Bing API 搜索网页，`deep_search` 自动抓取结果页面全文。

**配置**（`pet/tools/web_search/config.json`）：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `backend` | str | `"auto"` | 搜索后端：`"auto"` 自动选 / `"searxng"` / `"bing"` |
| `searxng_url` | str | `""` | SearXNG 实例地址 |
| `searxng_key` | str | `""` | SearXNG API Key（如需要） |
| `bing_search_key` | str | `""` | Bing Web Search API Key |

`backend: "auto"` 模式下优先 SearXNG，不可用时自动回退 Bing。至少配置一个后端，否则工具加载失败。

### knowledge

RAG 知识库，支持语义检索。可配置向量嵌入以启用语义搜索；未配置时使用关键词匹配。

**配置**（`pet/tools/knowledge/config.json`）：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `chunk_size` | int | `500` | 文本分块大小 |
| `chunk_overlap` | int | `50` | 分块重叠字符数 |
| `embedding_enabled` | bool | `false` | 启用向量嵌入 |
| `embedding_url` | str | `""` | Embedding API 地址 |
| `embedding_key` | str | `""` | Embedding API Key |
| `embedding_model` | str | `""` | Embedding 模型名 |
| `embedding_dim` | int | `256` | 向量维度（需与模型匹配） |

### weather

基于 [Open-Meteo](https://open-meteo.com/) 免费 API，无需 API Key。支持中英文城市名。

无需额外配置。

### system_monitor

依赖 `psutil`，首次加载时自动安装。

无需额外配置。

### file_ops

所有文件操作限定在桌面和文档目录内，保证安全。

无需额外配置。

### timer

倒计时定时器，支持持久化（重启后仍有效）。到时宠物主动说话提醒。

无需额外配置。

### todo

待办事项管理，右键菜单可打开管理面板。

无需额外配置。

## 右键菜单

桌宠有两个右键入口：**桌宠身体**和**系统托盘**。

### 桌宠身体右键

右键点击桌宠本体，弹出完整功能菜单：

| 菜单项 | 功能 | 用法 |
|--------|------|------|
| **开启/关闭自主行动** | 控制 AI 是否自动思考、说话、走动。开启后桌宠会定时观察屏幕并自主产生行为 | 想让它自由活动时开启；专心工作时关闭让它安静挂机 |
| **调试面板** | 打开实时状态窗口，显示饥饿、精力、心情等内部数值 | 开发调试用；普通用户一般不需要 |
| **日志** | 打开运行日志窗口，实时查看 LLM 调用、工具执行、错误等信息 | 排查问题或了解幕后发生了什么 |
| **对话历史** | 查看与 LLM 的完整对话记录 | 回顾之前的互动；了解模型如何理解你的上下文 |
| **记忆管理** | 打开记忆管理面板，查看、搜索、删除已存储的记忆条目 | 清理不准确的记忆；搜索"恋恋记住了什么" |
| **设置** | 打开设置窗口，配置模型连接、提示词、语音等 | 同托盘"设置"入口 |

**工具子菜单**：展开后列出所有已注册的工具，每个工具旁边有**勾选框**，可随时启用或禁用某个工具。

部分工具有专属面板入口：

| 工具 | 子菜单项 | 作用 |
|------|----------|------|
| `knowledge` | **知识库管理** | 手动添加/删除/搜索知识条目，管理 RAG 知识库 |
| `todo` | **查看待办** | 打开待办面板，查看、添加、勾选、删除待办事项 |

底部控制项：

| 菜单项 | 功能 |
|--------|------|
| **关闭/开启互动反应** | 关闭后拖拽、释放桌宠不再触发 LLM 对话，默认关闭|
| **隐藏桌宠** | 隐藏桌面窗口（可通过托盘重新显示） |
| **退出** | 退出应用程序 |

### 系统托盘右键

| 菜单项 | 功能 |
|--------|------|
| **隐藏/显示** | 切换桌宠窗口的可见状态 |
| **设置** | 打开设置窗口 |
| **退出** | 退出应用程序 |

## 工具开发指南

### 工具骨架

在 `pet/tools/` 下创建新目录，包含 `__init__.py`（必须）和实现文件：

```
pet/tools/my_tool/
├── __init__.py           # 注册入口（必须）
├── core.py               # 业务实现（推荐）
├── config.example.json   # 私有配置模板（可选，首次自动复制为 config.json）
└── requirements.txt      # 私有依赖（可选，首次自动安装）
```

核心文件说明：
- `__init__.py` -- 需定义 `TOOL_NAME`、`TOOL_DESCRIPTION`、`TOOL_GROUP`、`register()`
- `core.py` -- 业务逻辑可放在任意文件中，加载器不关心文件名
- `config.example.json` -- 工具私有配置模板，框架首次加载时自动复制为 `config.json`（已 gitignore）

`__init__.py` 模板：

```python
from pet.tools.my_tool.core import do_something

TOOL_NAME = "my_tool"
TOOL_DESCRIPTION = "一句话描述工具用途"
TOOL_GROUP = "productivity"  # 工具分组，LLM 通过 tool_search 按需发现

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

### 工具分组

`TOOL_GROUP` 决定工具所属分组，用于动态激活。当前分组：

| 分组 | 包含工具 | 说明 |
|------|----------|------|
| `default` | `tool_search` | 始终激活，无需搜索 |
| `web` | `browser`, `web_search` | 浏览器与网络搜索 |
| `file` | `file_ops` | 本地文件操作 |
| `info` | `weather`, `system_monitor` | 信息查询 |
| `productivity` | `todo`, `timer` | 效率工具 |
| `memory` | `knowledge` | 知识库 |

初始状态下 LLM 仅能调用 `default` 组的 `tool_search`。当 LLM 需要某个功能时，先调用 `tool_search.list_groups` 或 `tool_search.search(keyword)` 探索工具，匹配到的分组自动激活，后续请求即可调用该组的全部工具。

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

## 许可

GPL-3.0
