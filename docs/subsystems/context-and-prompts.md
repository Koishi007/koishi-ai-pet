# 上下文与提示词组装

每轮请求发给模型的内容由三部分拼成：**静态块**（`pet/brain/prompts.py`）、
**运行时块**（`pet/brain/context_builder.py`）、**多轮历史**（`pet/brain/base.py`）。
块的机械清单与组合白名单见 [reference/prompt-blocks.md](../reference/prompt-blocks.md)（生成物）。

## 1. system prompt 的拼接顺序

`ContextBuilder._build_system(mode, task)`：

1. `prompts.build_system_prompt(mode, task)` 产出的静态块，顺序固定：
   身份 → 独立生活设定 → 输入可信度 → 你的人格 → 人格台词范例 →
   称呼 → 表达底线 → 记忆格式（`autonomous` / `chat` / `analyze`）→ **感知段**（按 `mode`，末尾是动作表）→ **任务段**（按 `task`）→ `<<FEELING>>` 锚点。
   人格与范例为空、记忆格式不适用时会跳过对应块。锚点排在所有静态块之后 - 运行时块接在它后面，
   静态前缀（锚点之前）逐轮不变才能命中 prompt 缓存。
2. 运行时块，拼好后替换掉 `<<FEELING>>` 锚点：
   - `[你现在的状态]`：`_build_feeling()`（数值→自然语言）+ `_build_attention_hint()`（仅自主任务，连续未互动档位）；
   - `[你惦记着的事]`：`_build_needs_note()`（未满足需求 + 该怎么做）+ `_memory_event_note()`（忽然想起来的旧事），
     **只在 `_NEEDS_TASKS`（`autonomous` / `chat`）注入**；
   - `[最近发生了什么]`：`_recent_events_note()`（近期事件，含工具上报与动作产出）。
3. 最后追加 `[你对用户的记忆]`：`memory_store.retrieve_context(user_message)` 的召回结果（可能为空）。

`mode` 决定感知段（视觉 / 非视觉 / 对话 / 交互 / 分析），`task` 决定任务段（自主 / 对话 / 分析 / 交互），
合法组合是白名单，写错直接抛 `ValueError` - 新增模式或任务要**同时**改
`_PERCEPTION_SECTIONS`、`_TASK_SECTIONS` 与 `build_system_prompt` 里的组合白名单三处。

## 2. user 段与历史

- user 段承载**每轮都会变、又必须原样进上下文的东西**：时间前缀（`_time_prefix`）、窗口探测结果、
  用户消息或截图。感受/需求/旧事这类逐轮内容走 system 尾部的锚点（见上），不进静态前缀；
  静态前缀稳定才能命中 prompt 缓存（`LLM_CACHE_PROMPT`）。
- 历史多轮由 `BrainMixin.get_multi_turn_messages` 装配，先按条数上限淘汰、再按 token 预算淘汰；
  历史里的 system 片段会被 `_merge_system_history` 并成一段 `[上下文备注]` 追加到 system 末尾，
  保持「一个 system + 若干 user/assistant」的干净结构。
- **候选池与每轮注入是同一条上限**：`CONTEXT_HISTORY_ENTRIES`（默认 15）是 `_context` 池子的容量
  （`_evict_context()` 的超限淘汰依据），而每轮注入的条数上限取池子的常态上界
  `_MAX_POOL_ENTRIES = _MAX_ENTRIES + _EVICT_BATCH_SIZE`（默认 21） - 淘汰是批量触发的
  （攒够 6 条才裁一批），池子会在容量之上再多挂几条，注入上限必须跟着池子的真实上界走，
  否则多出来的那几条每轮都被条数裁剪排除、又够不上淘汰。池内条目正常都会入选本轮
  （例外只有 token 预算超限，以及工具调用挤占配额把池子顶到上界之外时的兜底）；
  被淘汰的普通对话**必定**进 `_pending_summary_queue`，压成摘要后回到池子。
  取这个不变式的原因见 [设计文档](../specs/2026-09-29-context-injection-pool-unify-design.md) §3。
- 交互任务（`interact`）只发 system + user 两条，不带历史。

## 3. 动态块的生命周期

| 块 | 状态保存在哪 | 什么时候消失 |
|---|---|---|
| 需求（`_active_needs`） | `ContextBuilder` 内存字典（需求 → 起始时间） | 数值恢复即移除；重启后「已持续」归零 |
| 旧事（`_recent_event_ids`） | 同上（上一轮注入过的 id） | 每轮更新，候选耗尽时允许重复 |
| 近期事件 | `PetAgent` 的事件缓冲区 | 超出 `RECENT_EVENT_WINDOW_S` 保鲜窗口后不再注入 |
| 求关注提示 | `PetAgent.rounds_without_user` | 用户一说话就清零 |
| 召回记忆 | 记忆库（SQLite） | 每轮重新召回，与冷却、有效分相关（见 [memory.md](memory.md)） |

作息需求（`bedtime` / `drowsy`）不依赖数值、按钟点判定，钟点定义见
[vitals-and-mood.md](vitals-and-mood.md) §4；文案里不出现具体时间 - `system` 里放钟点会让缓存每秒失效。

## 4. 四条任务路径的差异

| | autonomous | chat | analyze | interact |
|---|---|---|---|---|
| mode | `autonomous_vision` / `autonomous_non_vision` | `chat_vision` / `chat_non_vision` | `analyze` | `interact` |
| 记忆格式块 | 有 | 有 | 有 | 无 |
| `[你惦记着的事]` | 注入 | 注入 | **不注入** | **不注入** |
| 求关注提示 | 有 | 无 | 无 | 无 |
| 历史多轮 | 有 | 有 | 有 | 无 |
| 随轮截图 | 有 | 有 | **不带** | 无 |
| 感知段 | 视觉 / 窗口探测 | 视觉 / 窗口探测 | 只有分析段与动作表 | 只有动作表 |
| 动作要求 | 按耗时撞满 | ≥3 个 | ≥3 个 | 1-2 个 |

`interact` 是对单一事件的反射（被抓、放下、投喂、窗口消失），注入长上下文只会让台词偏离事件本身，
所以它的 prompt 最薄。`analyze` 走与 `chat` 相同的装配（历史、上下文池、记忆都保留），
差别只在感知段、任务段与 user 段的步骤清单：它面向「看懂用户交付的东西」，动作数量要求与 `chat` 相同。

## 5. 设计取舍

1. **稳定前缀、变化进 user**：时间、窗口原始数据、用户消息、截图都在 user 段；
   感受/需求/旧事等受控运行时块只排在静态 system 前缀之后（`<<FEELING>>` 锚点），
   否则 prompt 缓存的前缀每一轮都会失效。
2. **数值不直接进提示词**：先由 `_build_feeling` 译成感受，档位与数值区间一一对应；
   档位表、需求阈值与作息判定见 [vitals-and-mood.md](vitals-and-mood.md) §4。
3. **「该怎么做」集中在一处**：需求对应的做法只写在「你惦记着的事」章节（`_NEED_HINTS`）；
   静态块、任务段与自主 user prompt 只做抽象引导（「反映当前状态」「见『你惦记着的事』」），
   感受描述里也不放祈使句，两处都给指令时模型会任选其一，行为变得不可预测。
4. **被动注入不产生副作用**：旧事走只读查询（不 touch 记忆的访问统计），
   见 [memory.md](memory.md) §3。
5. **块与任务解耦**：块是常量、任务决定组合，新增任务只需加一个构建函数并在白名单登记。

## 6. 不变量与陷阱

1. 新增 `mode` / `task` 要改**三处**（`_PERCEPTION_SECTIONS`、`_TASK_SECTIONS`、组合白名单），
   否则 `ValueError` 或组合被拒。
2. `_PERCEPTION_SECTIONS` 里的动作表是模块级共享的 `_Lazy(generate_action_section)` - **首次求值后缓存**，
   让 system 前缀在进程内保持稳定。时长范围随 `SCHEDULER_MID_MS` / `LLM_ACTION_MIN_DIVISOR` 变化，
   调度相关配置变更时由设置界面调用 `prompts.invalidate_action_section()` 显式失效，而不是每轮重算。
3. 需求阈值必须与感受档位一致（档位与 `_NEED_THRESHOLD` 的权威定义在
   [vitals-and-mood.md](vitals-and-mood.md) §4，`sanity` 走 `SANITY_CRITICAL_THRESHOLD`）。
   两条都有注释与测试锁定。
4. `_NEEDS_TASKS` 决定哪些任务注入需求块，改它等于改 `interact` 的台词风格。
5. 任何块的文案改动都会体现在生成物 `docs/reference/prompt-blocks.md` 里，
   改动后随之运行 `python scripts/gen_docs.py`，否则 CI 的文档检查会失败。
6. 时间前缀与窗口信息不进 system 静态前缀；运行时块也只能接在 `<<FEELING>>` 锚点之后，
   否则代价是 prompt 缓存整体失效。
