# 恋恋刷 B 站视频与互动设计

> 恋恋使用独立的 B 站账号：按关键词搜最近几天的视频，读字幕，随机取几部连同字幕交给演绎模型；是否点赞、投币、评论、私信由那一轮的模型自己决定。不发动态，不参与他人讨论。可手动触发，也可按间隔定时提醒。

## §0 现状

| 项 | 现状 | 位置 |
|---|---|---|
| 工具系统 | 目录即工具，`register(registry)` 注册方法，对外名称为 `工具名__方法名` | `pet/tools/__init__.py`、`pet/tools/registry.py` |
| 工具执行 | handler 在独立线程执行，默认 30s 超时；超时后线程继续运行，异常转为错误文本返回模型 | `pet/tools/executor.py` |
| 工具面板 | `pet/tools/<name>/panel.py` 加 `show_panel()`，经 `registry.add_menu_action` 从右键菜单打开 | `pet/tools/knowledge/panel.py` |
| 工具分组 | 非 `default` 组的工具需模型先经 `tool_search` 激活才可见 | `docs/tool-development.md`「分组与按需激活」 |
| 定时 | 三档 tick 加 `schedule_at` 一次性闹钟；`TOOL_CTX.register_alarm` 只存内存 | `pet/agent/scheduler.py`、`pet/tools/context.py` |
| LLM 连接 | `active_llm_profile` / `resolve_llm_profile` 取当前方案；独立客户端写法见 `memory_merge._call_llm` | `pet/brain/llm_client.py`、`pet/brain/memory_merge.py` |
| 记忆 | `MemoryStore.save(category, content, keywords, importance, level)`，类别常量 `MEMORY_CATEGORIES` | `pet/brain/memory.py` |
| 人格 | `PET_PERSONALITY` 与 `PET_PERSONALITY_EXAMPLES` 由 `build_system_prompt` 注入 | `pet/brain/prompts.py` |
| 凭证 | 无凭证设施，`settings.json` 存配置明文 | `pet/settings.py` |
| B 站能力 | 无 | 无 |

外部参考实现为 `c1overzz/dsh-bili-scroll` 及其内置 `bilibili-mcp`（MIT）。其宿主是 Node 插件，能力侧是 MCP 子进程，运行模型与本项目不同；可迁移的是流程结构：峰谷窗口、工具白名单、判重表、配额计数、发送后复核。代码不适用。

## §1 目标

- 一轮内完成：搜索、判重、读字幕，随机取几条，返回结构化结果给模型。
- 独立账号：二维码登录、凭证落盘、登录态校验、失效提示。
- 写操作分四档：点赞、投币、评论、私信主人，全部受限额与内容 filter 约束。
- 不新增主依赖：HTTP 用 `httpx`；二维码生成用工具私有依赖 `qrcode`。
- 不改 `pet/config.py` 与 system prompt 结构；配置读工具目录下的 `config.json`。

## §2 非目标

- 不发动态，不发弹幕，不参与他人评论区的讨论。
- 不关注、不取关任何人。
- 不做收藏夹管理：收藏需要选择收藏夹，留到后续阶段。
- 不做扫码以外的登录方式；不支持多账号同时保存。
- 不做本地语音转写；无字幕视频直接跳过。
- 不写 markdown 日报，不落 `out/` 目录。
- 不引入 `bilibili-api-python`：其接口为异步，工具 handler 为同步，需要额外的 event loop 包装。

## §3 账号与凭证

### §3.1 登录

登录入口是右键菜单「B 站账号」，打开 `pet/tools/bilibili/panel.py` 的面板。对话中提及登录时，模型只返回指引，不触发 UI。

二维码流程：

| 步骤 | 请求 | 说明 |
|---|---|---|
| 生成 | `GET passport.bilibili.com/x/passport-login/web/qrcode/generate` | 返回 `data.url` 与 `data.qrcode_key` |
| 渲染 | 本地生成 | `data.url` 转二维码 PNG，面板内显示，有效期 180 秒 |
| 轮询 | `GET passport.bilibili.com/x/passport-login/web/qrcode/poll` | 带 `qrcode_key`，每 2 秒一次 |

轮询返回码：

| `data.code` | 含义 | 面板行为 |
|---|---|---|
| 0 | 登录成功 | 从响应 `Set-Cookie` 取凭证，写入文件，刷新状态 |
| 86101 | 未扫码 | 继续轮询 |
| 86090 | 已扫码待确认 | 提示在手机端确认 |
| 86038 | 二维码失效 | 停止轮询，显示「刷新二维码」 |

### §3.2 凭证

| 项 | 约定 |
|---|---|
| 位置 | 用户配置目录下的 `bilibili_credential.json`（Windows 为 `%APPDATA%/KoishiAI/`），与 `settings.json` 同目录，不随源码目录覆盖而丢失；不提交 |
| 字段 | `sessdata`、`bili_jct`、`dedeuserid`、`buvid3`、`ac_time_value`、`updated_at`；字段名与 `bilibili_api` 的 Credential 对齐 |
| 写入 | 登录成功、轮询成功各写一次；文件权限尽力设为仅本人可读 |
| 读取 | 每次调用 `BiliClient` 时读取，不在内存缓存凭证 |
| 校验 | `GET x/web-interface/nav`，`data.isLogin` 为真且 `mid` 等于 `dedeuserid` |
| 失效 | 业务码 `-101` 视为未登录，返回「登录已失效，右键菜单重新扫码」 |
| 退出 | 面板提供「退出登录」，删除凭证文件 |

凭证为明文文件，能读到该文件即可使用该账号。这是本地桌面程序的取舍，不额外引入密钥环依赖。

### §3.3 身份

`bilibili__status` 返回当前账号昵称、UID、等级、硬币余额、粉丝数。恋恋在提示词里带上昵称与等级，避免把自己的身份说成观众。

## §4 只读接口

全部请求带桌面 Chrome 的 `User-Agent` 与 `Referer: https://www.bilibili.com/`。

| 用途 | 方法与路径 | 签名 | 关键返回 |
|---|---|---|---|
| 匿名指纹 | `GET /x/frontend/finger/spi` | 无 | `data.b_3` 作为 `buvid3` 写入 Cookie |
| wbi 密钥 | `GET /x/web-interface/nav` | 无 | `data.wbi_img.img_url`、`data.wbi_img.sub_url` |
| 搜索 | `GET /x/web-interface/wbi/search/type` | wbi | `data.result[]`：`bvid`、`title`、`author`、`play`、`pubdate`、`duration`、`description` |
| 视频信息 | `GET /x/web-interface/view` | 无 | `data.cid`、`title`、`owner.name`、`pubdate`、`duration`、`stat.view`、`desc` |
| 字幕索引 | `GET /x/player/wbi/v2` | wbi | `data.subtitle.subtitles[]`：`lan`、`lan_doc`、`subtitle_url` |
| 字幕正文 | `GET <subtitle_url>` | 无 | `body[].content` |

wbi 签名约定：

- `img_key`、`sub_key` 取 nav 返回 URL 的文件名主干。
- 两者拼接后按 64 项混入表重排，取前 32 位作为 `mixin_key`。
- 参数按 key 升序排列，值过滤 `!'()*` 后 urlencode，串联后追加 `mixin_key` 取 MD5，写入 `w_rid`；同时带秒级 `wts`。
- 密钥按自然日轮换，进程内缓存到当日 24 点失效。

失败与降级：

| 情形 | 判定 | 处理 |
|---|---|---|
| HTTP 非 2xx | 状态码 | 抛 `BiliError`，本轮该关键词跳过 |
| 业务码非 0 | `code` 字段 | `-412` 归为风控，`-101` 归为未登录，其余归为接口错误 |
| 字幕列表为空 | `subtitles` 为空数组 | 记为「无字幕」，不消耗 LLM 输入 |
| 字幕正文拉取失败 | 网络异常 | 该条记为「字幕拉取失败」，继续下一条 |

## §5 一轮流程

```
读取 config.json 与凭证
  → 按关键词逐个搜索，间隔 min_interval_s
  → 每词合并、按 bvid 去重、按 scan_days 过滤发布时间、剔除 bili_seen
  → 逐条取字幕加入候选，够 max_videos 或到采集预算就停止，不足则试下一个词
  → 有字幕的候选拼成一次 LLM 请求，得到 picks 与互动建议
  → 写 bili_seen（全部读过的）、bili_rounds
  → 按限额执行点赞、投币、评论、私信
  → 有 picks 时写 1 条 event 记忆
  → 返回结果给模型
```

| 步骤 | 规则 |
|---|---|
| 搜索关键词 | 一个或多个：`browse` 传了就用它，逗号分隔最多 3 个；留空时从 `keywords` 随机选一个 |
| 排序 | 默认 `totalrank`，可配 `pubdate` / `click` |
| 去重 | 同一 `bvid` 只保留首次出现 |
| 时间过滤 | `pubdate` 早于 `now - scan_days` 的丢弃 |
| 判重 | 未看过的排在前面，候选不足时补已看过的，不丢弃 |
| 分页 | 单关键词时按需翻页，最多 3 页；多关键词时每个只搜 1 页 |
| 换词 | 当前关键词凑不出新候选时，按随机顺序试下一个，最多 3 个 |
| 候选上限 | 有字幕的候选超过 `max_videos` 时按当前排序截断 |
| 无字幕 | 记录到 `skipped`，仍进候选，`subtitle` 为空串 |
| 失败重试 | 不重试；同一关键词失败两次即放弃 |
| 空结果 | 全部失败或无候选时返回原因，不调用 LLM |
| 写操作失败 | 不重试，记台账与原因；下一轮不再对同一目标重复发送 |

## §6 采集与返回

`bilibili__browse` 只做采集，不调用模型。

| 项 | 约定 |
|---|---|
| 关键词 | 本轮一个：参数传入优先，否则从 `keywords` 随机选一个 |
| 候选 | 搜索、时间范围、判重、无字幕过滤后，最多 `max_videos` 条 |
| 抽取 | 从候选里随机取 `per_run` 条，默认 2 条 |
| 返回 | `videos` 数组，每项含 `bvid`、`title`、`up`、`duration`、`play`、`desc`、`url`、`keyword`、`subtitle`；`keyword` 是这条命中的搜索词或请求里的 BV 号 |
| 字幕 | 每条截到 `subtitle_chars` 字，默认 2500；无字幕时为空串 |
| 封面 | `TOOL_CTX.has_vision()` 为真时，取随机结果第一条的封面，缩放后作为 `__image__` 附在工具结果之后；一次只能带一张 |
| 记忆 | 工具不写记忆，由演绎模型在对话里决定是否记录 |
| 写操作 | 不在 `browse` 内执行，由模型随后调用 `like` / `coin` / `comment` / `dm` |

`videos` 里不包含「为什么看」这类判断，判断由演绎模型在对话里完成。

## §7 写操作

`bilibili__browse` 只读，不执行任何写操作。是否点赞、投币、评论、私信，由调用它的模型在看完结果后自行决定，分别调用 `like` / `coin` / `comment` / `dm`。`allow_*` 开关是授权边界，由工具内部校验，代码不代替模型决定。

### §7.1 操作与限额

| 操作 | 接口 | 每日 | 目标 | 可否撤销 |
|---|---|---|---|---|
| 点赞 | `POST /x/web-interface/archive/like` | 不限 | 最近一轮候选 | 是，在 B 站端取消 |
| 投币 | `POST /x/web-interface/coin/add` | 2 | 最近一轮候选 | 否 |
| 评论 | `POST /x/v2/reply/add` | 7 | 最近一轮候选 | 可删除，但有通知痕迹 |
| 私信 | `POST api.vc.bilibili.com/web_im/v1/web_im/send_msg` | 不限 | 仅 `dm_uid` | 否 |

补充约定：

- 点赞与评论的目标必须是最近一轮 `browse` 看到的 `bvid`，进程内只保留最近一轮，30 分钟后失效。提示注入无法指定任意视频。
- 投币前检查硬币余额，低于 `coin_floor + 1` 时跳过；`multiply` 固定 1，`select_like` 固定 0，由点赞操作单独控制。
- 私信只发给配置里的 `dm_uid`；未配置时返回错误，不静默跳过。
- 该视频存在未回复的 @ 时，评论挂在 @ 那条评论下（`root` 与 `parent` 填它的 `rpid`），正文前缀 `@昵称 `，前缀不计入 filter 长度；发送成功后把该 @ 标为已回复。
- 点赞、投币每个目标 1 条有效记录；评论每个目标最多 3 条，由台账计数保证。私信目标恒定，不做目标去重，也不设每日上限。
- 每个写开关独立，默认全部关闭；刷视频自身由 `allow_browse` 控制，默认开启。
- 五个开关都在账号面板里，改动即时写回 `config.json`；关闭 `allow_browse` 时 `bilibili__browse` 直接返回原因，不发起请求。

### §7.2 评论内容 filter

代码侧硬规则，`pet/tools/bilibili/filters.py` 纯函数实现，任一不通过即拒绝发送。

| 规则 | 判定 | code |
|---|---|---|
| 长度 | 去空白后 6 到 `comment_max_chars` 字，默认 100 | `too_short` / `too_long` |
| 链接 | 含 `http://`、`https://`、`www.`、`b23.tv` | `url` |
| 提及 | 含 `@` 或「@某人」 | `mention` |
| 内部术语 | 命中内置词表：`prompt`、`token`、`配置`、`接口`、`插件`、`判重`、`一轮`、`上下文`、`sdk`、`api`、`缓存`、`日志`、`设置页`、`埋点` | `internal` |
| 隐私 | 盘符路径、`/Users/`、IP 字面量、邮箱、QQ 号 | `privacy` |
| 身份暴露 | 含「作为一个 AI」「我是 AI」「语言模型」「AI 助手」「聊天机器人」 | `identity` |
| 敏感类别 | 命中内置词表：政治、医疗建议、金融建议、法律建议 | `sensitive` |
| 索取行为 | 含「加群」「私信我」「关注我」「点赞我」「三连」「求关注」 | `solicit` |
| 空壳内容 | 去掉标点、空格与符号后没有剩余字符 | `empty` |
| 重复 | 与本轮及最近 20 条已发评论的字符 2-gram 相似度大于 0.8 | `duplicate` |

`filters.check_text` 返回 code，`describe()` 映射为中文文案；文案只出现在工具返回值与日志里，测试断言 code。

补充：

- 词表可由 `comment_blocklist` 追加，用户配置优先。
- 命中隐私或敏感类别时，除了拒绝，还要把该条记入日志，便于事后核查。
- filter 只做拒绝，不做改写；改写会引入「绕过过滤」的循环。
- 私信复用同一套规则，长度上限取 `dm_max_chars` 默认 400，链接一项放行：私信正文要带视频地址。

### §7.3 台账与幂等

写请求前先落台账，状态机：

```
pending → done（拿到远端 id 或业务码 0），同时记录发评论用的 root
pending → failed（业务码非 0，确定没发出去，可重试）
pending → unknown（业务码 0 但回执缺 rpid / msg_key，可能已发出，不再重试）
done → visible / hidden / unverified（复核结果）
unverified → visible / hidden（下一轮复核）
```

| 场景 | 处理 |
|---|---|
| 有效记录达到上限 | 评论 3 条、其余 1 条，超过时返回「有效记录已达上限」 |
| 上一次记录为 `failed` | 允许重新占位 |
| 请求超时 | 记 `unknown`，占住目标不再重试，等复核或人工收敛 |
| 业务码非 0 | 记 `failed`，返回原因，不重试 |
| 回执缺 `rpid` / `msg_key` | 可能已发出，记 `unknown`，占住目标不再重试 |
| 工具线程被超时中断 | 线程继续执行，成功后仍会更新台账 |

超时语义来自 `pet/tools/executor.py`：handler 超时后守护线程继续运行，模型收到的是「工具执行超时」。台账是唯一能判断「到底发没发出去」的依据，因此写操作必须先写台账再发请求。

### §7.4 复核

| 项 | 约定 |
|---|---|
| 目的 | 确认评论是否公开可见，收敛 `pending`、`done` 与 `unverified` |
| 查询 | 顶层用 `reply/main`（`mode=2` 按时间）；有 `@` 回复时用 `reply/reply` 按台账记录的 `root` 拉楼中楼 |
| 触发 | `bilibili__verify` 手动触发 |
| 命中判定 | 按 `rpid` 精确匹配，且 `member.mid` 必须是本人；未命中时退回正文前 20 字匹配 |
| `invisible` | 命中但接口标了 `invisible`，记 `hidden`，不重发 |
| `state` | 非 0 视为审核或折叠，保持 `done`，下一轮再核 |
| 未命中 | 记 `unverified`，下一轮再核；看不到不等于没发出，不重发 |
| 私信 | 不参与复核，接口无公开可查状态 |

## §8 数据落点

使用 `TOOL_CTX.db_path()` 指向的同一个 `pet.db`。

```sql
CREATE TABLE IF NOT EXISTS bili_seen (
    bvid TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    up TEXT NOT NULL DEFAULT '',
    pubdate INTEGER NOT NULL DEFAULT 0,
    verdict TEXT NOT NULL DEFAULT 'skipped',
    note TEXT NOT NULL DEFAULT '',
    seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bili_rounds (
    day TEXT NOT NULL,
    uid INTEGER NOT NULL DEFAULT 0,
    picked INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 0,
    last_run TEXT NOT NULL,
    PRIMARY KEY (day, uid)
);

CREATE TABLE IF NOT EXISTS bili_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid INTEGER NOT NULL,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    remote_id TEXT NOT NULL DEFAULT '',
    root INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bili_actions_target
    ON bili_actions(uid, kind, target);

CREATE TABLE IF NOT EXISTS bili_mentions (
    msg_id INTEGER PRIMARY KEY,
    uid INTEGER NOT NULL DEFAULT 0,
    bvid TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    rpid INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    handled INTEGER NOT NULL DEFAULT 0,
    seen_at TEXT NOT NULL
);
```

| 表 | 用途 | 写入时机 |
|---|---|---|
| `bili_seen` | 判重，避免重复读同一条字幕 | 每轮结束，写入本轮读过的全部候选 |
| `bili_rounds` | 每日挑中数与请求数，按 `(day, uid)` 累加 | 每轮结束 |
| `bili_actions` | 写操作台账与幂等依据 | 每次写操作前 |
| `bili_mentions` | @ 台账：未回复的每轮重新带出，回复后置 `handled` 不再出现 | 每次拉取 @ 时 |

保留策略：`bili_seen` 与 `bili_actions` 保留 90 天，工具注册时清理更早的行；`bili_mentions` 长期保留，作为不重复回复的依据。

记忆写入：

| 项 | 取值 |
|---|---|
| 时机 | 本轮有 `picks` 时写 1 条；0 条不写 |
| `category` | `event` |
| `content` | `我在B站看了《标题》（UP主）：<line>`；多部时合并为一段，总数不超过 300 字 |
| `keywords` | UP 名加标题中的关键词，最多 5 个 |
| `importance` | 3 |
| `level` | `L2` |

不逐部写入：连续运行 30 天会产生上百条 event 记忆，挤占 `MEMORY_MAX_CAPACITY`。

## §9 工具接口

| 方法 | 参数 | 超时 | 返回 |
|---|---|---|---|
| `bilibili__browse` | `keyword`，可选：关键词或 BV 号 | 300s | `summary`、`picked`、`skipped`、`note`、`requests` |
| `bilibili__like` | `bvid` | 30s | 操作结果 |
| `bilibili__coin` | `bvid` | 30s | 操作结果与余额 |
| `bilibili__comment` | `bvid`、`text` | 30s | 操作结果、`rpid`、拒绝原因 |
| `bilibili__dm` | `text` | 30s | 操作结果 |
| `bilibili__verify` | `limit` | 60s | 复核结果 |
| `bilibili__status` | 无 | 20s | 账号信息、今日计数、登录态、最近一次运行 |

- 工具名 `bilibili`，`TOOL_GROUP = "info"`，与 `weather`、`system_monitor` 同组，需经 `tool_search` 激活。
- 返回结构统一带 `summary` 一行，供模型改写成人格口吻。
- 只在有 `picks` 时调用 `TOOL_CTX.note_event("bili", ...)`。
- 不在工具内调用 `speech`：对话场景中模型本来就要说话，再从工具侧播报会重复。
- 写操作方法始终注册，执行时检查开关；关闭时返回明确原因，便于模型告知主人。
- 失败返回统一为 `ok: false` 加 `reason` code，文案放 `summary`。code 取值：`not_logged_in`、`disabled`、`target_unknown`、`dm_uid_missing`、`quota`、`insufficient_coins`、`filtered`、`duplicate`、`send_failed`、`result_unknown`。

## §10 配置项

工具私有 `config.json`，由 `config.example.json` 首次加载时复制生成，不提交。

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `keywords` | list[str] | 见下 | 关注关键词；BV 号形式直接读取该视频，不经过搜索 |
| `max_videos` | int | 6 | 单轮最多读几条字幕 |
| `per_run` | int | 2 | 单轮随机取几条返回 |
| `scan_days` | int | 30 | 只看最近几天的视频 |
| `order` | str | `totalrank` | `totalrank` / `pubdate` / `click` |
| `subtitle_chars` | int | 2500 | 每条字幕截断字数 |
| `min_interval_s` | float | 3 | 两次请求的最小间隔 |
| `timeout_s` | float | 15 | 单次 HTTP 超时 |
| `allow_browse` | bool | true | 刷视频总开关 |
| `allow_like` | bool | false | 允许点赞 |
| `allow_coin` | bool | false | 允许投币 |
| `allow_comment` | bool | false | 允许评论 |
| `allow_dm` | bool | false | 允许私信 |
| `coin_per_day` | int | 2 | 每日投币上限 |
| `coin_floor` | int | 1 | 硬币余额下限 |
| `comment_per_day` | int | 7 | 每日评论上限 |
| `comment_max_chars` | int | 100 | 评论长度上限 |
| `comment_blocklist` | list[str] | 空 | 追加的禁用词 |
| `dm_uid` | int | 0 | 私信收件人 UID，0 表示不发 |
| `schedule_enabled` | bool | false | 定时提醒总开关 |
| `schedule_interval_minutes` | int | 180 | 两次提醒的最小间隔，最小 10 |
| `schedule_active_hours` | str | `09:00-23:00` | 允许触发的作息窗口 |
| `schedule_backoff_base_minutes` | int | 5 | 状态不可用时的退避起点 |
| `schedule_pause_hours` | str | 空 | 禁用时段，逗号分隔多个区间；非空且格式正确时生效 |
| `schedule_pause_weekdays_only` | bool | true | 禁用时段只作用于工作日 |
| `mention_enabled` | bool | false | 每次提醒前同步主人的 @；评论可能被 B 站折叠，默认关闭 |
| `mention_per_day` | int | 3 | 每轮带出的未回复 @ 上限 |
| `dm_max_chars` | int | 400 | 私信长度上限 |

默认关键词：`东方Project`、`古明地觉`、`古明地恋`、`猫`、`狗`、`动物`。

`keywords` 在账号面板里编辑，按中英文逗号或换行切分，去重后最多 20 个；留空回退默认列表。

`dm_uid` 也在账号面板填写，非数字或 0 拒绝保存；配置为 0 时私信调用与一轮内的私信步骤都返回明确错误。

关键词清洗：去不可打印字符、去空、去重，单条上限 50 字，最多 20 个。`browse` 的 `keyword` 参数走同一套清洗，但不受配置列表限制；BV 号固定 12 位，不受长度限制。传了 BV 号就只取这些视频、不再搜索关键词，且抽选时这些 BV 必定返回。

配置读取失败时用内置默认值并在 `summary` 里注明，不中断本轮。

## §11 成本与限速

| 项 | 上界 |
|---|---|
| HTTP 请求 / 轮 | whoami 1 + 搜索 1 至 3 + 候选 6 × 2 = 14 至 16；字幕与封面走 CDN，不计入接口间隔 |
| 耗时 / 轮 | 14 至 16 次 × 3s 约 42 至 48s；采集另有 90s 预算，工具超时取 300s |
| LLM 调用 / 轮 | 0，判断与发言由演绎模型在主对话完成 |
| 输入 token / 轮 | 返回 2 条字幕，约 2.5k tokens |
| 输出 token / 轮 | 0 |
| 磁盘 / 轮 | 1 条记忆、不超过 6 行 `bili_seen`、1 行 `bili_rounds`、写操作各 1 行台账 |

限速为单线程串行请求：`BiliClient` 内部锁加最近一次请求时间，不足 `min_interval_s` 时 sleep。写操作同样排队，不并发。

## §12 验证

单元测试，不联网，`tests/test_bili.py`：

| 用例 | 断言 |
|---|---|
| wbi 签名 | 固定 `img_key`、`sub_key`、`params`、`wts` 时 `w_rid` 与基准值一致；参数顺序不影响结果 |
| 值过滤 | 参数值含 `!'()*` 时先过滤再签名 |
| 搜索解析 | 带 `<em class="keyword">` 的标题被清洗；缺字段用空值兜底 |
| 字幕选择 | `ai-zh` 优先于 `zh-CN`，其次 `zh`，最后取第一条 |
| 时间过滤 | 超出 `scan_days` 的候选被丢弃；`pubdate` 为 0 时保留 |
| picks 解析 | 非法 bvid、重复 bvid、超条数、代码块包裹、解析失败五种输入各有确定结果 |
| 评论 filter | 每条硬规则各一个正例与反例 |
| filter 词表 | `comment_blocklist` 命中即拒绝 |
| 重复检测 | 与最近评论相似度超过阈值时拒绝 |
| 台账唯一 | 同一 `uid`、`kind`、`target` 的有效记录只能有一条 |
| 台账状态机 | 有效记录存在时拒绝重发；上一条为 `failed` 时允许重新占位 |
| 请求超时 | 记 `unknown`，同一目标不再重试 |
| 限额 | 单轮与每日上限各自生效，跨日重置 |
| 目标校验 | 不在候选集内的 `bvid` 被拒绝 |
| 开关关闭 | 写操作返回明确原因，不产生台账记录 |
| 空字幕 | 候选被记为 skipped，不进入 LLM 输入 |
| 私信放行链接 | `allow_urls` 为真时链接不拦截，评论仍拦截 |
| 凭证往返 | 保存后读回字段一致；缺少 `bili_jct` 时视为未登录 |

手工核对：

1. 右键菜单打开「B 站账号」，扫码登录，`bilibili__status` 返回昵称与 UID。
2. 对话中说「看看 B 站最近有什么」，观察日志中的请求条数与耗时，确认未触发 `-412`。
3. 查询 `SELECT bvid, verdict, note FROM bili_seen ORDER BY seen_at DESC LIMIT 10;`。
4. 查询 `SELECT kind, target, status, reason FROM bili_actions ORDER BY id DESC LIMIT 10;`。
5. 在 B 站网页端确认评论内容与点赞、投币状态。
6. 在记忆面板确认新增一条 `event` 记忆。
7. 删除凭证后重复步骤 1，确认提示为「登录已失效」。

## §13 风险

| 项 | 影响 | 处理 |
|---|---|---|
| 风控 `-412` | 搜索、互动或评论全部失败 | 串行 3s 间隔、单轮与每日限额、失败不重试、返回明确原因 |
| 账号被限制 | 独立账号发言功能受限 | 默认全关、评论每日 3 条、同一视频一次、内容 filter |
| 提示注入 | 账号在任意视频下发出异常内容 | 写操作目标限定候选集；字幕固定为素材；评论过滤内部术语与链接 |
| 凭证泄露 | 本机文件可被其他程序读取 | 凭证与配置分离、面板可一键退出登录、文档写明明文存储 |
| 超时重复发送 | 同一视频两条评论 | 台账唯一索引、pending 10 分钟拒绝重发、复核收敛 |
| filter 误杀 | 正常评论被拒 | 拒绝原因返回模型与日志；词表可由配置追加，不可删除内置规则 |
| 评论未公开 | 台账 `done` 但实际仅自己可见 | `bilibili__verify` 复核，标记 `hidden`，不重发 |
| 接口变更 | wbi 签名或字段变化导致全量失败 | 字段解析容错、失败降级为原因文本；接口集中在 `core.py` |
| 记忆膨胀 | event 记忆挤出其他内容 | 每轮最多 1 条，`importance` 固定 3 |
| 长耗时阻塞 | 300s 内未返回，执行器超时后线程仍在后台 | 请求间隔、候选上限与 90s 采集预算共同约束上界；字幕与封面走 CDN 不占接口间隔 |

## §14 阶段

| 阶段 | 内容 | 前置 |
|---|---|---|
| 1 | 凭证与登录面板、`status`、只读一轮、记忆 | 无 |
| 2 | 点赞、投币、台账 | 阶段 1 |
| 3 | 评论、filter、`verify` 手动复核 | 阶段 2 |
| 4 | 私信主人 | 阶段 3 |
| 5 | 定时提醒、发送后自动复核 | 阶段 4 |

定时规则：

| 项 | 约定 |
|---|---|
| 频率 | `schedule_interval_minutes`，最小 10，低于按 10 处理 |
| 窗口 | `schedule_active_hours` 默认 09:00-23:00，窗口外顺延到下一个窗口开始，不计退避 |
| 状态守卫 | `TOOL_CTX.is_idle()` 为假时指数退避，`5 × 2^n` 分钟，上限 60，触发成功后清零 |
| 重启 | 从当前时间加一个间隔重新排，不补跑 |
| 记录 | 不落表，不判断今天是否跑过，只用频率控制 |
| 触发 | `request_interact(schedule.md, cooldown_ms=0, thinking=True, enable_tools=True)`；提示词要求这一轮先用 `browse` 挑出推荐视频，再用 `dm` 把 BV 号发给主人；被 @ 时改用 `comment` 在视频下回复，不走私信；点赞、投币可选 |
| 手动触发 | 账号面板的「立即触发一次」直接发起一次提醒，不检查 `schedule_enabled`、作息窗口与禁用时段，也不改动下一次闹钟 |
| @ 检查 | `mention_enabled` 为真时，提醒前在 daemon 线程拉 `GET api.bilibili.com/x/msgfeed/at`（只看 `dm_uid`），新条目落 `bili_mentions`；未回复的每轮都带进提示词，回复成功后置 `handled`，`mention_per_day` 是每轮带出的条数上限 |
| 禁用时段 | `schedule_pause_hours` 非空且解析成功时，落在任一段内的触发点顺延到这些段的并集结束时刻；段可跨零点（`22:00-08:00`）；默认只作用于工作日，周末不受限 |

@ 的处理不做事件驱动：只在每次提醒前顺带拉一次，新 @ 落表后与历史未回复的一起带出，提示词把 @ 处理排在必做任务之前；模型用 `bilibili__browse` 看过那个视频后，用 `bilibili__comment` 在视频下回复，成功后该条 @ 不再出现在后续提示词里。每轮带出的候选 @ 记一行日志。`dm_uid` 为 0 时不处理任何 @。

只回复 `dm_uid` 的 @。回复时用通知里的 `rpid` 挂到该评论下，正文前缀 `@昵称 `；`rpid` 为 0 时退化为视频下的顶级评论。

待定项：

- 是否在 `pet/config.py` 增加 `BILI_ENABLED` 开关，让设置页可见。当前依赖 `TOOLS_ENABLED`，设置页没有独立入口。
- 收藏夹管理是否纳入：需要收藏夹选择与 fid 存储，与发言类操作不同，可单独成阶段。
