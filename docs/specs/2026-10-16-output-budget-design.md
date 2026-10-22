# 输出预算与截断处理设计

> 上游把思考计入 `max_tokens`，长思考会把额度占满并以 `finish_reason=length` 结束，整段输出作废。本改动把输出上限从用户配置收回归代码：按模式给基线，思考与工具各自追加一份预算，统一硬上限封顶；空响应修复只在 `length` 截断时才关闭思考。

## §0 背景与现状

改动前的输出上限（`pet/config.py` 的 `_KEY_META`）：

| 项 | 现状 | 位置 |
|---|---|---|
| 可见配置 | 交互 1024 / 聊天 4096 / 自主 4096，界面范围 100~8000 | `pet/ui/settings_window.py:489-494` |
| 无界面入口 | `LLM_MAX_TOKENS_ANALYZE` 4096 | `pet/config.py` |
| 仅配置文件 | `LLM_MAX_TOKENS_SUMMARY` 1024 | `pet/config.py` |
| 语义 | 单次请求 completion 总量，含思考 | `pet/brain/llm_gateway.py` |
| 作用范围 | 同一数值复制给工具循环的每一轮 | `pet/brain/tool_loop.py:167-172` |
| 结束原因 | `finish_reason` 只进日志，编排层读不到 | `pet/brain/parsing.py:554-559` |
| 空响应重试 | 与首次请求同预算、同思考设定再跑一次 | `pet/brain/repair.py:23-33` |

失败链：思考占满额度 → 流以 `length` 结束 → 解析不到任何字段 → 修复重试再用同预算重跑一次，`length` 复现，两次额度全部落空。

既有运行记录的结论有两项：上报的 completion 已含思考；空响应基本来自输出未遵循行标签协议（`finish_reason=stop`），与预算无关。自主决策的实际用量已贴近原上限，思考再变长即触发截断，这是本次上调额度的直接依据。

## §1 目标

- 思考与工具的增量各自抬高上限：开思考或带工具时预算追加，不压缩正文基线。
- 用户不需要理解 token 语义，也没有把它改坏的机会。
- 截断可辨识：`length` 与格式类失败在日志与重试策略上分开。
- 常见请求的额度贴近实际用量，只有工具轮与思考型请求占用大额度。

## §2 非目标

- 不改输入侧预算（`CONTEXT_TOKEN_BUDGET` 与其淘汰策略不动）。
- 不设服务商级的思考上限（`thinking_budget` / `reasoning_effort` 依赖服务商支持，见 §8）。
- 不为预算新增配置键：数值属于实现细节，不适合再开一个用户入口。
- 不改工具轮次上限（`LLM_TOOL_MAX_ROUNDS`）与超时。

## §3 预算模型

### §3.1 三段配额

`pet/brain/budget.py` 的 `output_budget(mode, thinking=..., has_tools=...)`：

```
max_tokens = min(_CEILING, 基线 + (思考开启 ? 思考预留 : 0) + (带工具 ? _TOOLS_RESERVE : 0))
```

| mode | 基线 | 思考预留 | 关思考 | 开思考 | 开思考 + 带工具 |
|---|---|---|---|---|---|
| `interact` | 512 | 1536 | 512 | 2048 | 4096 |
| `chat` / `analyze` / `autonomous` | 2048 | 4096 | 2048 | 6144 | 8192 |
| `summary` | 512 | 512 | 512 | 1024 | - |

- `_TOOLS_RESERVE = 2048`、`_CEILING = 8192`。
- 三段相加得到的是请求侧唯一的 `max_tokens`。服务商不为思考与正文分别设限：预留的作用是把上限抬高，使思考占用时正文基线仍然存在；思考超过预留后仍会占满整个上限，与 `length` 截断的关系见 §4.2。
- 工具预留按「首轮带 tools」给：`has_tools=bool(tools_param)`，工具循环的每一轮由 `_handle_tool_calls` 按 `has_tools=True` 重算，与首轮同档。
- 交互档的思考预留低于决策档：交互的正文只有一句台词加 1~2 个动作。

### §3.2 数值依据

- 决策档基线按正文结构取：摘要、台词与动作行合计数百 token，2048 留足冗余。
- 思考预留取基线的两倍：思考长度是主要波动来源，模型之间差异也大。
- 交互档的正文只有一句台词加 1~2 个动作，基线与思考预留整体低于决策档。
- 工具预留独立于模式：写文件与写笔记的正文落在 tool arguments 内，单轮需要额外额度。
- 硬上限取决策档最坏组合（基线 + 思考预留 + 工具预留）的合计值。

数值随模型换代需要复测，方法见 §8。

### §3.3 思考判定

`thinking_active(thinking)` 与请求参数注入同源：`thinking=None` 时跟随 `LLM_THINKING_DISABLED`，显式值覆盖全局开关；`pet/brain/llm_gateway.py` 的 `_apply_thinking_param` 改调同一函数，避免预算与请求参数在「这次到底带不带思考」上出现分歧。

## §4 截断处理

### §4.1 结束原因链路

`pet/brain/parsing.py` 的 `BehaviorSink` 增加 `note_finish_reason(reason)`：流式在 `collect_stream` 消费结束后上报，非流式在 `_call_llm_and_parse` 直接读 `resp.choices[0].finish_reason`。`Behavior` 用与 `_last_unparsed` 相同的线程本地属性存放，管线入口（非修复轮）重置为 `""`。

非流式解析同时关掉动作兜底（`action_fallback=False`）：`retry_if_empty` 以「无动作且无台词」判定空响应，兜底补出的 `sit` 会让空响应看不出来。`sit 5s` 改由 `Behavior._decide_with_repair` 在修复轮之后仍为空时补上，与原行为等价。

诊断日志随之带上结束原因：`响应未解析出字段：content X 字符、reasoning Y 字符、finish_reason length`。

### §4.2 只在 length 时关闭思考

`Behavior._thinking_for_repair()`：上一轮 `finish_reason == "length"` 时修复轮返回 `thinking=False`，其余失败原样透传调用方与用户设定。截断说明思考用光了抬高的上限（§3.1），关闭思考才能把额度交还正文；格式类失败的成因不在思考，保持设定是为了不改变第二次尝试的能力。

`Behavior._note_budget_truncation()` 在空输出且上一轮为 `length` 时单独打一条告警，与格式类失败分开。

### §4.3 预算随之下调

修复轮关闭思考后 `output_budget` 不再计入思考预留，第二次请求的额度低于第一次；工具情况不变，因此差异全部来自思考预留。

## §5 配置面变化

| 动作 | 键 / 位置 |
|---|---|
| 删除 | `LLM_MAX_TOKENS_INTERACT`、`LLM_MAX_TOKENS_CHAT`、`LLM_MAX_TOKENS_ANALYZE`、`LLM_MAX_TOKENS_AUTONOMOUS`、`LLM_MAX_TOKENS_SUMMARY` |
| 删除 | `pet/ui/settings_window.py` 的三个输入框与模式联动的字段项 |
| 保留不动 | `LLM_THINKING_DISABLED`（直接影响表现，属于用户可见取舍） |

旧 `settings.json` 里的残留键由 `Config._load_user_settings` 按 `_KEY_META` 过滤，不报错也不生效。

## §6 文件改动清单

| 文件 | 改动 |
|---|---|
| `pet/brain/budget.py`（新增） | 配额表、`thinking_active()`、`output_budget()` |
| `pet/brain/behavior.py` | 五个入口传 `mode` 不再传 `max_tokens`；管线内按 `tools_param` 算预算；新增 `note_finish_reason` 与线程本地的 `_last_finish_reason`；`_thinking_for_repair` / `_note_budget_truncation`；工具轮按带工具的预算；非流式解析关掉动作兜底，`sit 5s` 移到 `_decide_with_repair` |
| `pet/brain/parsing.py` | `BehaviorSink` 增加 `note_finish_reason`，`collect_stream` 上报，诊断行补 `finish_reason`，`_NullSink` 补空实现 |
| `pet/brain/llm_gateway.py` | `_apply_thinking_param` 复用 `thinking_active` |
| `pet/brain/summary.py` | 摘要改用 `output_budget("summary")` |
| `pet/config.py` | 删除五个上限键 |
| `pet/ui/settings_window.py` | 删除三个输入框与联动项 |
| `tests/test_brain_budget.py`（新增） | 配额组合、封顶、思考判定 |
| `tests/test_brain_decision_modules.py` | `length` 截断关闭思考并降预算；修复轮保持 thinking 与降温；假 LLM 记录预算与 `finish_reason` |
| `tests/test_brain_parsing.py` | 结束原因上报与「无 finish_reason 不上报」 |
| `docs/architecture.md` | brain 模块表与数据流补预算、结束原因 |

## §7 验收

- `python -m pytest` 全量通过；`python scripts/gen_docs.py --check` 通过。
- 新增预算用例覆盖：各 mode 的关/开思考与带工具组合、超过硬上限时的封顶、未知 mode 报错、`thinking=None` 跟随全局开关。
- 决策入口用例覆盖：`finish_reason=length` 的空响应在修复轮 `thinking=False` 且预算下降；`stop` 的空响应保持原设定；解析 sink 收到结束原因。
- 非流式入口用例覆盖：`length` 截断触发修复并关闭思考；两次都为空时补 `sit 5s`；告警点名预算耗尽。

## §8 风险与复测方法

- 硬上限 8192 是常量。服务商拒绝较大的 `max_tokens` 时（400 参数非法），当前链路会退回本地兜底；后续可加「400 指向 max_tokens 时上限减半重试并记住」的降级，与 `_thinking_unsupported` 同款。
- 工具轮的最终解析已关掉动作兜底（`pet/brain/tool_loop.py` 的 `parse_behavior(content, action_fallback=False)`），空响应保持为空，由入口补修复轮，`length` 截断在这条路径上可辨识。修复轮从最初的 messages 重跑，工具结果不在其中，可能重复执行有副作用的工具：`bilibili` 有 `claim_action` 幂等兜底，其余依赖工具自身幂等。
- 配额数值取自当前模型的运行结果。换更重的思考模型后复测：以是否出现 `length`、以及 completion 是否贴近当前额度为准，按 §3.2 的同比例调整基线与思考预留。
