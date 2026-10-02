# Koishi AI Pet 

![image1](image1forReadMe.png)
![image2](image2forReadMe.png)

> 基于 PySide6 + LLM 的桌面 AI 虚拟宠物，形象来自东方Project的古明地恋，能感知屏幕、与窗口互动、与用户对话。

## 项目功能

- **自主行动**：通过模型调用控制桌宠行动，有自己的生活节奏，也可以沉默不语；接口报错或超时时自动切换到备选模型
- **视觉感知**：截图分析 + 窗口探测，理解屏幕上正在发生什么
- **物理交互**：模拟重力下落，可站立在其他窗口顶部；可拖拽、点击
- **主动对话**：可以键盘输入、语音输入（需要配置讯飞API），与桌宠对话
- **持久记忆**：使用SQLite实现持久记忆，带可视化记忆管理窗口（浏览/搜索/筛选/编辑）；部分近期事件会注入上下文
- **宠物状态**：有生理（饱食、精力）和心理（好感、愉悦、理智）参数，会影响桌宠行为
- **互动游戏**：内置猜数字、猜拳、井字棋、二十问，可以和桌宠游玩
- **自主觅食**：饿了会自己寻找食物、跳起来吃掉
- **音乐控制**：悬停桌宠可控制系统媒体播放/暂停、切歌、音量与静音
- **工具系统**：内置浏览器、天气、待办、文件操作、系统监控、知识库等工具，参照指南可以自行拓展

## 项目结构

```
KoishiAI/
├── pyproject.toml              # 项目配置 & 依赖
├── tests/                      # 单元测试（pytest，dev 可选依赖）
├── assets/actions/             # 帧动画素材（idle、walk、sit、sleep…）
└── pet/
    ├── app.py                  # 主入口
    ├── config.py               # 全局配置
    ├── action/                 # 动作系统：注册、ActionQueue、重力模拟
    ├── agent/                  # 调度层：PetAgent、Scheduler、StateMachine
    ├── brain/                  # LLM 集成：Behavior、prompts、memory、window_detector
    ├── pulse/                  # 心理数值引擎：Mood（好感/愉悦/理智）、Vitals（饱食/精力）
    ├── food/                   # 觅食系统：食物生成、过期、自主进食
    ├── game/                   # 回合制游戏：GameBase 容器 + 猜数字/猜拳/井字棋/二十问
    ├── tools/                  # 工具系统：Registry、Executor、内置工具
    ├── ui/                     # Qt 界面：宠物窗口、气泡、聊天框、托盘、设置
    └── voice/                  # 语音输入：麦克风采集、讯飞 STT
```

## 文档

- 完整开发文档索引（按目的选路径、文档地图、架构与子系统、ADR、运维手册、术语表）：
  [koishi-ai-pet · docs/README.md](https://github.com/Koishi007/koishi-ai-pet/blob/master/docs/README.md)；
  需要与本包版本严格对应时，在该页面切换到同版本的 tag
- 按版本分组的变更记录：[CHANGELOG.md](CHANGELOG.md)（在包内）

## 快速开始

### Windows

#### 使用 setup.bat（需要自己安装 Python，稳定）

1. 安装 Python 3.11~3.14：[python.org/downloads](https://www.python.org/downloads/)（勾选 **"Add Python to PATH"**；须为 **64 位标准版**，不支持 32 位及 free-threaded 版本）
2. 在右侧下载**最新**的release版本
3. **双击 `setup.bat`**，自动完成安装和桌面快捷方式创建
4. 双击桌面 **"Koishi AI Pet"** 快捷方式启动

#### 使用 koishi-manager（不需要自己安装 Python，公测中）

1. 在项目根目录打开终端，运行下方代码
```
koishi-manager.exe install
```
2. 双击 `venv/Scripts/koishi.exe` 即可

> 若出现 bug，请换回 setup.bat 安装，并将 bug 内容、复现条件等重要信息提交 issue。

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

桌宠的"智商"来自大模型（LLM）。模型通过 **API**（理解为"网络接口"）接收消息与屏幕截图，思考后返回对话和动作指令。

配置流程：在模型供应商注册账号 → 获取 **API Key**（一串密钥）→ 填到桌宠设置中。API 按使用量计费，单次互动大概几厘。

### 三种调用模式

在设置的「LLM 调用模式」中可选：

| 模式 | 说明 | 适合 |
|------|------|------|
| `local` | 离线模式，不调用 LLM，随机执行预设动作 + 固定台词 | 无需 API 配置，快速体验桌宠的交互和动作 |
| `api` | 调用 LLM，连接 OpenAI 兼容接口（填 `LLM_URL` + `LLM_KEY`） | 正常使用，选购第三方 API（Mimo、硅基流动、DeepSeek 等） |
| `ollama` | 调用 LLM，连接本机 Ollama 服务（填 `OLLAMA_BASE_URL`，默认 `http://localhost:11434/v1`） | 已有 Ollama 本地部署的用户 |

> 需要完整对话与智能交互时选 `api` 或 `ollama`；只需看桌宠跑起来的样子时选 `local`，无需任何配置。

### 配置步骤

1. 启动桌宠，打开托盘菜单 → **设置**
2. **「连接」页签**：填入**首选 API 地址**（Base URL）和**首选 API Key**（密钥），首选模型名称设为供应商对应的模型名，如 `mimo-v2.6-flash`
3. 点「测试连接」验证，成功后右下角保存

> 推荐方案：**deepseek-flash** - 支持视觉的多模态模型，推荐首选（查看 [DeepSeek 开放平台](https://platform.deepseek.com/) 获取 API 信息）
>
> 推荐方案：**mimo-v2.6-flash** - 原生多模态、价格便宜（查看 [Mimo 官网](https://mimo.mi.com/docs/zh-CN/quick-start/summary/first-api-call/) 获取 API 信息）
>
> 记忆系统推荐 **智谱 embedding-3** - 便宜且快速；不配置也能用基础的关键词匹配记忆

### 首选与备选模型

「连接」页签提供两套模型方案，用于接口不稳定时兜底：

| 配置项 | 首选 | 备选（留空则沿用首选对应项） |
|--------|------|------------------------------|
| API 地址 | `LLM_URL` | `LLM_URL_ALT` |
| API Key | `LLM_KEY` | `LLM_KEY_ALT` |
| 模型名称 | `LLM_MODEL` | `LLM_MODEL_ALT` |

- **模型方案**区可一键在首选与备选之间切换（`LLM_ACTIVE_PROFILE`），当前启用的方案与模型名实时显示
- 勾选「调用报错/超时重试时自动切换到备选模型」（`LLM_FALLBACK_ENABLED`，默认开启）后，请求报错、读超时、建流超时时会在重试前自动换到备选模型继续，每条请求链结束后回到所选方案

> 只有一套 API 时无需配置备选，留空即可，行为与只填首选时一致。

### 桌宠基本设置

- **「提示词」页签**：设置角色人格（可参考项目目录下的 `预设人格提示词.md`），并填写「人格台词范例」 - 每行一句真实台词，注入后用于校准说话语感，只参考风格不照搬内容（抓起、释放、窗口消失等提示词有默认值，可选填）
- **语音输入**：如需语音对话，在「通用」页签填讯飞听写 API 并开启语音输入（热键默认 F8）

> 模型兼容 OpenAI 格式接口，硅基流动、DeepSeek 等均可直接填入。Ollama 本地部署理论上也支持。

## 开发与测试

跑测试需要 **PySide6（运行时依赖）+ pytest（dev 依赖）**，用 `[dev]` 一次装齐：

```bash
# Windows (PowerShell)
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m pytest

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
python -m pytest
```

> 只装 `pip install -e .`（不带 `[dev]`）会缺少 pytest，测试无法运行 - 这是本地"测试跑不起来"最常见的原因。
> 测试范围由 `pyproject.toml` 的 `[tool.pytest.ini_options]` 决定（`testpaths = ["tests"]`）。

## 更新

项目提供一键更新脚本，会自动从 GitHub 下载最新 Release 源码并更新依赖，**保留虚拟环境、配置与数据**。
> 更新脚本要访问 GitHub 的 Release 接口与源码包，需保证网络可达。
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
| `default` | `tool_search` | `list_groups` / `search` | 工具发现：列出分组、按关键词搜索并自动激活匹配分组 |
| `default` | `food` | `spawn` / `status` | 觅食：在桌面生成食物、查询食物位置与过期状态 |
| `default` | `game` | `list` / `init` / `play` / `stop` | 回合制小游戏：列出游戏、开局、玩一回合、主动结束 |
| `default` | `recall` | `search` / `browse` | 回忆：主动检索桌宠关于用户的长期记忆（语义回忆、分页翻阅） |
| `web` | `browser` | `search` / `read_url` / `screenshot_url` / `close` | 多引擎网页搜索、读取网页正文（分页）、截图 |
| `web` | `web_search` | `search` / `deep_search` | SearXNG / Bing API 搜索，深度搜索自动抓取全文 |
| `info` | `weather` | `get_current` / `get_forecast` | 基于 Open-Meteo 的免费天气查询 |
| `info` | `system_monitor` | `get_overview` / `get_top_processes` / `get_memory_detail` / `get_network` | CPU、内存、磁盘、电池、进程 |
| `file` | `file` | `list_dir` / `read_file` / `write_note` / `write_file` | 列目录、读写文件（限桌面/文档） |
| `productivity` | `timer` | `set` / `list` / `cancel` / `cancel_all` | 倒计时定时器，到时宠物主动提醒 |
| `productivity` | `todo` | `add` / `list` / `toggle` / `delete` / `update` | 待办事项管理 |
| `knowledge` | `knowledge` | `search` / `list` | RAG 知识库：语义检索、知识条目管理 |

### food

觅食系统：桌宠饱食度低时会主动生成食物并走过去吃掉，也可以从悬停按钮或直接投喂触发。

- `spawn(food_type)`：在桌面随机位置生成食物（10 种 emoji，可指定类型），返回坐标与偏移
- `status`：查询当前食物的实时位置、是否到达、剩余过期时间
- 食物会随时间过期变质，桌宠到达后自动"吃掉"并触发互动台词

### game

回合制小游戏系统。桌宠会主动邀请玩家玩游戏：

| 游戏 | 玩法 |
|------|------|
| `guess_number` | 猜数字：随机生成 1-100 的数，桌宠每回合猜一个，给桌宠反馈"大了/小了"，7 次内猜中算赢 |
| `rps` | 猜拳：石头剪刀布，桌宠先出拳、玩家后出，三局两胜，15 秒未出拳判负 |
| `tic_tac_toe` | 井字棋：3×3 棋盘，随机先后手，先连成三子获胜，15 秒未落子判负 |
| `twenty_questions` | 二十问：玩家心想一个东西，桌宠问最多 20 个"是/否"问题猜出它，在面板点"是/否/不确定"作答；桌宠可随时给出最终猜测由玩家确认对错，20 问用完未猜中算它输 |

游戏流程：`game__list` 了解可选游戏 → `game__init` 开局 → `game__play` 每回合推进 → 返回 `ended=True` 即结束。猜拳、井字棋和二十问有可视化交互面板，点击按钮/格子即可操作。

### recall

记忆检索元工具：每轮自动注入的记忆条数有限（按核心/最近/语义匹配分槽），当桌宠感觉记忆不完整、或用户提到过去的事而注入段中没有相关内容时，可主动检索自己的长期记忆补齐。

- `search(query, limit)`：按语义或关键词回忆与查询相关的记忆（向量检索可用时走语义检索，否则降级关键词匹配）
- `browse(start_date, end_date, keyword, page)`：分页翻阅全部记忆，可按创建日期范围（`YYYY-MM-DD`，闭区间、可只填一端）、关键词筛选，每页 10 条

检索到的记忆进入召回冷却（防止随后输出重复的 Memory 行）并获得回忆强化（L3 高频访问自动升级 L2）。

### browser

内嵌 Playwright 无头浏览器，每次调用独立实例，用完自动回收。

**前置依赖**：

```bash
pip install playwright
playwright install chromium
```

Playwright 及浏览器二进制缺失时，工具加载直接报错。

**反爬措施**：
- 原生 `add_init_script`：隐藏 `navigator.webdriver`、伪造 `window.chrome`、`navigator.plugins`、`navigator.languages`
- 自定义桌面 Chrome UA + `--disable-blink-features=AutomationControlled` 启动参数
- Bing/百度/搜狗使用「模拟真人」搜索（进首页 → 填搜索框 → 敲回车），避免直接 URL 跳转触发反爬

**安全限制**：`read_url` / `screenshot_url` 仅接受 `http://` 或 `https://` 开头的 URL，拒绝 `file://` 等协议。

**配置**（`pet/tools/browser/config.json`）：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `headless` | bool | `true` | 无头模式；`false` 可见窗口便于调试 |
| `search_engine` | str | `"bing"` | 搜索引擎：`"bing"` / `"baidu"` / `"sogou"` / `"duckduckgo"` |
| `user_agent` | str | Chrome 桌面 UA | 自定义 UA，用于反爬 |
| `ignore_https_errors` | bool | `false` | 忽略 HTTPS 证书错误 |
| `no_sandbox` | bool | `false` | 禁用 Chrome 沙箱（仅容器等特殊环境需开启） |

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

### file

所有文件操作限定在桌面和文档目录内，保证安全。

无需额外配置。

### timer

倒计时定时器，支持持久化（重启后仍有效）。到时宠物主动说话提醒。

无需额外配置。

### todo

待办事项管理，右键菜单可打开管理面板。

无需额外配置。

## 悬停交互与右键菜单

### 悬停桌宠

鼠标靠近桌宠时，顶部浮现三个快捷按钮：

- **对话**：点击展开聊天输入框
- **喂食**：点击弹出喂食气泡，输入想喂的食物
- **音乐**：点击展开音乐控制条 - 上一首 / 播放·暂停 / 下一首、音量加减、静音

### 桌宠身体右键

右键点击桌宠本体，弹出完整功能菜单：

| 菜单项 | 功能 | 用法 |
|--------|------|------|
| **开启/关闭自主行动** | 控制 AI 是否自动思考、说话、走动。开启后桌宠会定时观察屏幕并自主产生行为 | 想让它自由活动时开启；专心工作时关闭让它安静挂机 |
| **调试面板** | 打开实时状态窗口，显示饥饿、精力、心情等内部数值 | 开发调试用；普通用户一般不需要 |
| **日志** | 打开运行日志窗口，实时查看 LLM 调用、工具执行、错误等信息 | 排查问题或了解幕后发生了什么 |
| **对话历史** | 查看与 LLM 的完整对话记录 | 回顾之前的互动；了解模型如何理解上下文 |
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
| **开启/关闭鼠标穿透** | 开启后鼠标可穿透桌宠，不影响点击下方窗口 |
| **设置** | 打开设置窗口 |
| **退出** | 退出应用程序 |

## 许可

GPL-3.0
