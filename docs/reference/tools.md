<!-- 由 scripts/gen_docs.py 生成，请勿手工编辑 -->

# 内置工具参考

工具分组与方法的机械展开；怎么写一个新工具见 [工具开发指南](../tool-development.md)，测试与提交规范见 [CONTRIBUTING.md](../../CONTRIBUTING.md)。
工具调用名是 `工具名__方法名`（双下划线），参数里的 `aside` 由注册表统一追加。

## 分组

| 分组 | 工具 |
|---|---|
| `default` | `tool_search`、`food`、`game`、`recall` |
| `file` | `file_ops` |
| `info` | `system_monitor`、`weather` |
| `knowledge` | `knowledge` |
| `productivity` | `timer`、`todo` |
| `web` | `browser`、`web_search` |

## 独立工具（`pet/tools/<目录>/`）

### `browser`

- 目录：`pet/tools/browser/`
- 分组：`web`
- 说明：浏览器操作（搜索网页、读取网页正文、截图）

| 方法 | 参数 | 说明 |
|---|---|---|
| `search` | `query`（str, 必需） 搜索关键词<br>`count`（int, 默认 10） 返回结果数量(1-10) | 搜索关键词，返回结构化搜索结果（标题、URL、摘要），最多10条 |
| `read_url` | `url`（str, 必需） 要读取的网页地址（包含 http/https）<br>`max_chars`（int, 默认 10000） 最大提取字符数<br>`wait_seconds`（float, 默认 3.0） 页面加载等待时间(秒)<br>`page`（int, 默认 1） 分页页码，从1开始<br>`page_size`（int, 默认 3000） 每页字符数 | 用无头浏览器打开URL并提取页面正文文本，支持分页（默认每页3000字），返回当前页内容及总页数 |
| `screenshot_url` | `url`（str, 必需） 要截图的网页地址（包含 http/https）<br>`width`（int, 默认 1280） 视口宽度(px)<br>`height`（int, 默认 800） 视口高度(px)<br>`wait_seconds`（float, 默认 3.0） 页面加载等待时间(秒)<br>`full_page`（bool, 默认 False） 是否截取整页（默认仅可视区域） | 用无头浏览器打开URL并截图，可以'看到'网页外观 |
| `close` | 无参数 | 关闭浏览器，释放内存（所有网页操作完成后调用） |

### `file_ops`

- 目录：`pet/tools/file_ops/`
- 分组：`file`
- 说明：文件操作（读写、列目录，限桌面/文档）

| 方法 | 参数 | 说明 |
|---|---|---|
| `list_dir` | `path`（str, 默认 "~/Desktop"） 目录路径，默认桌面<br>`page`（int, 默认 1） 页码，从1开始 | 列出指定目录内容（限桌面/文档，分页每页50项） |
| `read_file` | `path`（str, 必需） 文件路径<br>`max_chars`（int, 默认 1000） 本次读取最大字符数<br>`offset`（int, 默认 0） 读取起始位置（字符偏移），从0开始，上限5000 | 读取文本文件（限桌面/文档，每次1000字符，支持 offset 翻页，最多读5000字符） |
| `write_note` | `filename`（str, 必需） 文件名（含扩展名）<br>`content`（str, 必需） 写入内容 | 在桌面创建一个文本笔记 |
| `write_file` | `path`（str, 必需） 文件路径<br>`content`（str, 必需） 写入内容<br>`mode`（str, 默认 "w", 可选：w/a） 写入模式: w=覆盖, a=追加 | 写入/覆盖/追加文件内容（限桌面/文档。追加时 mode='a'，覆盖 mode='w'） |

### `knowledge`

- 目录：`pet/tools/knowledge/`
- 分组：`knowledge`
- 说明：RAG 知识库。可语义检索用户手动录入的知识和文档，只读。

| 方法 | 参数 | 说明 |
|---|---|---|
| `search` | `query`（str, 必需） 搜索查询文本<br>`limit`（int, 默认 3） 返回结果数量(1~10) | 语义检索知识库，返回与查询最相关的知识片段。当用户询问你之前学过的知识、你录入的笔记、或你需要参考已存储的文档来回答问题时调用此工具。 |
| `list` | `page`（int, 默认 1） 页码(从1开始) | 分页列出知识库中的条目。 |

### `system_monitor`

- 目录：`pet/tools/system_monitor/`
- 分组：`info`
- 说明：系统资源监控（CPU、内存、磁盘、电池、进程）

| 方法 | 参数 | 说明 |
|---|---|---|
| `get_overview` | 无参数 | 获取系统整体概况（CPU、内存、磁盘、电池、运行时长） |
| `get_top_processes` | `count`（int, 默认 5） 返回进程数量（1-20） | 获取占用CPU最高的前N个进程 |
| `get_memory_detail` | 无参数 | 获取详细内存信息（物理内存+交换分区） |
| `get_network` | 无参数 | 获取网络流量统计（累计发送/接收字节数） |

### `timer`

- 目录：`pet/tools/timer/`
- 分组：`productivity`
- 说明：倒计时定时器，设定后宠物会在指定时间后主动提醒。

| 方法 | 参数 | 说明 |
|---|---|---|
| `set` | `duration`（int, 必需） 倒计时秒数(1~86400)<br>`label`（str, 默认 "时间到"） 提醒内容标题 | 设置一个倒计时定时器。当用户说「提醒我X分钟后Y」「X分钟后喊我」时调用。 |
| `list` | 无参数 | 查看当前所有活跃的定时器。 |
| `cancel` | `timer_id`（str, 必需） 定时器ID | 取消指定定时器。 |
| `cancel_all` | 无参数 | 取消所有活跃的定时器。 |

### `todo`

- 目录：`pet/tools/todo/`
- 分组：`productivity`
- 说明：待办事项管理。支持添加、查看、完成、删除任务。

| 方法 | 参数 | 说明 |
|---|---|---|
| `add` | `title`（str, 必需） 任务标题 | 添加新待办事项 |
| `list` | `status`（str, 默认 "pending", 可选：pending/done/all） 状态: pending/done/all | 查询任务列表 |
| `toggle` | `todo_id`（int, 必需） 任务ID | 切换任务完成状态（已完成↔恢复待办） |
| `delete` | `todo_id`（int, 必需） 任务ID | 删除指定任务 |
| `update` | `todo_id`（int, 必需） 任务ID<br>`title`（str, 必需） 新标题 | 修改已有任务的标题 |

### `weather`

- 目录：`pet/tools/weather/`
- 分组：`info`
- 说明：天气查询（当前天气、未来预报）

| 方法 | 参数 | 说明 |
|---|---|---|
| `get_current` | `city`（str, 默认 "Beijing"） 城市名（中英文均可，如 Beijing/北京/Tokyo/東京） | 查询指定城市当前天气（含未来3日简要预报） |
| `get_forecast` | `city`（str, 默认 "Beijing"） 城市名（中英文均可）<br>`days`（int, 默认 3） 预报天数（1-7） | 查询指定城市未来N日天气预报 |

### `web_search`

- 目录：`pet/tools/web_search/`
- 分组：`web`
- 说明：网络搜索，获取实时信息、新闻、百科知识

| 方法 | 参数 | 说明 |
|---|---|---|
| `search` | `query`（str, 必需） 搜索关键词<br>`count`（int, 默认 5） 返回结果数量（1-10）<br>`language`（str, 默认 "zh-CN", 可选：zh-CN/en-US/ja-JP） 语言/区域代码（zh-CN / en-US / ja-JP 等）<br>`page`（int, 默认 1） 页码，从1开始（结果不够时翻页获取更多） | 搜索网络获取最新信息（返回标题和摘要，速度快） |
| `deep_search` | `query`（str, 必需） 搜索关键词<br>`count`（int, 默认 5） 搜索结果数量（1-10）<br>`language`（str, 默认 "zh-CN", 可选：zh-CN/en-US/ja-JP） 语言/区域代码<br>`extract_top`（int, 默认 2） 抓取正文的结果条数（1-3，越多越慢）<br>`page`（int, 默认 1） 页码，从1开始 | 深度搜索：搜索后自动抓取前几条结果的页面正文，信息更完整详细（比 search 慢） |

## 元工具（注册在 `pet/tools/registry.py`）

元工具由系统内置，不可禁用，也不出现在工具管理列表中。

### `tool_search`

- 分组：`default`
- 说明：工具发现：浏览和搜索可用的工具

_（未解析到 add_method 调用）_

### `food`

- 分组：`default`
- 说明：觅食：在桌面上生成食物，或查询食物状态与距离

_（未解析到 add_method 调用）_

### `game`

- 分组：`default`
- 说明：游戏：玩回合制小游戏，如猜数字

_（未解析到 add_method 调用）_

### `recall`

- 分组：`default`
- 说明：回忆：主动检索你关于用户的长期记忆。系统每轮自动注入的记忆条数有限；当你感觉记忆不完整、想不起细节，或看到/听到的线索（画面里的人、物、地点、照片、应用，对话中的人名、事件、话题）与用户有关、而注入段中没有时，主动用此工具补齐。

_（未解析到 add_method 调用）_
