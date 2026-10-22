# 工具输出声明与投影设计

> 工具的返回值从「随手返回一个 dict」改为「按 `output.schema` 声明的规范值」；框架用统一的 render 把规范值投影成模型可见的 `ContentBlock[]`。统一的是声明与投影的机制，不是各工具的具体字段。
>
> 本文描述最终形态。修订：2026-10-22 初稿；同日按评审收敛（删除 schema 回退、补齐 `$ref` 与 `model_hint` 的归属、校验改为迁移期只 warn、artifact 正文定义）。

## §0 现状与问题

| 项 | 现状 | 位置 |
|---|---|---|
| 返回值 | 任意 dict / str / list，无契约 | 各 `pet/tools/*/core.py` |
| 文本出口 | `summary` 一个字段兼三种用途 | `ToolExecutor._normalize` |
| 并列头部 | `summary` 与 `model_hint` 同时拼在前面 | `_normalize` |
| 魔法键 | `__image__` / `__image_mime__` / `__context__` | `_normalize`、`_execute_one` |
| 当轮序列化 | `dict` → `summary + model_hint + 其余字段 JSON` | `_normalize` |
| 历史记录 | `context_brief → summary → 结果首行 → "操作已完成"` 三级回退 | `tool_loop._tool_brief` |
| 字段语义 | 靠口头约定 | 各 `core.py` |

具体表现：

- `summary` 名义上是「一句话摘要」，实际被当成正文用：`web_search` 是 `"\n".join(lines)` 的搜索结果列表，`browser.read_url` 是整页正文，`weather` 是多行预报。历史记录只能截前 120 字符，常常断在句子中间。
- `model_hint` 是 `game` 独有的事实标准，21 处用来给模型下「下一步该调什么」的指令。它和 `summary` 一起被拼在文本最前，但没有任何声明机制保证别的工具也能用。
- `hint`（`file_ops`、`tool_search`）没有被 `_normalize` 识别，被 JSON 包了一层，模型读到的是 `{"hint": "..."}`。
- `browser.read_url`、`file_ops.*` 没有 `summary`，靠 `__context__` 兜底；`tool_search__list_groups` 两者都没有，历史里记为「操作已完成」，模型下次还会重复调用。
- 同一个值有两套渲染：当轮走 `_normalize`，历史走 `_tool_brief`。两边规则不同步，改一处另一处容易漏。
- 工具作者无法从代码得知「应该返回哪些字段」，只能抄隔壁工具。

## §1 目标

- 每个工具有一份**可执行的输出契约**：`output.schema`，约束规范返回值的形状。
- 框架只提供**一套渲染机制**，所有工具共用；工具不再各自拼文本。
- 规范值到模型视图有两条固定投影：**当轮视图**（完整）与**历史视图**（一句话）。
- 字段名遵循「见名知义」的词表，禁止再出现 `summary` 这类一名多用的字段。
- 现有键（`model_hint` / `hint` / `__context__` / `__image__`）逐个给出迁移去向，删除旧路径时不静默丢行为。

## §2 非目标

- 不统一各工具的**具体字段**。天气有 `temperature`，文件有 `chars_read`，各自声明即可。
- 不改 `tool_call` 当轮的 OpenAI 协议形态（`assistant.tool_calls` + `role="tool"`）。
- 不引入 schema 库；校验用内置轻量实现。
- 不动 `session_artifacts` 的落盘时机（仍由超长原文触发）。
- 不改文件动作（`add_file_action`）的返回约定，只补一个内置默认定义，见 §6.5。

## §3 声明层

### §3.1 类型

```python
@dataclass(frozen=True)
class ToolOutputDefinition:
    schema: dict                      # JSON Schema 子集 + x-role 扩展
    renderer: Callable | None = None  # 需要特殊排版时才提供
```

注册：

```python
registry.add_method(
    TOOL_NAME, "get_current",
    "查询指定城市当前天气",
    handler=get_current,
    args={...},
    output=ToolOutputDefinition(schema=WEATHER_OUTPUT),
)
```

历史视图固定取 `x-role: headline` 的字段，不另设 `record_field`：语义只有一处声明，避免两个开关互相覆盖。多个字段标 `headline` 时取声明顺序第一个，并记 warning。

**静态可解析是硬约束**：`load_tools` 与 `gen_docs` 只用 AST 读源码，不在运行时加载工具（会触发工具私有依赖的 pip 安装）。因此 `output=` 的实参只能是同文件内的字典字面量，或指向字面量常量的名字，二者都不允许函数调用。schema 常量必须放在注册文件（通常 `__init__.py`）里，写成模块级字面量；跨文件复用的片段用 `**常量名` 展开一层，解析器需要支持这一形式。违反此约束的后果是 `reference/tools.md` 静默缺列，因此配一条测试：声明了 `output` 的方法必须能被静态解析出 schema。

### §3.2 schema 方言

支持 JSON Schema draft 2020-12 的子集，**不解析 `$ref`**：

| 关键字 | 说明 |
|---|---|
| `type` | `object` / `array` / `string` / `number` / `integer` / `boolean` / `null` |
| `required` | 字段名数组 |
| `properties` | 子字段声明，可嵌套 |
| `items` | 数组元素声明 |
| `enum` | 枚举值 |
| `description` | 字段说明，生成参考文档用 |
| `maxLength` | 字符串长度上限，`headline` 用它约束 |
| `additionalProperties` | 只支持 `false`，且只在阶段 5 之后生效 |
| `x-role` | 投影角色，render 的唯一依据 |

公共片段是模块级字面量常量，用 `**` 展开，schema 内不出现 `$ref`，也不出现函数调用：

```python
PAGINATION_SCHEMA = {
    "type": "object",
    "x-role": "pagination",
    "properties": {
        "page":        {"type": "integer", "x-role": "suppress", "description": "当前页码"},
        "page_size":   {"type": "integer", "x-role": "suppress", "description": "每页条数"},
        "total_items": {"type": "integer", "x-role": "suppress", "description": "总条数"},
        "has_next":    {"type": "boolean", "x-role": "suppress", "description": "是否还有下一页"},
    },
}

READ_FILE_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline":   {"type": "string", "maxLength": 120, "x-role": "headline",
                       "description": "一句话结论"},
        "body":       {"type": "string", "x-role": "body", "description": "文件正文"},
        "pagination": {**PAGINATION_SCHEMA},
    },
}
```

`x-role` 词表：

| `x-role` | 投影结果 | 进历史 |
|---|---|---|
| `headline` | 文本块，置顶 | 是（截 120 字符） |
| `body` | 文本块，原样保留换行 | 否 |
| `data` | 紧凑键值块，嵌套用点号路径 | 否 |
| `hint` | 文本块，置于最后，提示下一步动作 | 否 |
| `images` | 图片块数组 | 否 |
| `pagination` | 追加一行「第 1/3 页，共 100 条」 | 是（附加在 headline 后） |
| `suppress` | 丢弃 | 否 |

`hint` 承接现有 `model_hint` 与 `hint`：它们是「给模型的下一步指令」，语气与正文不同，且不该进历史。混进 `body` 会让历史记录变成指令文本。

### §3.3 字段命名规则

`x-role` 负责语义，字段名负责可读性：

- 名词单数、集合复数：`item` / `items`，`image` / `images`
- 布尔以 `ok` / `has_*` / `is_*` 开头
- 数量带所指：`chars_read`、`total_items`，不用 `count`、`size`
- 时间统一 `*_at` 后缀
- 错误统一 `error: {code, message}`，`code` 为稳定的蛇形标识
- 禁止 `summary`、`data1`、`__xxx__` 这类一名多用或魔法命名
- `hint` 类字段推荐 `agent_hint`，与 `x-role: hint` 对应

### §3.4 硬约束

1. `headline` 必填，且 `maxLength: 120`。
2. `suppress` 字段不得出现在任何投影里。
3. `images[].base64` 不得出现在文本投影中。
4. 分页工具的 `has_next` 由工具计算，render 不推断。
5. 同一工具的 schema 里 `headline` 至多一个。

## §4 规范返回值

```python
def get_current(city: str = "Beijing") -> dict:
    return {
        "headline": "深圳 晴 29.6 度，体感 31 度",
        "body": "明天转阴，32 度；后天有雨，28 度。",
        "data": {"city": "深圳", "temperature": 29.6, "humidity": 62},
    }
```

```python
WEATHER_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "maxLength": 120, "x-role": "headline"},
        "body":     {"type": "string", "x-role": "body"},
        "data":     {"type": "object", "x-role": "data"},
        "images":   images_schema(),
    },
}
```

推荐字段名（不强制）：`headline` / `body` / `data` / `images` / `pagination` / `items` / `agent_hint` / `ok` / `error`。

## §5 渲染机制

### §5.1 投影目标

```python
@dataclass(frozen=True)
class TextBlock:
    text: str
    role: str = "primary"    # primary | data | hint

@dataclass(frozen=True)
class ImageBlock:
    base64: str
    mime: str
    caption: str = ""
```

`ContentBlock = TextBlock | ImageBlock`。这是内部表示，落 wire 格式交给编码层。

两条边界：

- `TextBlock.role` 只在渲染端有意义（排序与合并依据），**不得泄漏到 wire**。编码器只看 `text`，自行决定合并成一个字符串还是拆成多个 content 块。
- `ImageBlock` 不进 `role="tool"` 消息。多数兼容端点不接受 tool 消息里的多模态内容，现在也是绕开的：`tool_loop` 把图片汇总后，作为紧邻的一次请求里的 user 消息附上。编码层负责把 `ImageBlock` 转成 user 消息的 `image_url`，native 档同样如此。

这条边界决定了 `native` 档不会因为工具带图而触发 400，也是 `_image_source` 能被 `images[].caption` 取代的前提。

### §5.2 当轮投影

```python
def render_turn(definition: ToolOutputDefinition, value) -> list[ContentBlock]:
    """按 schema 声明顺序展开：headline → body → data → pagination → images → hint。"""
```

1. 按 `x-role` 的固定优先级排序（`headline` → `body` → `data` → `pagination` → `images` → `hint`），同角色内按 `schema.properties` 的声明顺序，两者都不按返回 dict 的键序。
2. `headline` / `body` → `TextBlock(role="primary")`，字段之间空行分隔。
3. `data` → 递归展开为 `key: value` 行，一个 `TextBlock(role="data")`。
4. `pagination.has_next` → `TextBlock(role="hint")`：「第 1/3 页，共 100 条，用 page=2 继续」。
5. `images` → 逐个 `ImageBlock`，`caption` 取 `images[].caption`，缺省用工具名；这些块只交给编码层附在下一轮 user 消息里，不进 `role="tool"`。
6. `hint` → `TextBlock(role="hint")`，多段按声明顺序。
7. `suppress` 跳过；未在 schema 中声明的返回字段**归入 `data` 渲染**，不判违约（迁移期策略，见 §5.5）。
8. `renderer` 非空时整段交给它，返回值仍须是 `list[ContentBlock]`。

**预算裁剪**：当轮文本总量超过 `output_budget(mode, has_tools=True)` 时，先截 `data`，再截 `body`，`headline` 与 `hint` 不截。顺序固定，避免把结论或下一步指令削掉。

### §5.3 历史投影

```python
def render_record(definition: ToolOutputDefinition, value) -> str:
    """取 x-role: headline 的字段，附加分页提示，截 120 字符。"""
```

- 缺失或空 → `"操作已完成"` / `"操作失败"`
- `pagination.has_next` 为真时追加「，还有更多」
- 结果写入 `session_events` 的 `tool_result.payload.record`

### §5.4 artifact 正文

原文超过 `ARTIFACT_INLINE_LIMIT` 时落 `session_artifacts`，`body` 取 **`render_turn` 的纯文本拼接**：

```
headline
\n
body
\n
key: value 逐行
```

不含图片 base64，不含 `suppress` 字段。这样 `read_artifact` 拿到的内容与当轮模型所见一致，不会出现「历史视图有、artifact 没有」的错位。

### §5.5 校验与降级

```python
def validate(definition: ToolOutputDefinition, value) -> list[str]:
    """返回违约路径列表，空表示通过。"""
```

支持 `type` / `required` / `properties` / `items` / `enum` / `maxLength` / `additionalProperties: false`，不做 `oneOf`、`pattern`、数值区间。

**分两个开关**：

| 模式 | 行为 | 使用阶段 |
|---|---|---|
| `warn` | 记 warning，未声明字段归入 `data` 渲染，缺 `headline` 时用 `data` 首字段兜底 | 阶段 1 至 4 |
| `strict` | 违约即降级为「工具返回不符合约定（headline 缺失）」 | 阶段 5 |

迁移期默认 `warn`。原因是 `game` / `system_monitor` / `todo` 的返回键随分支变化，立即 strict 会把可读结果换成一句废话，比现状更差。切 `strict` 的条件见 §7 的迁移出口。

`headline` 超长不算违约：`validate` 记 warning，`render` 截断到 120 字符，避免因为一句话过长就丢掉整条结果。

## §6 与现有链路的衔接

### §6.1 传输层结构

```python
@dataclass
class ToolResult:
    name: str
    output: Any = None                    # 规范值，符合 output.schema
    error_code: str = ""                  # 空即成功
    error_message: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.error_code
```

- `success` 由 `ok` 取代，只保留一个真值源；`tool_loop` 里 `bool(result.success)` 改为 `result.ok`
- `data` 改名 `output`，`error: str` 拆成 `error_code` 与 `error_message`
- `image_b64` / `image_mime` 删除，图片走 `images` 字段；`executor._execute_one` 里 `pop("__image__")` 的两行随之删除
- `context_brief` 删除，由 `render_record` 顶替；`tool_loop._image_source` 一并删除，caption 由 `images[].caption` 承担

### §6.2 `tool_result` payload

```json
{"ok": true, "record": "搜到 1 个工具：weather", "artifact_id": null}
```

`brief` 改名为 `record`。库里没有带 `brief` 的历史事件（会话事件表随本次改造从零开始，见会话事件文档 §6.1 的破坏性切换），因此**不做 `record or brief` 的双读**，读点只认 `record`。

改动清单（改名连坐处）：

| 位置 | 现状 | 改后 |
|---|---|---|
| `pet/tools/executor.py` 的 `_normalize` | 产出当轮文本 | 由 `render_turn` 取代 |
| `pet/brain/tool_loop.py` 的 `_tool_brief` | 三级回退产出 brief | 改为 `render_record` |
| `pet/brain/tool_loop.py` 的 `_record_tool_events` | `results[].brief = _tool_brief(...)` | `results[].record = render_record(...)` |
| `pet/brain/base.py` 的 `record_tool_events` | `payload["brief"]` | `payload["record"]` |
| `pet/brain/projection.py` 的 `join_briefs` | `payload.get("brief")` | `payload.get("record")` |
| `pet/brain/projection.py` 的 `_render_group`（native 分支） | `payload.get("brief")` | `payload.get("record")` |
| `pet/brain/projection.py` 的 `degrade_messages` | 兜底文案「操作已完成」 | 改读 `record`，兜底文案不变 |

兜底文案统一为 `操作已完成` / `操作失败` / `操作结果未知`，只在这三处出现：`render_record`、`join_briefs`、`degrade_messages`。

### §6.3 现有键的迁移去向

| 现有键 | 现在怎么被消费 | 迁移后 |
|---|---|---|
| `summary` | `_normalize` 头部文本；`_tool_brief` 第二档 | 拆成 `headline`（一句话结论）与 `body`（正文），由工具自行分配 |
| `model_hint` | `_normalize` 头部文本，与 `summary` 并列 | `agent_hint`，`x-role: hint`，渲染在最后，不进历史 |
| `hint` | 没有特殊处理，被 JSON 包裹 | 同上，`agent_hint` |
| `__context__` | `ToolResult.context_brief`，历史第一档 | 删除，历史统一取 `headline` |
| `__image__` / `__image_mime__` | `ToolResult.image_b64/mime`，进多模态 | `images[].base64` / `mime`，`x-role: images` |
| 其余数据字段 | `_normalize` JSON 化 | `data`，`x-role: data` |
| `error`（字符串） | `ToolResult.error`，渲染成 `[FAIL name]` | `error: {code, message}`，见 §6.4 |
| `success` | 18 处：`food` 6、`game` 12 | 统一为 `ok`，`x-role: suppress`，只用于历史兜底文案 |
| `ok` | 30 处：`bilibili` 27、`knowledge` 3 | 保留，与 `success` 同义合并 |
| `reason` | `bilibili` 的失败原因（`not_logged_in` / `quota` / `filtered`） | 映射为 `error.code`，`message` 取现有文案 |
| `speech` / `suppress_speech` | `gamebase._emit_speech` 在返回前就 pop 掉，到不了 executor | 工具内部消费，框架不感知、不进 schema。通用说话通道是 `arguments.aside` |

映射表是迁移的检查清单：阶段 5 删除 `_normalize` 前，每个现有键都必须有明确去向，否则视为未完成。

### §6.4 异常路径

`ToolExecutor` 的失败分支统一产出规范错误，不让工具自己造文案：

| 来源 | `code` | `message` |
|---|---|---|
| 未知工具 | `unknown_tool` | `unknown tool: <name>` |
| 参数校验失败 | `bad_arguments` | 原校验信息 |
| 超时 | `timeout` | `工具执行超时（30s）` |
| handler 抛异常 | `handler_error` | 异常字符串 |
| 输出不符合 schema | `output_violation` | 首个违约路径 |

模型看到的是 `[FAIL name] timeout: 工具执行超时（30s）`，历史记录是 `操作失败`。`code` 稳定，可用于观测统计。

### §6.5 文件动作

`add_file_action` 的 handler 返回 `{"ok", "summary"}`，由文件气泡直接调用，不经过 LLM 工具循环，也不进 `session_events`。

它不过 `ToolMethod`，因此没有 `output` 声明。阶段 5 删除 `_normalize` 后，这条路径会失去渲染器。处理：**补一个内置默认定义** `FILE_ACTION_OUTPUT`，把 `summary` 视为 `headline`、`ok` 视为 `ok`，由 `FormatFileActionResult` 适配。这样 `_normalize` 可以真正删除，文件动作的播报文案也不依赖旧函数。

不做「豁免并保留 `_normalize`」：那会让旧渲染函数永远删不掉，而它正是本次要清理的对象。

## §7 迁移

| 阶段 | 内容 | 出口条件 |
|---|---|---|
| 1 | 框架层：`ToolOutputDefinition`、`render_turn`、`render_record`、`validate(warn)`、`add_method(output=)` | 未声明 output 的工具行为不变；框架单测全绿 |
| 2 | 读取类：`browser`、`file_ops`、`knowledge` | 三个工具的返回无 `summary` / `__context__`；历史记录可读 |
| 3 | 动作类：`bilibili`、`timer`、`todo`、`weather`、`system_monitor`、`web_search`；`success` 统一为 `ok`，`bilibili.reason` 映射为 `error.code` | 声明覆盖率 ≥80%；legacy 路径调用占比 ≤20% |
| 4 | 元工具：`tool_search`、`recall`、`food`、`game` | 声明覆盖率 100%；`model_hint` 全部改为 `agent_hint` |
| 5 | 删 `_normalize`、`__context__`、`_tool_brief` 回退链；`validate` 切 `strict`；文件动作切 `FILE_ACTION_OUTPUT` | 连续 7 天 `output_violation` ≤1%/千次调用 |

兼容：`add_method(output=None)` 走 legacy，同一个方法只走一条路径。第三方工具不声明也能用，但没有 schema 校验。

## §8 观测

每次工具调用记一行结构化日志：

| 字段 | 说明 |
|---|---|
| `tool` | 完整调用名 |
| `path` | `declared` / `legacy` |
| `violations` | `validate` 返回的违约路径数 |
| `record_len` | 历史记录长度 |
| `block_count` | 当轮投影的块数 |
| `error_code` | 失败时的稳定标识，空表示成功 |
| `budget_truncated` | 是否触发 §5.2 的裁剪 |

业务指标：

- 按工具的 `output_violation` 计数与违约路径分布，决定能否切 `strict`
- `legacy` 路径调用占比，作为阶段 3 与阶段 4 的出口条件
- `headline` 被截断的次数，超过阈值说明工具有人在写长句

## §9 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| `additionalProperties: false` 判死动态返回 | 可读结果被替换成「不符合约定」 | 迁移期只 warn，未声明字段归入 `data`；阶段 5 才 strict |
| 多行 `summary` 拆成 `headline` + `body` 时丢信息 | 历史记录变短，模型重复调用 | 拆分由工具作者做，迁移期用真实日志回归对比 |
| `model_hint` 改 `agent_hint` 漏改 | `game` 的下一步指令消失，模型卡住 | 阶段 4 用映射表逐条核对，21 处全列 |
| 渲染顺序与 schema 声明顺序不一致 | 模型读到的结构与预期不同 | `render_turn` 只遍历 `schema.properties`，不遍历返回 dict |
| 当轮文本超预算 | 结论被截断 | 裁剪顺序固定为 data → body，headline 与 hint 不截 |
| artifact 与当轮视图不一致 | `read_artifact` 拿到别的内容 | §5.4 规定 artifact 取 `render_turn` 的纯文本 |

## §10 验收

单测：

1. `validate` 覆盖 `type` / `required` / `enum` / 嵌套 `items` / `maxLength` / `additionalProperties` 六个分支
2. `render_turn` 按 schema 声明顺序输出，与返回 dict 的键序无关
3. 未声明字段归入 `data` 渲染，`warn` 模式下不判违约
4. `suppress` 字段在任何投影里都不出现
5. `images` 产出的 `ImageBlock` 数量与 `caption` 正确
6. `render_record` 截断到 120 字符，且保留 `has_next` 提示
7. 未声明 output 的方法仍走 legacy，行为与今天一致
8. `record` 写入事件后，投影层不再调用回退链
9. 超预算时先丢 `data` 再丢 `body`，`headline` 完整保留
10. `headline` 超长：`warn` 模式记录违约但照常渲染并截断，不降级
11. 异常四类分别产出稳定 `code`
12. 静态解析：所有 `add_method(output=...)` 的 schema 都能从 AST 取出，不接受函数调用形式的实参
13. 文件动作结果走 `FILE_ACTION_OUTPUT`，渲染文案与旧 `_normalize` 一致

端到端：

- `weather__get_current`：当轮能看到温度与预报正文，历史记录是「深圳 晴 29.6 度」
- `browser__read_url`：第二页请求时 `pagination` 提示正确，历史记录是「读取网页 X（第 2/3 页，3000 字符）」
- `tool_search__list_groups`：历史记录不再是「操作已完成」
- `game__play`：`agent_hint` 渲染在文本最后，历史记录只有 `headline`
- 构造一个缺 `headline` 的返回，确认 `warn` 模式用 `data` 首字段兜底并记 warning
- 构造一个多余字段的返回，确认 `warn` 模式把它渲染进 `data`，`strict` 模式判违约
- `bilibili` 未登录时 `error_code` 为 `not_logged_in`，模型看到的是 `[FAIL bilibili__like] not_logged_in: ...`

## §11 关键决策

### §11.1 为什么用 `x-role` 而不是固定字段名

固定字段名会把所有工具压进同一个信封，无法表达「天气有 temperature、文件有 chars_read」。`x-role` 只声明投影角色，字段名照样自由，render 不需要猜。

### §11.2 为什么两条投影而不是一条

当轮模型需要完整正文与数据，历史只需要结论。用同一份规范值 + 两个纯函数，避免 `_normalize` 与 `_tool_brief` 两套规则长期漂移。

### §11.3 为什么自带轻量校验且不解析 `$ref`

`jsonschema` 会给所有工具加运行时依赖，而实际用到的关键字只有六个。片段用 Python 常量展开，schema 保持纯数据，校验器不需要引用解析。

### §11.4 为什么不保留 `__context__`

它本质是「历史记录」的专用通道，而历史记录现在由 `x-role: headline` 声明。保留两套会让工具作者继续二选一，正是当前混乱的根源。

### §11.5 为什么迁移期只 warn

`game` / `system_monitor` / `todo` 的返回键随分支变化，`additionalProperties: false` 会在这些工具上把可读结果换成一句废话。先靠观测拿到违约率，再决定是否 strict，比一次性切换安全。

### §11.6 为什么 `hint` 单独成角色

`model_hint` 的语义是「下一步该调什么」，它不该被当成结果正文的一部分，也不该进历史。单独成角色后，渲染位置固定在最后，历史投影天然不受影响。

### §11.7 为什么 `speech` 不进 `meta`

`gamebase._emit_speech` 在返回给 executor 之前就把 `speech` 与 `suppress_speech` pop 掉了，它们根本到不了 `ToolResult`。把它们塞进 `meta` 会逼 executor 在 `validate` 前专门剥离 `meta` 键，否则 `additionalProperties: false` 会把它们判成违约。凭空多一条框架通道，只为搬运两个工具内部信号，不划算。

通用说话通道已经是 `arguments.aside`，工具要发声用它，不需要第二条路。

### §11.8 为什么 schema 必须是字面量

`gen_docs` 与 `load_tools` 只做 AST 解析，不运行时导入工具：导入会触发工具私有 `requirements.txt` 的 pip 安装，文档生成不该有这种副作用。因此 `output=` 的实参必须能在语法树里读出来，函数调用会让字段表静默缺列。跨文件复用只能靠 `**常量名` 展开一层。

代价是 schema 里不能做计算，换来的是 `gen_docs.py --check` 能真正守住文档与代码的一致性。
