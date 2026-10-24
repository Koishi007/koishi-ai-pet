# 会话事件日志与消息投影设计

> 会话历史是一条只追加的事件日志（Session Event Log），作为唯一事实来源；每轮发给模型的 `Message[]` 只是从日志投影（Projection）出的视图。工具调用的记录形态、模型能力适配、条数与 token 裁剪、摘要覆盖，全部是投影策略，不再改写事实。
>
> 本文描述最终形态，不含过渡方案与灰度开关。修订：2026-10-22 初稿；同日按两轮评审收敛（assistant_turn 时序、native 参数回放、失败事件、裁剪边界、崩溃窗口、Profile 字段收敛），并放弃历史迁移，改为带发布说明的破坏性切换；同日再收敛工具通道（native 首选、错误反馈降档、传输编码层预留）。

## §0 现状与问题

### §0.1 存储与投影

| 项 | 现状 | 位置 |
|---|---|---|
| 持久化表 | `context_entries(id, role, content, timestamp, is_summary)` | `pet/brain/base.py` 的 `_init_db` |
| 保存 | 每次全量 `DELETE` 后重新 `INSERT` 全部条目 | `_do_save` |
| 摘要去重 | 命中相似度阈值时**就地替换**旧条目 | `add_context` |
| 淘汰 | 超限条目丢弃原文，只把 `[{role}] {content}` 推入 `_pending_summary_queue` | `_evict_context` |
| 投影 | `get_multi_turn_messages` 同时承担条数裁剪、token 预算、角色映射、时间前缀 | `get_multi_turn_messages` |
| system 合并 | 历史 system 折叠进主 prompt 的 `[上下文备注]` | `pet/brain/context_builder.py` 的 `_merge_system_history` |

"发生过什么"和"这轮让模型看到什么"共用同一份可变列表，任何裁剪、替换、合并都直接改事实。

### §0.2 工具调用的记录形态

| 层 | 现状 | 位置 |
|---|---|---|
| 当轮协议 | `assistant.tool_calls` + `role="tool"`（协议正确） | `pet/brain/tool_loop.py` 的 `current_messages.append(...)` |
| 跨请求回填 | 折叠成一条 assistant 文本：`[工具调用] （自言自语：…）工具名 → 摘要` | `tool_loop.py` 的 `log_entry`、`session.add_context` |
| 结果兜底 | `result.context_brief or tool_brief or result_text[:200]`，最后一档是 JSON | `tool_loop.py` |
| 识别方式 | `content.startswith("[工具调用]")` | `base.py` 的 `_score_entry`、`_evict_context` |
| 失败兜底 | `parse_behavior(content)` 默认 `action_fallback=True`，补 `sit 5s` | `tool_loop.py` 两处 |

### §0.3 一次真实故障（2026-10-22 09:44）

刷 B 站流程第三轮，模型未走 `tool_calls` 通道，正文输出：

```
[09:43] [工具调用] （自言自语：小狗好可爱。。。给它比个心）bilibili__like → {"bvid": "BV1aPJR6oExx", "aside": "..."}
```

`finish_reason=stop`，解析不出字段；`parse_behavior` 补了 `sit 5s`，外层 `retry_if_empty` 因"有动作"未触发修复轮，点赞没有执行。该正文与历史中由 harness 写入的 `[工具调用] …` 行几乎逐字同构，是事实与视图混淆的直接后果。

## §1 目标

- 会话历史成为只追加的事件日志，不提供单条 UPDATE/DELETE；整段回收只发生在摘要覆盖（`purge_covered`）与清空上下文（`purge_sessions`）。
- `Message[]` 由纯函数投影产生：同一份日志可按不同模型能力投影出不同视图。
- 工具调用以事件组存储，投影时按组处理，不产生孤立的 `tool` 消息。
- 模型能力差异（是否支持 `tool` 角色、是否支持多条 system）只影响投影，不影响存储。
- 摘要、裁剪、降级都不销毁事实，可回放、可审计。

## §2 非目标

- 不改当轮工具循环的协议（`assistant.tool_calls` + `role="tool"` 已正确）。
- 不引入外部消息队列或通用事件溯源框架，仍使用现有 `pet.db`。
- 不做跨会话历史合并。
- 不把工具的原始 JSON / 长文本结果写入事件正文。
- 不改记忆库（`memories`）的存储模型。

## §3 事件模型

### §3.1 表结构与会话

```sql
CREATE TABLE IF NOT EXISTS session_events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    ts         REAL NOT NULL,
    type       TEXT NOT NULL,
    group_id   TEXT,                        -- 工具组 / 轮次组；可空
    payload    TEXT NOT NULL,               -- JSON
    refs       TEXT NOT NULL DEFAULT '{}'   -- JSON，group_id 不放这里
);
CREATE INDEX IF NOT EXISTS idx_session_events_session_seq
    ON session_events(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_session_events_group
    ON session_events(session_id, group_id);

CREATE TABLE IF NOT EXISTS session_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS session_artifacts (
    artifact_id TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    mime        TEXT NOT NULL DEFAULT 'text/plain',
    size        INTEGER NOT NULL DEFAULT 0,
    body        TEXT NOT NULL
);
```

`group_id` 提为独立列而不是塞进 JSON：投影要按组聚合，表达式索引在部分 SQLite 发行版上不可用（JSON1 未编译）。

**会话边界**：同一时刻只有一个活动会话，`session_id` 是 UUID，存于 `session_meta('current_session')`。应用重启不换会话，保持"上下文跨重启保留"的行为。`clear_context` 生成新 UUID 并删除其他会话的事件与不再被引用的 artifact：清空是用户可见的删除动作，磁盘随之释放。

`refs` 约定：

| 键 | 用途 |
|---|---|
| `tool_call_id` | OpenAI 协议的调用 id，投影时原样复用 |
| `parent_seq` | 逻辑归属（如 tool_result 指向对应 tool_call） |
| `covers` | `[start_seq, end_seq]`，仅 `summary` / `snapshot` 使用 |
| `supersedes` | 指向被本条修正或替代的事件 seq |

### §3.2 事件类型

| type | payload | 参与投影 | 说明 |
|---|---|---|---|
| `user_msg` | `{text}` | 是 | 用户消息 |
| `assistant_turn` | `{v, text?, summary?, emotion?, speech_parts?, actions?, memory?, mood?, vitals?}` | 是 | 一次**有效**模型响应的结构化记录；`v=1` 时 `text` 为原文（≤4KB，仅审计），投影只用其余字段 |
| `assistant_speech` | `{text}` | 条件 | 工具 `arguments.aside` 产生的台词；投影时并入组内 assistant `content` |
| `tool_call` | `{name, args_digest, side_effect}` | 是 | 一次工具调用；不含原始参数；`side_effect` 由工具注册表声明 |
| `tool_result` | `{ok, brief, artifact_id?}` | 是 | 结果摘要；大结果落 artifacts |
| `failed_turn` | `{text, finish_reason, tag, reason}` | 否 | 解析失败的一次响应；只审计，不进入任何视图 |
| `system_note` | `{text}` | 是 | 系统事实（离开提示、定时提醒等） |
| `summary` | `{text}` | 是 | 上下文压缩；`refs.covers` 标记覆盖区间 |
| `snapshot` | `{state}` | 条件 | 折叠事件，见 §6.3 |

### §3.3 写入规则

1. 只 `INSERT`；不提供 UPDATE / DELETE 接口。
2. 修正用补偿事件：结果被复核推翻时追加新的 `tool_result`，`refs.supersedes` 指向旧 seq。
3. **时序**：最终响应由产出方写一条结构化 `assistant_turn`（`v=1`）；工具轮只写 `tool_call` 与执行后的 `tool_result`（同一 `group_id`），中间响应的 Summary / Speech 与最终输出重复，不入日志。
4. `assistant_speech` 记录工具 `arguments.aside` 产生的台词，投影时并入组内 assistant `content`。
5. `args_digest` 只保留参数要点，单条上限 120 字符；禁止写入完整 JSON。
6. `tool_result.brief` 必须是人类可读短句；原文超过 `ARTIFACT_INLINE_LIMIT`（默认 2048 字符）才落 `session_artifacts`，否则只存 brief，不建 artifact。
7. `summary.covers` 允许相邻，不允许重叠；重叠视为对旧 summary 的 supersede。
8. 解析失败的响应写 `failed_turn`，**不写** `assistant_turn`，因此不会进入下一轮视图。
9. `assistant_turn` 的结构化字段在落库前按白名单校验：`emotion` 只保留协议列出的名字，`actions` 名字必须在动作表里，`mood` / `vitals` 只保留白名单键与数值；非法值丢弃，原文保留在 `text` 审计副本。

### §3.4 不变量

- `seq` 单调递增，同一 `session_id` 内事件顺序即事实顺序。
- 每个 `tool_call` 在投影时刻必须能找到一个 `tool_result`；找不到即视为不完整组（见 §5.3）。
- 事件一经写入不可变，日志重放结果只取决于 `(session_id, seq, profile, budget)`。
- 被 `supersedes` 指向的事件不参与投影；其兄弟事件（同组）保留。

## §4 投影器

### §4.1 接口与纯函数边界

```python
def project(events: list[Event], profile: Profile, budget: Budget) -> list[dict]:
    """只依赖入参：不读时钟、不读随机、不读全局配置、不读主 prompt。"""
```

`Budget`：

| 字段 | 说明 |
|---|---|
| `max_entries` | 对话类条目上限 |
| `max_tokens` | 总 token 预算 |
| `max_summaries` | 摘要事件条数上限，独立于 `max_entries` |
| `evict_before_ts` | 半衰期淘汰边界，由调用方用 `now - CONTEXT_HALF_LIFE_S` 算好传入 |
| `now` | 投影时刻，决定相对时间标签；为 0 时退化为时钟 |

时间相关规则：

- **相对时间由事件 `ts` 与 `Budget.now` 共同决定**：`刚刚` / `N 分钟前` / `N 小时前` / `昨天 22:10`。重放要固定 `now`，同一份事件在不同 `now` 下渲染不同。
- 时间只出现在 `user_msg` / `system_note` / `summary` 上；`assistant_turn` 渲染出的字段行与 `Speech:` 行不带时间，模型照抄历史行也不会把时间念出来。模型自造的绝对时间标签（如 `〔11:18〕`）按时间样式剥掉，不进入历史。
- `evict_before_ts` 只用于**读取前过滤**，不进入投影器内部的判断。
- 缓存键：`(session_id, last_seq, profile.name, max_entries, max_tokens, evict_before_ts, now)`；两处时间都按分钟取整，跨过分钟或半衰期边界才失效。

### §4.2 Profile 与能力判定

| 字段 | 说明 | 取值来源 |
|---|---|---|
| `tool_channel` | 工具记录的传输通道：`native` / `user-note` / `system-note` / `none` | 配置 + 运行时降级 |
| `supports_image` | 是否允许工具结果附图 | 现有 `TOOL_CTX.has_vision()` |
| `name` | 预设名，用于日志与缓存键 | 见下表 |

`multi_system` 不在 Profile 中：投影器不负责 system 合并，是否允许多条 system 只影响组装阶段，由 `context_builder` 判断（当前恒为 false，统一走 `_merge_system_history`）。

| 预设 | tool_channel | 工具记录形态 |
|---|---|---|
| `native` | `native` | `assistant.tool_calls` + `role="tool"` |
| `user-note` | `user-note` | assistant 正文 + 随后一条 user，前缀 `（环境记录）` |
| `system-note` | `system-note` | 一条 system，进 `[上下文备注]` |
| `minimal` | `none` | 不投影工具记录，只保留对话 |

`tool_channel` 与 system 条数无关。历史中多条 system 引发 500（qwen3/ollama 模板）是组装阶段的问题，由 `_merge_system_history` 处理，不参与本档位判定。

`LLM_TOOL_ROLE_MODE=auto` 的判定（细节见 §10.1）：

1. 命中用户覆盖 `LLM_TOOL_ROLE_OVERRIDE` → 用指定预设；
2. 命中 `_TOOL_CHANNEL_HINTS` → 用提示档位，跳过首次尝试；
3. 默认 `native`，直接按协议发送；
4. 运行时 400/422 且错误文本命中 `tool` / `tool_call_id` / `role` / `function` → 同一次调用内降一档重试一次；单次调用最多降一档；
5. 降级结果只写进程内缓存，不回写配置；重启后重新判定。

### §4.3 投影步骤

1. 读取当前会话 `seq > snapshot.covered_until` 的事件。
2. 调用方已按 `evict_before_ts` 过滤掉过期工具组。
3. 剔除被 `supersedes` 指向的事件；被 supersede 的 summary 连同其 `covers` 整段失效。
4. 跳过落在有效 `summary.covers` 区间内的事件；`snapshot` 优先于 `summary`。
5. 事件 → 逻辑消息：
   - `user_msg` / `system_note` / `summary` 渲染成 `[相对时间] 正文`；
   - `assistant_turn` 按字段渲染：Summary → Emotion → Speech → Action → Memory → Mood → Vitals，顺序固定、不加时间标签；
   - `assistant_speech` 渲染成 `Speech: 正文`；
   - `tool_call` + `tool_result` 按 `group_id` 聚合为 `ToolExchange`；
   - `failed_turn` 直接丢弃。
6. `ToolExchange` 按 profile 映射（§4.5）。
7. 预算裁剪（§4.4），以组为原子单位。
8. 输出逻辑消息列表，**不做 system 合并**。合并由 `context_builder` 在组装阶段完成（§4.6）。

### §4.4 裁剪规则

- 原子组：同组 `tool_call` / `tool_result` 视为一组，不可拆。
- 超 `max_entries` 或 `max_tokens` 时，从最旧的组开始整组丢弃。
- 工具组另受半衰期约束：早于 `evict_before_ts` 的组在读取阶段就被过滤，不进入投影。
- `summary` 不参与 token 裁剪，只受 `max_summaries` 约束；`max_summaries` 默认取 `max(2, int(max_entries * 0.2))`，且摘要**不占用** `max_entries` 配额。
- `system_note` 与 `user_msg` 参与 token 预算，优先级高于工具组。

### §4.5 工具组映射

`ToolExchange` 的聚合与输出：

```
for group in groups(按首个 seq 升序):
    turn   = group.assistant_turn
    calls  = group.tool_calls            # 按 seq 升序
    result = {r.tool_call_id: r for r in group.tool_results}

    if 任一 call 缺 result:
        emit_note(group)                 # 整组降级为文本，绝不发半对
        continue

    if profile.tool_channel == "native":
        out.append({
            "role": "assistant",
            "content": join_non_empty(turn.text, group.aside_rendered) or None,
            "tool_calls": [
                {"id": c.tool_call_id, "type": "function",
                 "function": {"name": c.name, "arguments": replay_args(c)}}
                for c in calls
            ],
        })
        for c in calls:
            out.append({
                "role": "tool",
                "tool_call_id": c.tool_call_id,
                "content": result[c.tool_call_id].brief,
            })

    elif profile.tool_channel == "user-note":
        if turn.text:
            out.append({"role": "assistant", "content": turn.text})   # 不带 tool_calls
        out.append({"role": "user",
                    "content": "（环境记录）\n" + join_briefs(calls, result)})

    elif profile.tool_channel == "system-note":
        if turn.text:
            out.append({"role": "assistant", "content": turn.text})
        out.append({"role": "system",
                    "content": "（环境记录）\n" + join_briefs(calls, result)})

    else:                                                # minimal
        if turn.text:
            out.append({"role": "assistant", "content": turn.text})
```

要点：

- `content` 与 `tool_calls` 是**同一条** assistant 消息的并列字段；`content` 为空时置 `null`（OpenAI 允许）。
- 一轮多个工具：一条 assistant 的 `tool_calls` 数组包含多项，随后按 `tool_call_id` 顺序输出多条 `tool`。
- `aside` 在 native 档并入 `content`（有正文时以换行追加），避免模型下一轮"忘记自己说过什么"；它不单独占一条消息，否则会破坏 `tool_calls` 后必须紧跟 tool 消息的顺序约束。
- 并入 `content` 的 aside 与独立 `assistant_speech` 同形，都带 `Speech: ` 前缀：同一语义只有一种历史形态，不给模型裸文本＝正文的示范。降级档同样使用带前缀的文本。
- `user-note` 档**不输出** `tool_calls`：只带 `tool_calls` 而不跟 `role="tool"` 的请求会被严格端点判定为缺配对，降级档必须自称完整。
- 三个降级档在 `turn.text` 为空时省略该条 assistant，避免产出空 `content` 消息被兼容端点拒绝。
- `user-note` 优于 `system-note` 的原因只在位置：user 消息留在对话流的时序位置上，system 会全部挤进 `[上下文备注]`，离发生点远、模型更易忽略。两者文案一致。
- `replay_args` 三级回退：

| 档位 | 行为 |
|---|---|
| `minimal`（默认） | 按 `args_digest` 重建顶层标量 JSON；无法还原的嵌套字段省略 |
| `empty` | `"{}"` |
| 端点拒绝 | 本次调用降一档 `tool_channel`，重试一次 |

配置项 `NATIVE_REPLAY_ARGS = minimal | empty`。选 `minimal` 是因为部分兼容端点（vLLM、Ollama、部分网关）会按 function schema 校验必填字段，`"{}"` 有 400 风险。

`tool.content` 使用 `brief`（短句），不是原始结果全文。这是有意取舍：当轮模型看到全文，历史里只保留结论；需要细节时用 `read_artifact`（后续项）或重新调用工具。数据库里 `artifact_id` 保留可追溯性。

统一文案规则（四档共用）：

- 成功：`刷了 B 站，看了《小狗: 不养了吗…》`
- 失败：`点赞没有成功：目标不在最近一轮候选里`
- 禁止：工具内部名、`{...}`、`→`、`[工具调用]`、`（自言自语：`
- 一轮多个工具合成一个 `ToolExchange`，多行列出。

### §4.6 投影与组装的边界

- 投影器是**唯一**的条数与 token 裁剪入口。组装阶段不再调用旧的 `get_multi_turn_messages` 裁剪，否则二次裁剪会按单条切组，产生孤立 `tool` 消息。
- system 合并仍在 `_merge_system_history`（组装阶段），投影器只负责产出独立的 system 消息。
- 组装顺序固定：主 prompt → `[上下文备注]`（含 system_note / summary / `system-note` 档的工具记录）→ 对话消息 → 本轮 user 消息。

### §4.7 assistant 文本不做改写

assistant 行只来自结构化字段，按协议顺序逐字渲染；投影不对正文做清洗或加前缀。
未带标签的散文与伪调用行不进字段，只留在事件的 `text` 审计副本里。

### §4.8 传输编码层

投影产出的是协议无关的逻辑消息，落到 wire 格式由编码器负责：

```python
class MessageEncoder(Protocol):
    name: str
    def encode(self, messages: list[dict], tools: list | None) -> dict:
        """把逻辑消息编码成端点请求体。"""
```

| 编码器 | 状态 | 说明 |
|---|---|---|
| `OpenAIChatEncoder` | 现有唯一实现 | `chat.completions.create`，`role="tool"` + `tool_calls` |
| `AnthropicMessagesEncoder` | 预留，引入原生 SDK 时实现 | 见下表约束 |
| `GeminiEncoder` | 预留，引入原生 SDK 时实现 | 见下表约束 |

当前所有请求（含 `LLM_URL` 指向的第三方网关）都是 OpenAI 格式，因此本层现在没有分支。`tool_channel` 是**同一个 OpenAI 协议内的**形态选择，不是编码器切换；两者不可混用。

接入原生通道时需要处理的硬约束：

| 项 | Anthropic Messages | Gemini generateContent |
|---|---|---|
| 工具调用 | `assistant.tool_use` block | `model.functionCall` |
| 结果承载 | `user.tool_result` block，`tool_use_id` 必须匹配 | `user.functionResponse`，按 `name` 而非 id |
| 顺序 | `tool_use` 后必须紧跟 `tool_result`，中间不可插入其他消息 | 同轮可交错 |
| system | 顶层单条 `system` 字段 | `systemInstruction` 单条 |
| 空正文 | `content` 可为空数组 | `parts` 不可为空 |

投影器对此的义务只有两条：`tool_call_id` 稳定可复用、同组事件顺序稳定。满足这两条即可在不同编码器下得到等价视图。

## §5 工具调用记录

### §5.1 写入时机

| 时机 | 事件 | 内容 |
|---|---|---|
| 工具轮响应 | `tool_call` × N | `name`、`args_digest`、`refs.tool_call_id`；`group_id=G` |
| 工具轮响应 | `assistant_speech` | 工具 `arguments.aside` 产生的台词；`group_id=G` |
| 工具执行完成 | `tool_result` × N | `ok`、`brief`、`artifact_id`；`group_id=G`、`refs.parent_seq` 指向对应 call |
| 解析失败 | `failed_turn` | 原文、`finish_reason`、tag；不参与投影 |
| 最终响应 | `assistant_turn` | 结构化字段（`v=1`）与原文；不带 `group_id` |

`aside` 不拼进工具记录，也不带 `（自言自语：` 标签。

### §5.2 参数与结果摘要

`args_digest` 生成规则：

- 只看顶层标量字段，最多 3 个，形如 `bvid=BV1aPJR6oExx, keyword=小狗`。
- 字符串截断 40 字符，嵌套对象只写 `{…}`。
- 空参数写 `-`。
- 禁止写入完整 JSON。

`brief` 的生成责任在 `ToolExecutor`（项目当前没有 `post_execute` 钩子，不引入新扩展点），回退链：

```
tool 返回的 __context__
  → executor 规范化：去 JSON、去内部工具名、截断 120 字符
  → 原始结果清洗后截断
  → "操作已完成" / "操作失败"
```

回退到最后一档时记 warning，计入观测。

### §5.3 不完整组与崩溃窗口

投影前扫描：`tool_call` 无对应 `tool_result`，或 `tool_result` 无对应 `tool_call`，视为不完整组。

- 不完整组一律按 `user-note` 形态输出，绝不发出半对消息；已是 `system-note` / `minimal` 的保持原档。
- 记 warning，计入 `orphan_groups`。

崩溃窗口：启动时扫描未闭合组，`side_effect=true` 的组只标记为 `unknown`，**不重放、不自动重试**，必要时在日志/面板提示人工确认；`side_effect=false` 的组按普通不完整组降级即可。日志不完整不等于可以重放，尤其是点赞、投币、评论这类已对外生效的操作。

## §6 存储与升级

### §6.1 切换策略

**不做数据迁移。** 新版本首次启动直接创建 `session_events`，从空日志开始。

理由是旧表的收益与成本不匹配：`context_entries` 每次保存都是全量重写，条目快速滚动，实际只承载最近几十条上下文；为它写一套转换、解析、校验、回滚逻辑，收益只在升级瞬间的一次性历史，成本却要长期维护。

具体约定：

| 项 | 结论 |
|---|---|
| 旧表 | `context_entries` 与 `context_meta` 保留在库中，代码不再创建、读取或写入，也不自动删除 |
| 升级表现 | 会话历史从零开始，桌宠不记得升级前的对话；`memories`、待办、定时器不受影响 |
| 首次启动 | 没有 `shutdown_time`，不显示"离开了 X 分钟"，属预期行为 |
| 发布说明 | 标注破坏性变更：会话上下文不延续；回退旧版本只能看到升级前旧历史，升级后产生的新历史不会写回旧表 |
| 旧表用途 | 仅作本地留档，便于用户核对或临时回退查看，不参与任何运行时路径 |

这样事件日志从第一天起就是单一事实来源，库内不会出现"新旧两份历史"并存的中间态。

### §6.2 配置项

| 配置 | 取值 | 说明 |
|---|---|---|
| `LLM_TOOL_ROLE_MODE` | `auto` / `native` / `user-note` / `system-note` / `minimal` | 默认 `auto` |
| `NATIVE_REPLAY_ARGS` | `minimal` / `empty` | 默认 `minimal` |
| `LLM_TOOL_ROLE_OVERRIDE` | 模型名前缀 → 预设名（hidden，不进设置页） | 覆盖 `_TOOL_CHANNEL_HINTS` |

### §6.3 增长与 compaction

`snapshot` 是折叠事件，不是物化消息列表。schema：

```json
{
  "v": 1,
  "covered_until": 12345,
  "summaries": [
    {"text": "……", "covers": [100, 900]}
  ],
  "tail": [12340, 12341]
}
```

| 约束 | 内容 |
|---|---|
| 同构 | `summaries` 单项与 `summary.payload` 同构，投影器复用同一套处理代码 |
| 只存引用 | `tail` 存 seq，不存事件副本 |
| 禁止嵌套 | `state` 内不得引用另一个 snapshot |
| 版本 | `v` 为唯一迁移依据 |

规则：

- 优先级：`snapshot` > `summary` > 原始事件。
- snapshot 内 summary 的 `covers` 只作元数据，不参与"跳过原始事件"的判断，因为原始事件已被整体覆盖。
- 物理清理只删除"落在有效 `summary.covers` 区间内"的原始事件：投影本就跳过它们，删除不改变视图。`summary` 与 `snapshot` 自身不删。
- 触发条件：清理挂在 slow_tick，事件数低于阈值（默认 800）直接跳过；本版不生成新 snapshot，只有历史遗留或将来写入的 snapshot 参与投影。

## §7 观测

每次投影记录一行结构化日志：

| 字段 | 说明 |
|---|---|
| `session_id` / `seq_range` | 本次投影覆盖的事件区间 |
| `profile` / `tool_channel` | 预设名与当前工具通道 |
| `messages` / `est_tokens` | 投影结果规模 |
| `dropped_groups` | 因预算丢弃的组数 |
| `orphan_groups` | 不完整组数量 |
| `elapsed_ms` | 投影耗时 |

业务指标：

- `failed_turn` 数量，以及其中正文含工具名的比例。
- `empty LLM response ... retrying once` 次数。
- 工具轮后落到 `sit` 兜底的占比。
- `session_artifacts` 总字节数与条数。
- 工具通道降档次数与最终停留档位，按 `(base_url, model)` 分组；`native` 占比应接近 100%。

## §8 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| `arguments` 被 schema 校验 | `native` 档 400 | 默认 `minimal` 重建；失败退 `empty`；再失败降一档 `tool_channel` |
| 二次裁剪 | 组被拆散、产生孤立 tool 消息 | 投影器为唯一裁剪入口（§4.6）；注入前扫描断言 |
| 修复轮重跑工具循环 | 已执行工具被重复执行 | 依赖工具侧幂等（`claim_action`）；修复提示带上"上一轮已执行的操作" |
| 事件增长 | 磁盘与查询变慢 | snapshot compaction；索引 `(session_id, seq)`、`(session_id, group_id)` |
| artifact 增长 | 磁盘被长文本占满 | 2048 字符阈值、TTL、总体积上限、清理前引用扫描 |
| 崩溃窗口 | `tool_call` 已写、`tool_result` 未写，副作用已发生 | `tool_call.side_effect` 标记；重启后只标记不重放（§5.3） |
| 严格端点 | `native` 档 400 | 错误反馈降档（§10.1）、孤儿检测、id 稳定 |
| 原生协议端点 | Anthropic / Gemini 无 `role="tool"` | 走 `user-note` 降档可保语义；真正的 wire 转换留给 §4.8 编码器，届时不改投影器 |
| 多 system 端点 | qwen3/ollama 500 | 与工具通道解耦：合并留在 `_merge_system_history`，不因 system 问题降 `tool_channel` |
| 过度降级 | 首次请求的偶发 400 被记成永久能力缺失 | 单次调用最多降一档；缓存只记本次进程，重启重探 |

## §9 验收

单测：

1. 投影是纯函数：同输入两次调用结果完全一致。
2. 日志只追加：任意操作后 `SELECT COUNT(*)` 单调不减。
3. `summary.covers` 内的事件不出现在投影里，但仍在表中。
4. 工具组裁剪：只删 `tool_call` 不删 `tool_result` 的情况不可能出现。
5. 不完整组降为 `user-note`，且不产生 `role="tool"`。
6. 四档投影的**自然语言文本**（`content`、`brief`、system/user 文案）不含工具内部名、原始 JSON、`→`、`[工具调用]`；`native` 的 `function.name` / `arguments` 属协议字段，不受此限。
7. `assistant_speech` 以独立台词出现，无标签；与 `content` 相同时不重复写。
8. 升级：旧表存在时新代码对 `context_entries` / `context_meta` 零读写；新事件只写 `session_events`。
9. `failed_turn` 不出现在任何档位的投影里。
10. 清洗：构造 `[工具调用] xxx → {...}` 正文，投影后该行变为 `[已省略]`，事件原文不变。
11. 多工具：一条 assistant 的 `tool_calls` 含多项，随后 tool 消息顺序与 id 对应。
12. `content` 为空时 native 输出 `content: null`，不报错。
13. 崩溃窗口：启动时未闭合组按 `side_effect` 分别处理，`side_effect=true` 只标记不重放。
14. 投影器是唯一裁剪入口：组装阶段不再调用旧裁剪，输出消息序列与投影结果一致。
15. `user-note` 档输出里不存在 `tool_calls`，也不存在 `role="tool"`。
16. 降档：模拟 400 且错误文本含 `tool_call_id`，同一调用内 `tool_channel` 掉一档并重试一次；第二次成功则请求链成功。
17. 降档次数有界：一次调用内连续两次 400 不会掉到 `minimal`，仍停在降一档后的档位并向上抛错。

端到端：

- MiMo 上完整跑一次"刷 B 站 → 点赞"，两轮历史重放不报错。
- 强制 `system-note`，同样流程不 500，且模型不再输出伪调用。
- 强制 `native` + `NATIVE_REPLAY_ARGS=empty`，确认 400 时降一档 `tool_channel` 而不是整轮失败。
- 切主/备模型时，仅切换 profile，事件表无写入。
- 复现 10-22 伪调用场景：`failed_turn` 落库但不出现在下一轮视图，修复轮产出正常行为。

## §10 关键设计决策（已核实）

四条均按现有代码核实。

### §10.1 `auto` 的判定与降档

仓库现有能力探测只有 `pet/brain/llm_gateway.py` 的模块级 `_thinking_unsupported`：首次 400 后置位，进程内生效、重启失效。仓库也没有模型名到能力的映射表，且 `llm_client._build_profile` 只构造 `OpenAI` 客户端，不存在原生协议分支。

因此 `auto` 不按模型品牌预判，默认直接发 `native`：国内主流 OpenAI 兼容端点（DeepSeek、Qwen、GLM、Kimi、豆包、MiniMax）都支持 `role="tool"`；把 `system-note` 当默认会把工具结果降成纯文本，白丢协议级结构。

| 项 | 结论 |
|---|---|
| 首档 | `native`，不做探测请求 |
| 触发降档 | 400 / 422，且错误文本命中 `tool` / `tool_call_id` / `role` / `function` |
| 降档幅度 | 同一次调用内只降一档并重试一次，避免连续降级掩盖真实错误 |
| 缓存 | 进程内 dict，键 `(base_url, model)`，值为已确认可用的最低档；重启清空 |
| 提示清单 | `_TOOL_CHANNEL_HINTS` 默认空，只列确知完全不支持工具协议的模型；与 `memory.py` 的分类常量同一路子 |
| 用户覆盖 | hidden 配置 `LLM_TOOL_ROLE_OVERRIDE`，进 `_KEY_META`、不进设置页 |
| 优先级 | 用户覆盖 > 提示清单 > 运行时降档 |
| 匹配方式 | **前缀 + 分隔符边界**：`qwen3` 命中 `qwen3-32b`、`qwen3:8b`，不命中 `qwen2.5`；不用裸 substring |
| 不参与判定 | system 条数（归 `_merge_system_history`）、图像能力（归 `supports_image`） |

不采用品牌清单做首判的原因：清单会随厂商迭代腐化，误判方向还是"多降一档"，代价高于一次 400 重试；而降档本身有缓存兜底，同一进程内只付一次。

### §10.2 `session_artifacts` 常量

`config.py` 对内部阈值没有先例，`crash_reporter._MAX_REPORTS`、`memory._BLOCKED_TTL` 都是代码内常量。artifact 属于实现细节，同样处理，省掉 `_KEY_META` 登记：

| 常量 | 值 | 说明 |
|---|---|---|
| `ARTIFACT_TTL_DAYS` | 3 | 对齐 `MEMORY_L3_EXPIRE_DAYS` |
| `ARTIFACT_MAX_BYTES` | 64 KB | 单条上限，超出截断并 warning |
| `ARTIFACT_TOTAL_BYTES` | 100 MB | 兜底保护，优先按 TTL 淘汰，再淘汰最旧 |

清理挂在现有 slow_tick 维护链路，不新增线程。

### §10.3 记忆写入不入事件日志

`assistant_turn.memory_line` 已是审计源。三条硬冲突决定不能并入：

1. `memories` 有 UPDATE/DELETE 语义（`_do_merge` 更新、`enforce_capacity` 淘汰、整点合并删旧），只追加日志无法表达"后来被合并"；要真审计得给记忆库加补偿事件，与 §2"不改记忆库"冲突。
2. 记忆写入是异步且可能失败（`pet_agent.py` 的 `_save_memory_line` 只 warn），日志记"已写入"会与库内事实不一致。
3. 记忆跨会话、会话事件不跨会话，生命周期不同。

需要观测时记在记忆库侧（成功 / 命中去重 / 失败），不混入事件日志。

### §10.4 `snapshot.state` 的稳定性

snapshot 一旦写入不可变，格式变更只能靠 `v` 迁移，因此：`summaries` 与 `summary.payload` 同构、`tail` 只存 seq 引用、禁止嵌套、顶层带 `v`。投影器对 snapshot 内与 snapshot 外的 summary 复用同一套处理代码，不写第二套逻辑。

## §11 落地记录

模块：`pet/brain/events.py`（EventStore）、`pet/brain/projection.py`（project / Budget / Profile）、`pet/brain/tool_channel.py`（通道选择与降档）。接入点：`BrainMixin.add_assistant_turn` 写结构化响应、`add_context` 写其余事件、`get_multi_turn_messages` 走投影、`record_tool_events` 写工具组、`llm_gateway` 在拒绝时降档。

与上文不同的实现取舍：

| 项 | 实现 | 原因 |
|---|---|---|
| `assistant_turn` 时机 | 结果产出方写一条结构化事件（`v=1`），`text` 只作审计 | 投影按字段还原，历史与模型输出在字段层面一致；工具轮中间正文与最终输出重复，不入日志 |
| 降档方式 | `degrade_messages` 改写本次请求体 | 重投影需要回传原始事件与 Profile，代价高于就地改写；缓存保证下一次投影直接是低档 |
| `assistant_speech` | 与 `tool_call` 同组，投影时并入 assistant `content` | 满足"紧随 tool 消息"的顺序约束 |
| `side_effect` | 由 `ToolDef` 声明，未注册工具按有副作用处理 | 崩溃窗口只关心"是否动过外部世界"；未知即保守 |
| artifact 生产 | `record_tool_events` 收到原文超 2048 字符时落表 | `brief` 只是短句，原文需要可追溯的落点 |
| 增长控制 | 按摘要覆盖区间物理回收（`purge_covered`），不写 snapshot 折叠 | 被覆盖事件投影本就跳过，删除不改视图；snapshot 折叠需要连续前缀覆盖，现实中难满足且收益低 |

未实现：`supports_image` 未接入（图片仍只在当轮请求里）；`failed_turn` 在两条非流式与流式入口都会落库，修复轮会再记一条。
