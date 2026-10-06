<!-- 由 scripts/gen_docs.py 生成，请勿手工编辑 -->

# 配置项参考

配置项定义在 `pet/config.py` 的 `_KEY_META`（唯一真源），这里是它的机械展开。
用户配置写入 `settings.json`（位置见「持久化」一节），`hidden` 的项只能手改文件。

运行 `python scripts/gen_docs.py` 可重新生成本文件。

## 设置界面页签

页面归属由 `pet/ui/settings_window.py` 手写，和 `_KEY_META.category` **不是一回事**（例如 `MEMORY_*` 的 category 是 `memory`，却显示在「行为」页）。需要改归属时改界面代码，然后重新生成本文档。

### 连接

| 键 | 类型 | 默认 | 需重启 | 说明 |
|---|---|---|---|---|
| `BRAIN` | str | `"local"` |  | LLM 调用模式（可选：local / api / ollama） |
| `LLM_URL` | str | `""` |  | 首选 API 地址(需兼容 OpenAI 格式) |
| `OLLAMA_BASE_URL` | str | `"http://localhost:11434/v1"` |  | Ollama 服务地址 |
| `LLM_KEY` | str | `""` |  | 首选 API Key |
| `LLM_MODEL` | str | `""` |  | 首选 LLM 模型名称 |
| `LLM_TIMEOUT` | float | 20 |  | LLM 请求超时(秒) |
| `LLM_MAX_RETRIES` | int | 2 |  | LLM 最大重试次数 |
| `LLM_RETRY_DELAY` | float | 1 |  | 重试延迟(秒) |
| `LLM_RETRY_MAX_DELAY` | float | 4 |  | 最大重试延迟(秒) |
| `LLM_TEMPERATURE` | float | 0.7 |  | LLM 采样温度 |
| `LLM_MAX_TOKENS_INTERACT` | int | 1024 |  | 交互模式LLM输出Token上限 |
| `LLM_MAX_TOKENS_CHAT` | int | 4096 |  | 聊天模式LLM输出Token上限 |
| `LLM_MAX_TOKENS_AUTONOMOUS` | int | 4096 |  | 自主模式LLM输出Token上限 |
| `LLM_TOOLS_ENABLED` | bool | true |  | 是否可使用工具 |
| `LLM_TOOL_MAX_ROUNDS` | int | 30 |  | 工具调用最大轮次（二十问等长流程游戏需要 ≥22 轮）（≥10, ≤99） |
| `LLM_CACHE_PROMPT` | bool | false |  | 启用 Prompt 缓存 |
| `LLM_THINKING_DISABLED` | bool | false |  | 关闭思考模式 |
| `LLM_FALLBACK_ENABLED` | bool | true |  | 调用报错或超时重试时自动切换到备选模型 |
| `LLM_URL_ALT` | str | `""` |  | 备选 API 地址(需兼容 OpenAI 格式)，留空则沿用首选 |
| `LLM_KEY_ALT` | str | `""` |  | 备选 API Key，留空则沿用首选 |
| `LLM_MODEL_ALT` | str | `""` |  | 备选 LLM 模型名称，留空则沿用首选 |

### 行为

| 键 | 类型 | 默认 | 需重启 | 说明 |
|---|---|---|---|---|
| `SCHEDULER_MID_MS` | int | 300000 |  | 自主决策间隔(毫秒) |
| `SCHEDULER_AUTO_START_MID` | bool | true |  | 自动启动 mid_tick(自主决策) |
| `CONTEXT_HISTORY_ENTRIES` | int | 15 |  | 每轮注入上下文数量上限(同时也是候选池容量) |
| `VISION_ENABLED` | bool | false |  | 启用视觉理解(需多模态模型支持) |
| `VISION_SCALE` | float | 0.7 |  | 截图缩放比例(0.1~1.0) |
| `SCREENSHOT_FORMAT` | str | `"jpeg"` |  | 截图编码格式（可选：jpeg / png） |
| `SANITY_CRITICAL_THRESHOLD` | int | 20 |  | 理智临界值，低于该值导致异常行为 |
| `EMBEDDING_ENABLED` | bool | false | 是 | 启用向量记忆(需配置下方 API) |
| `EMBEDDING_URL` | str | `""` | 是 | Embedding API 地址(需兼容 OpenAI 格式) |
| `EMBEDDING_KEY` | str | `""` | 是 | Embedding API Key |
| `EMBEDDING_MODEL` | str | `""` | 是 | Embedding 模型名 |
| `EMBEDDING_DIM` | int | 256 | 是 | 向量维度(需与模型匹配)（≥64, ≤8192） |
| `MEMORY_MAX_CAPACITY` | int | 200 |  | 记忆最大容量 |
| `MEMORY_RECALL_COUNT` | int | 15 |  | 每次对话召回的记忆条数 |
| `MEMORY_L3_EXPIRE_DAYS` | int | 3 |  | L3临时记忆过期天数 |

### 提示词

| 键 | 类型 | 默认 | 需重启 | 说明 |
|---|---|---|---|---|
| `PET_PERSONALITY` | str | `""` |  | 宠物人格描述(注入 system prompt) |
| `PET_PERSONALITY_EXAMPLES` | str | `""` |  | 人格台词范例(每行一句,注入 system prompt 校准语感) |
| `INTERACT_GRABBED_PROMPT` | str | `""` |  | 被抓取时的自定义回复 prompt |
| `INTERACT_RELEASED_PROMPT` | str | `""` |  | 被放下时的自定义回复 prompt |
| `INTERACT_WINDOW_DISAPPEARED_PROMPT` | str | `""` |  | 窗口消失时的自定义回复 prompt |

### 通用

| 键 | 类型 | 默认 | 需重启 | 说明 |
|---|---|---|---|---|
| `PET_WIDTH` | int | 125 | 是 | 宠物窗口宽度(px) |
| `PET_HEIGHT` | int | 125 | 是 | 宠物窗口高度(px) |
| `BUBBLE_MAX_WIDTH` | int | 300 | 是 | 气泡最大宽度(px) |
| `BUBBLE_FONT_SIZE` | int | 14 | 是 | 气泡字号 |
| `TOOLS_ENABLED` | str_list | ["*"] | 是 | 启用的工具插件(逗号分隔, *=全部) |
| `SHOW_TRAY` | bool | true | 是 | 显示托盘图标 |
| `AUTO_START_ON_BOOT` | bool | false |  | 开机自动启动 |
| `VOICE_INPUT_ENABLED` | bool | false |  | 启用语音输入 |
| `VOICE_HOTKEY` | str | `"F8"` |  | 语音输入全局热键 |
| `XF_APPID` | str | `""` |  | 讯飞语音听写 APPID |
| `XF_API_KEY` | str | `""` |  | 讯飞语音听写 API Key |
| `XF_API_SECRET` | str | `""` |  | 讯飞语音听写 API Secret |

## 未在设置界面列出的键

经 `hidden`（高级设置，仅 settings.json）或未接入界面；按 `category` 分组：

### category = `appearance`

| 键 | 类型 | 默认 | 高级 | 需重启 | 说明 |
|---|---|---|---|---|---|
| `CRASH_REPORT_ENABLED` | bool | true | 是 | 是 | 启用崩溃信息收集(写入 logs/crash 目录) |
| `HIDE_CONSOLE` | bool | true | 是 | 是 | 启动时隐藏控制台窗口 |
| `LOG_LEVEL` | str | `"DEBUG"` | 是 |  | 日志级别(DEBUG/INFO/WARNING/ERROR) |
| `PET_FPS` | int | 15 | 是 | 是 | 动画帧率 |

### category = `behavior`

| 键 | 类型 | 默认 | 高级 | 需重启 | 说明 |
|---|---|---|---|---|---|
| `ACTION_TIMEOUT_MS` | int | 90000 | 是 |  | 单个动作超时(毫秒) |
| `ATTENTION_THRESHOLDS` | str_list | ["10", "20", "30"] |  |  | 连续未互动轮次阈值，达到后向桌宠注入求关注提示（如10/20/30轮） |
| `BRAIN_STUCK_TIMEOUT` | int | 300 | 是 |  | 脑线程无进展超时(秒)：autonomous/interacting 状态下持续无输出、无工具进展超过该时长才判定挂死（进行中的游戏对局不计时） |
| `CONTEXT_HALF_LIFE_S` | int | 1800 | 是 |  | 上下文评分半衰期(秒) |
| `CONTEXT_PERSIST_ENABLED` | bool | true | 是 |  | 启用上下文持久化 |
| `CONTEXT_TOKEN_BUDGET` | int | 8192 | 是 |  | 上下文token预算上限 |
| `FALL_DOWN_SECONDS` | float | 1.5 | 是 |  | 下落超过该秒数，落地先播放 fall_down 动作再恢复队列 |
| `FILE_DROP_BUBBLE_TIMEOUT_S` | int | 12 | 是 |  | 文件气泡无操作收起秒数 |
| `FILE_DROP_DENY_PATTERNS` | str_list | [".env*", "*.key", "*.pem", "*.pfx", "*.p12", ".npmrc", ".netrc", ".pgpass", ".git-credentials", "id_rsa*", "id_ed25519*"] | 是 |  | 拒收名单，按文件名 glob 匹配 |
| `FILE_DROP_ENABLED` | bool | true | 是 |  | 拖入文件交互总开关 |
| `FILE_DROP_MAX_CHARS` | int | 1500 | 是 |  | 单文件进入 prompt 的字符上限 |
| `FILE_DROP_MAX_FILES` | int | 5 | 是 |  | 单次拖入数量上限 |
| `FILE_DROP_MAX_FILE_MB` | int | 10 | 是 |  | 单文件体积上限(MB) |
| `FILE_DROP_MAX_PIXELS` | int | 40000000 | 是 |  | draft 之后的像素上限，判据为大于等于 |
| `FILE_DROP_READ_CONTENT` | bool | true | 是 |  | 读取文件内容开关，关闭后所有文件只传元信息 |
| `FILE_DROP_TASTE_CHARS` | int | 300 | 是 |  | 尝一口路径的字符上限 |
| `FOOD_ENABLED` | bool | true |  | 是 | 觅食总开关（桌宠自主生成食物并吃掉），修改后需重启生效 |
| `FOOD_TTL_SECONDS` | int | 300 |  |  | 觅食食物存活秒数，超时未吃自动消失 |
| `INTERACT_FED_PROMPT` | str | `""` | 是 |  | 喂食交互的自定义 prompt 模板 |
| `INTERACT_FILE_REJECT_PROMPT` | str | `""` | 是 |  | 拒收台词的自定义 prompt 模板 |
| `INTERACT_TAKE_A_BITE_PROMPT` | str | `""` | 是 |  | 尝一口交互的自定义 prompt 模板 |
| `MOOD_AFFECTION_BASELINE` | float | 50.0 |  |  | 好感度回归基线，长期疏远会缓慢回落到此值 |
| `MOOD_AFFECTION_DECAY_PER_TICK` | float | 0.2 |  |  | 好感度每 tick 回归速率(点/300秒) |
| `MOOD_DECAY_ENABLED` | bool | true |  |  | 心理数值自然衰减总开关（愉悦/好感随时间回落） |
| `MOOD_GRACE_SECONDS` | int | 60 | 是 |  | 互动后免衰减秒数（防抖，避免刚被哄好又回落） |
| `MOOD_JOY_BASELINE` | float | 50.0 |  |  | 愉悦度回归基线，长期不互动会缓慢回落到此值 |
| `MOOD_JOY_DECAY_PER_TICK` | float | 2.0 |  |  | 愉悦度每 tick 回归速率(点/300秒) |
| `RECENT_EVENT_WINDOW_S` | int | 900 | 是 |  | 「最近发生了什么」事件保鲜窗口(秒)，超过后不再注入 |
| `SCHEDULER_AUTO_START_FAST` | bool | true | 是 |  | 自动启动 fast_tick |
| `SCHEDULER_AUTO_START_SLOW` | bool | true | 是 |  | 自动启动 slow_tick |
| `SCHEDULER_FAST_MS` | int | 1000 | 是 |  | fast_tick 间隔(毫秒) |
| `SCHEDULER_IDLE_TIMEOUT_MS` | int | 900000 | 是 |  | 空闲超时(毫秒)，超过后进入休眠 |
| `SCHEDULER_SLOW_MS` | int | 300000 | 是 |  | slow_tick 间隔(毫秒) |
| `UNCONSCIOUS_IDLE_SECONDS` | int | 60 | 是 |  | 连续待机秒数，超过后自动播放无意识化动作 |

### category = `connection`

| 键 | 类型 | 默认 | 高级 | 需重启 | 说明 |
|---|---|---|---|---|---|
| `LLM_ACTION_MIN_DIVISOR` | int | 25 | 是 |  | 动作权重最小除数 |
| `LLM_ACTIVE_PROFILE` | str | `"primary"` |  |  | 当前启用的模型方案（可选：primary / alternative） |
| `LLM_CREATE_TIMEOUT` | float | 30 |  |  | 建流超时(秒)：等待响应头超过该时长即放弃，避免服务端无响应时卡死 |
| `LLM_MAX_TOKENS_ANALYZE` | int | 4096 |  |  | 分析模式LLM输出Token上限 |
| `LLM_MAX_TOKENS_SUMMARY` | int | 1024 | 是 |  | 上下文摘要LLM输出Token上限 |
| `LLM_STREAM_TIMEOUT` | float | 120 |  |  | 流式调用总超时(秒)，超过后降级到本地决策 |
| `LLM_TOOL_PARALLEL` | bool | true | 是 |  | LLM 工具并行调用 |

### category = `memory`

| 键 | 类型 | 默认 | 高级 | 需重启 | 说明 |
|---|---|---|---|---|---|
| `EMBEDDING_DEDUP_THRESHOLD` | float | 0.6 | 是 |  | 向量语义去重距离阈值(0~1) |
| `MEMORY_EVENT_RECALL_COUNT` | int | 1 |  |  | 「你惦记着的事」随机注入的 event 记忆条数(0=关闭) |
| `MEMORY_RECALL_COOLDOWN_S` | int | 300 | 是 |  | 记忆召回冷却时间(秒) |
| `MEMORY_RERANK_WEIGHT_IMP` | float | 0.2 | 是 |  | 重排序-有效重要性权重 |
| `MEMORY_RERANK_WEIGHT_RECENCY` | float | 0.1 | 是 |  | 重排序-时效性权重 |
| `MEMORY_RERANK_WEIGHT_SIM` | float | 0.7 | 是 |  | 重排序-语义相似度权重 |

## 字段含义

- `type`：`str` / `int` / `float` / `bool` / `str_list`，决定 `settings.json` 的取值转换
- `default`：缺省值，用户未覆盖时生效
- `category`：内部归类，用于「改连接要重建客户端、改行为要刷新调度器」这类副作用判断
- `needs_restart`：改动后需重启才生效（界面会提示）
- `hidden`：`true` 表示不在界面显示，只能直接编辑 `settings.json`
- `enum` / `minimum` / `maximum`：取值约束，会写进 `settings-schema.json`

共 104 项（其中 51 项未在界面列出）。
