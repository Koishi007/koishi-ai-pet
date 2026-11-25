# 模型供应商抽象设计

> 在 `pet/brain` 内定义供应商边界：内部只流通中性消息、流式事件与错误类型，OpenAI 与 Gemini 各自负责编码与异常映射，调用方不出现任何供应商 SDK 的类型。

## §1 边界要覆盖的职责

供应商边界的成立条件是下面每一项都由 provider 承担，调用方只看中性结构：

| 职责 | 中性出口 | 落在哪里 |
|---|---|---|
| 客户端构建 | `LLMConnection` + `build_provider` | `pet/brain/providers/registry.py` |
| 工具被拒判据 | `tool_channel.is_tool_rejection` 的词表 | `pet/brain/tool_channel.py` |
| 请求发出 | `complete()` / `stream()` | 各 provider 模块 |
| 异常分类 | `ProviderError` 族 | `pet/brain/llm_errors.py` |
| 工具通道探测 | `ProviderBadRequest.body` 文本 | `pet/brain/tool_channel.py` 的 `is_tool_rejection` |
| 思考降级 | provider 实例字段 | 各 provider 模块 |
| 非流式解析 | `Completion` | `pet/brain/behavior.py`、`pet/brain/summary.py` |
| 流式解析 | `StreamEvent` | `pet/brain/parsing.py` 的 `collect_stream` |
| 图片块编码 | `{"type": "image", "mime", "data"}` | `pet/brain/context_builder.py`、`pet/brain/tool_loop.py` |
| 图片块统计 | 同一中性片段 | `pet/brain/llm_gateway.py` 的 `log_prompt_size` |
| 工具声明 | `to_tool_specs` 的中性描述 | `pet/tools/registry.py` |
| 工具历史编码 | `{"id", "name", "arguments"}` | `pet/brain/projection.py` 的 `_render_group` |
| 工具历史读取 | `call["arguments"]` | `pet/brain/projection.py` 的 `_message_aside` |
| 记忆汇总 | 与对话共用 `build_provider` | `pet/brain/memory_merge.py` |
| 嵌入 | `embed(texts, dim)` | `pet/brain/embedding_client.py` |
| 界面连通性测试 | `complete()` | `pet/ui/settings_workers.py` |
| 界面模型列表 | `list_models()` | `pet/ui/settings_workers.py`、`pet/ui/settings_actions.py` |

摘要走 `pet/brain/llm_gateway.py`，不单独建客户端。

三条约束：

1. 新增通道只改 provider 模块、登记表一行、`_KEY_META` 的配置键、一组用例，解析与编排不动。
2. 降级策略只依赖中性错误族，不看某一家 SDK 的异常类型与 `body` 形状。
3. 测试替身实现 `ChatProvider` 即可，不伪造 SDK 形状。

## §2 目标

- 决策链路只依赖 `pet/brain` 定义的中性类型：消息、流式事件、补全结果、错误。
- 新增一个供应商时，改动集中在四处：新建 provider 模块、在 `PROVIDER_SPECS` 登记一行、在 `_KEY_META` 增补该行声明的配置键、补一组用例。设置界面的选项与显隐由登记行派生。
- 能力差异用声明表达，不用布尔散字段，也不靠运行期试探发现。
- 首选与备选方案可以各自选择供应商，回退时模型与通道一起切换。
- 降级能力保持不退化：思考参数被拒后降级重试、工具通道逐档降级、重试分类、流式看门狗、总超时。
- 供应商 SDK 在导入期不加载，只在通道构造时导入。

## §3 非目标

- 不改提示词结构、上下文预算与输出预算的配额公式（`pet/brain/budget.py`）。
- 不改解析协议：`Summary` / `Speech` / `Action` 等行标签与 `BehaviorOutput` 不变。
- 不引入 asyncio。同步迭代接口与 `docs/architecture.md` §2 的线程模型一致。
- 不接入 Vertex AI。它需要 GCP 项目、区域与服务账号或 ADC，认证模型与 AI Studio 的 API Key 不同，留作后续通道。
- 不接入 Antigravity 订阅通道。该通道经本地 CLI 或独立 SDK 消耗会员额度，认证与计费模型与 HTTP 通道不同。
- 不改事件日志与投影的存储形态：`session_events` 表结构与 `ASSISTANT_TURN_VERSION` 不变。
- 不做多供应商并发与负载均衡。
- 不做能力的运行期自动探测：能力由通道声明，例外交给既有降级路径。
- 不做第三方 provider 的插件化热加载：新增通道仍需改代码并在 `PROVIDER_SPECS` 登记。

`budget.py` 的边界：配额公式不动，provider 不取配额数值；`thinking_active()` 仍由网关与 provider 共用，见 §5.2。Gemini 的思考长度分布与现有模型不同，是否出现 `length` 要在真实 key 下复测；若复测结论是配额需要调整，那属于独立改动，改的是 `_MODES` 的数值。

## §4 供应商边界

### §4.1 协议

`pet/brain/providers/__init__.py` 定义 `Capabilities`、`LLMConnection` 与对话协议：

```python
@dataclass(frozen=True)
class Capabilities:
    """通道能力声明。真源在 provider，不在配置里，也不靠运行期试探。"""
    vision: bool = False        # 接受图片片段
    native_tools: bool = False  # 接受 tools 参数并可能返回 tool_calls
    thinking: bool = False      # 接受思考开关
    embedding: bool = False     # 有嵌入模型
    model_list: bool = False    # 能列模型


class ChatProvider(Protocol):
    name: str                 # "openai" | "gemini"
    model: str
    capabilities: Capabilities

    def complete(self, messages: list[dict], *, max_tokens: int, tools: list[dict] | None,
                 thinking: bool | None, temperature: float | None) -> Completion: ...

    def stream(self, messages: list[dict], *, max_tokens: int, tools: list[dict] | None,
               thinking: bool | None, temperature: float | None) -> Iterator[StreamEvent]: ...

    def thinking_rejected(self, exc: ProviderError) -> bool: ...

    def reasoning_effort_rejected(self) -> bool: ...

    def close(self) -> None: ...
```

协议只保留所有通道都必须实现的方法。`embed()` 与 `list_models()` 不设协议，由 `capabilities.embedding` / `capabilities.model_list` 声明，调用方先看声明再调方法：没有嵌入模型的通道不写抛异常的空实现，也不需要额外的类型判断。

**`stream()` 的建连时机**：调用即发起请求并等待响应头，返回值同时满足两点：`iter()` 直接产出已到达的事件，且带 `close()`。

这条是硬约束。`pet/brain/llm_retry.py` 的 `_create_with_watchdog` 把 `create_stream_fn()` 放在后台线程计时，用「多久拿到返回值」度量响应头等待；`pet/brain/parsing.py` 的 `iter_stream_with_timeout` 在 finally 里调用流的 `close()` 释放连接。若 `stream()` 返回普通 generator，调用只创建生成器、不发请求，看门狗测不到等待，放弃时的 `_safe_close` 也关不掉连接。

实现方式：provider 在 `stream()` 内先拿到底层响应对象，再用一个持有该对象的迭代器包装。禁止用 `yield` 写函数体直接返回 generator。

### §4.2 能力声明与消费方

`capabilities` 是能力差异的唯一入口，不使用散落的布尔字段，也不靠「试一次会不会 400」发现。两个通道的声明：

| 能力 | openai | gemini | 消费方 |
|---|---|---|---|
| `vision` | 真 | 真 | `LLMClient.has_vision`、`tool_loop` 是否附图片 |
| `native_tools` | 真 | 真 | `tool_channel.channel_for()` 的初始档位 |
| `thinking` | 真，但端点可能拒绝，仍由 `thinking_rejected()` 兜底 | 真 | `thinking_active()` 之后是否下发思考字段 |
| `embedding` | 真 | 假 | 嵌入连接构建的条件。嵌入固定走 OpenAI 兼容端点，Gemini 通道不声明该能力 |
| `model_list` | 真 | 真 | 「获取列表」按钮 |

能力声明与降级的关系：声明为真但实际被拒（例如某个第三方 OpenAI 兼容端点不支持思考），仍由 provider 记住拒因后不再下发。声明决定「默认做什么」，降级处理「例外」。

能力声明与用户开关是两件事：`vision` 表示通道能做，`VISION_ENABLED` 表示用户要做。最终是否附图取二者的与，因此 `LLMClient.has_vision` 的实现是 `provider.capabilities.vision and config.VISION_ENABLED`。

`native_tools=False` 时 `tool_channel` 的初始档位取 `CHANNEL_ORDER` 中第一个非 `native` 的档，即 `user-note`。用户显式设置 `LLM_TOOL_ROLE_MODE` 时以该配置为准，能力声明只影响 `auto` 档。当前两个通道都声明为真，该分支只有在接入纯文本通道时才会命中。

### §4.3 中性类型

`pet/brain/llm_types.py`，全部为不可变 dataclass：

| 类型 | 字段 | 用途 |
|---|---|---|
| `ToolCall` | `id`、`name`、`arguments`（JSON 字符串） | 一次完整工具调用 |
| `ToolCallDelta` | `index`、`id`、`name`、`arguments`（追加语义）、`complete` | 流式增量片段 |
| `Usage` | `prompt_tokens`、`completion_tokens`、`total_tokens` | 用量 |
| `Completion` | `content`、`tool_calls`、`finish_reason`、`usage` | 非流式结果 |
| `StreamEvent` | `content`、`reasoning`、`tool_call`、`finish_reason`、`usage` | 流式增量 |

`StreamEvent` 各字段默认全空，一个事件只承载一类事实：正文增量、思考增量、工具调用增量、结束（带 `finish_reason` 与可选 `usage`）。

`finish_reason` 统一收敛为四个值：`stop`、`length`、`content_filter`、`tool_calls`。修复重试只区分 `length` 与其余。`tool_calls` 只在供应商原生返回该值时产出：Gemini 在返回函数调用时仍报 `STOP`，因此它只描述结束原因，判断本回合有无工具调用要看 `tool_calls` 字段。

### §4.4 错误族

`pet/brain/llm_errors.py`，一个基类加八个子类：

```python
class ProviderError(Exception):
    status_code: int | None
    body: str

class ProviderAuthError(ProviderError)       # 401 / 403
class ProviderBadRequest(ProviderError)      # 400 / 422
class ProviderNotFound(ProviderError)        # 404
class ProviderRateLimit(ProviderError)       # 429
class ProviderServerError(ProviderError)     # 5xx
class ProviderTimeout(ProviderError)         # 连接或读取超时
class ProviderConnection(ProviderError)      # 传输层失败
class ProviderUnavailable(ProviderError)     # 通道名未知或依赖缺失
```

`ProviderUnavailable` 不来自任何 SDK，由 `build_provider` 抛出：通道名不在登记表内，或该通道声明的依赖导入失败。消息里带通道名与依赖名，调用方据此提示或降级。

异常映射表：

| 中性错误 | OpenAI SDK | google-genai |
|---|---|---|
| `ProviderAuthError` | `AuthenticationError`、`PermissionDeniedError` | `ClientError` 且 `code` 为 401/403 |
| `ProviderBadRequest` | `BadRequestError`、`UnprocessableEntityError` | `ClientError` 且 `code` 为 400/422 |

| `ProviderNotFound` | `NotFoundError` | `ClientError` 且 `code` 为 404 |
| `ProviderRateLimit` | `RateLimitError` | `ClientError` 且 `code` 为 429 |
| `ProviderServerError` | `InternalServerError`、`APIStatusError` 且 5xx | `ServerError` 且 `code` 为 5xx |
| `ProviderTimeout` | `APITimeoutError` | 底层 HTTP 客户端超时异常 |
| `ProviderConnection` | `APIConnectionError` | 底层 HTTP 客户端传输异常 |

映射函数与 SDK 版本绑定，随 provider 一起维护。`ProviderError.body` 统一转成 `str`：SDK 给的可能是 dict，下游按文本匹配前必须先归一。

**降级责任划分**：参数被端点拒绝时，降级一律在 provider 内部完成，调用方只看到最终结果或错误。两个通道都遵循这条：

| 拒绝内容 | 判据 | 降级动作 | 状态存放 |
|---|---|---|---|
| 思考参数 | `thinking_rejected(exc)` | 去掉该字段重发一次 | `_thinking_unsupported` |
| 推理强度 | 错误文本含 `effort`，见 [通用推理强度设计](2026-11-25-reasoning-effort-design.md) §4.5 | 去掉该字段重发一次 | `_effort_unsupported` |
| 工具协议 | `tool_channel.is_tool_rejection(exc)` | 由网关降一档通道并改写消息 | `tool_channel._verified` |

网关只处理「工具协议整体不可用」这一层，因为它要改写整个消息体；参数级的重发不需要改消息，留在 provider 里。UI 与编排层不写降级副本。`reasoning_effort_rejected()` 供界面在降级发生后向用户说明原因。

**依赖**：`google-genai` 是必装依赖（`pyproject.toml` 的 `dependencies`），但仍只在函数内导入，避免模块级加载 SDK。它 3.6.1 的默认 HTTP 客户端是 `httpx2`，`httpx` 已不在默认依赖里。因此 Gemini provider 的超时与传输异常判断针对 `httpx2`，不能复用 `pet/brain/llm_retry.py` 的 `httpx.*` 分类；重试分类只判中性错误，两个 provider 各自完成映射。

## §5 各通道映射

### §5.1 编码映射

| 关注点 | openai | gemini |
|---|---|---|
| 客户端 | `OpenAI(base_url=LLM_URL, api_key=LLM_KEY, timeout=...)` | `genai.Client(api_key=LLM_KEY, http_options=HttpOptions(timeout=<毫秒>))` |
| 非流式 | `chat.completions.create` | `models.generate_content` |
| 流式 | `chat.completions.create(stream=True)` | `models.generate_content_stream` |
| system 段 | `messages` 首条 | `config.system_instruction` |
| 图片 | `image_url` 的 data URI | `inline_data` 的 `mime_type` + `data` |
| 输出上限 | `max_tokens` | `config.max_output_tokens` |
| 温度 | `temperature` | `config.temperature` |
| 思考 | `extra_body.thinking.type` | `config.thinking_config` |
| 结束原因 | `stop` / `length` / `content_filter` / `tool_calls` | 见 §5.3 |
| 用量 | `usage.prompt_tokens` 等 | `usage_metadata.prompt_token_count` / `candidates_token_count` / `total_token_count` |
| 嵌入 | `embeddings.create(dimensions=)` | `models.embed_content(config=EmbedContentConfig(output_dimensionality=...))`，取 `embeddings[].values` |
| 思考正文 | `delta.reasoning_content` | `Part.thought=True` 的正文片 |
| 缓存标记 | `cache_control` 原样透传 | 丢弃 |
| 超时 | `httpx.Timeout(read=LLM_TIMEOUT)` | `HttpOptions(timeout=<毫秒>)`，需把秒换算成毫秒 |

### §5.2 思考参数与推理强度

协议签名是 `thinking: bool | None`，只表达两件事：`None` 跟随 `LLM_THINKING_DISABLED`，显式 `True` / `False` 覆盖全局开关；`True` 表示允许思考，`False` 表示关闭思考。判定复用 `pet/brain/budget.py` 的 `thinking_active()`，保证预算与请求参数对「这次带不带思考」的结论一致。

推理强度按方案配置：`LLM_REASONING_EFFORT(S)` 与 `LLM_REASONING_EFFORT(S)_ALT`，存中性档位 `minimal` / `low` / `medium` / `high` / `xhigh` / `max`，空串表示不下发。备选把地址、Key 与模型名填齐时独立取值，缺任一项时档位跟随首选。通道声明、协议值映射与降级阶梯见 [通用推理强度设计](2026-11-25-reasoning-effort-design.md)。

档位只在 `thinking_active()` 为真时下发：`openai` 通道写进 `extra_body.reasoning_effort`，`gemini` 通道写进 `ThinkingConfig(thinking_level=...)`。界面选项由通道 `reasoning_efforts`、模型预设与端点清单共同收窄，不手写。

档位不按模型名推断代际：通道声明了就下发，端点拒绝时同一 provider 实例记录拒因后不再下发，本次请求按默认档位重发一次。设置页的连通性测试遇到该错误时同样降级重试，并在结果里告知用户。

关闭思考时 `gemini` 通道下发 `thinking_budget=0`、`openai` 通道下发 `thinking.type: disabled`，两者都不带档位：省略字段等于交给模型默认，与「用户要求关闭」相反。

### §5.3 结束原因对照

`google-genai` 3.6.1 的 `FinishReason` 取值与中性四值的对应：

| Gemini `FinishReason` | 中性值 | 说明 |
|---|---|---|
| `STOP` | `stop` | 正常结束 |
| `MAX_TOKENS` | `length` | 触发修复轮关思考，见 `_thinking_for_repair` |
| `SAFETY`、`RECITATION`、`BLOCKLIST`、`PROHIBITED_CONTENT`、`SPII`、`IMAGE_SAFETY` | `content_filter` | 内容侧阻断 |
| `MALFORMED_FUNCTION_CALL`、`UNEXPECTED_TOOL_CALL`、`TOO_MANY_TOOL_CALLS` | `content_filter` | 工具调用不可用，与内容阻断同属「本次输出不可采信」。归入该值而不归 `stop`，是为了让日志区分「模型正常说完」与「模型产出了废调用」 |
| `LANGUAGE`、`OTHER`、`FINISH_REASON_UNSPECIFIED`、缺失 | `stop` | 无额外语义 |

归到 `content_filter` 的三类工具调用失败会触发既有的空响应路径：解析不到字段时记 `failed_turn` 并走修复轮，与内容阻断的表现一致。修复轮是否重试由 `retry_if_incomplete` 决定，不看结束原因，因此该归类不改变重试次数。

## §6 消息与工具编码

### §6.1 中性消息形态

内部消息保持 dict，不带任何供应商的嵌套：

| 角色 | 形态 |
|---|---|
| system | `{"role": "system", "content": str}` |
| user | `{"role": "user", "content": str 或 list[part]}` |
| assistant | `{"role": "assistant", "content": str 或 None, "tool_calls": [{"id", "name", "arguments"}]}` |
| tool | `{"role": "tool", "tool_call_id": str, "content": str}` |

用户段的内容片段只有两种：

```python
{"type": "text", "text": "..."}
{"type": "image", "mime": "image/jpeg", "data": "<base64>"}
```

文本片段允许携带供应商扩展键，当前只有 `cache_control`：`Behavior._apply_cache_control` 在 `LLM_CACHE_PROMPT` 启用时给 system 段加上该键。中性形态保留，由 provider 决定是否透传：OpenAI 通道原样发出，Gemini 通道丢弃。

### §6.2 构造点与读取点

形态同时涉及写入与读取两侧，漏一处都是静默错误：

| 侧 | 位置 | 约定 |
|---|---|---|
| 构造 | `pet/brain/context_builder.py` 三处 | 图片片段为 `{"type": "image", "mime", "data"}` |
| 构造 | `pet/brain/tool_loop.py` | 同上 |
| 构造 | `pet/brain/projection.py` 的 `_render_group` | `tool_calls` 只有 `id` / `name` / `arguments` |
| 读取 | `pet/brain/projection.py` 的 `_message_aside` | 读 `call["arguments"]`，否则工具降级后 aside 丢失 |
| 读取 | `pet/brain/llm_gateway.py` 的 `log_prompt_size` | 读 `{"type": "image"}` 的 `mime` 与 `data` 长度，否则图片统计归零 |
| 读取 | `pet/brain/projection.py` 的 `degrade_messages` | 判据不变，取值路径同上 |

### §6.3 工具声明

`pet/tools/registry.py` 的 `to_tool_specs` 产出中性描述：

```python
{"name": "timer__set", "description": "...", "parameters": {"type": "object", "properties": {...}, "required": [...]}}
```

- OpenAI 编码：外层补 `{"type": "function", "function": spec}`。
- Gemini 编码：`types.FunctionDeclaration(name=..., description=..., parameters_json_schema=...)` 包进 `types.Tool(function_declarations=[...])`。用 `parameters_json_schema` 而非 `parameters`，后者只接受 Gemini 的 Schema 子集。
- 参数表里 `default`、`enum`、`items` 原样透传。

### §6.4 工具调用回放

| 关注点 | OpenAI | Gemini |
|---|---|---|
| assistant 工具段 | `tool_calls[].function` | `Part(function_call=FunctionCall(name, args))` |
| 工具结果 | `{"role": "tool", "tool_call_id": ...}` | `Content(role="user", parts=[Part(function_response=FunctionResponse(name, response={"result": text}))])` |
| 调用 id | 上游返回 | 上游不返回，provider 按序号生成 `call_<序号>` 并存进事件 |
| 并行调用 | 一个 assistant 消息多个 `tool_calls` | 一个 content 多个 function_call part |

`ToolCallDelta.arguments` 统一为追加语义：消费方按 `index` 累加。Gemini 在流式下以完整 part 返回函数调用，provider 为每个 part 合成一个 `arguments` 为完整 JSON、`id` 非空、`complete=True` 的 `ToolCallDelta`，并把 `index` 分配为互不相同的序号。累计时用 `complete` 判断该序号已完整：为真时用新值替换累计结果，为假时追加。两种到达形态共用同一累加逻辑，同名多次调用按 `index` 分开。

**id 约定**：调用 id 只在同一段历史内唯一。不返回 id 的通道按序号生成，非流式从 `call_0` 起、流式从 `call_1` 起，两条路径不会互相覆盖；同一路径的跨轮之间仍可能重复。因此 Gemini 编码 `function_response` 时按位置取「该 id 在本条 tool 消息之前最近一次出现」的函数名：

```python
calls: dict[str, list[tuple[int, str]]] = {}   # id -> [(消息位置, 函数名)]
for position, msg in enumerate(messages):
    for call in msg.get("tool_calls") or ():
        if call.get("id"):
            calls.setdefault(call["id"], []).append((position, call.get("name", "")))

name = next((n for seen_at, n in reversed(calls.get(call_id, []))
             if seen_at < position), "tool")
```

找不到时回落到 `"tool"`。投影器只需保证调用 id 稳定可复用，与既有传输编码契约一致。

## §7 方案、通道与既有配置

### §7.1 绑定规则

现有实现由四个配置项共同决定「用哪个客户端」：`BRAIN`、`OLLAMA_BASE_URL`、`LLM_ACTIVE_PROFILE`、`LLM_FALLBACK_ENABLED`。本设计不改它们的语义，只补一条绑定规则。

**通道属于方案，不属于全局。** `LLM_PROVIDER` 描述首选方案，`LLM_PROVIDER_ALT` 描述备选方案，与 `LLM_URL` / `LLM_KEY` / `LLM_MODEL` 和 `LLM_URL_ALT` / `LLM_KEY_ALT` / `LLM_MODEL_ALT` 一一对应。`LLM_ACTIVE_PROFILE` 只决定两套方案里哪一套生效，不改变归属。

**备选是整方案生效或整方案沿用。** 三个备选键不逐项回退：地址、Key 与模型名都填了才算独立方案，缺任一项时整个备选方案读首选的三项，通道也沿用 `LLM_PROVIDER`。`LLM_PROVIDER_ALT` 只在备选配齐时生效。

`BRAIN` 保持三档，作为通道之上的总开关：

| `BRAIN` | 行为 | 与通道的关系 |
|---|---|---|
| `local` | 不建 provider，全走本地兜底 | 忽略全部通道配置 |
| `api` | 按 `LLM_PROVIDER` / `LLM_PROVIDER_ALT` 建 provider | 通道在此档生效 |
| `ollama` | 两套方案都建 OpenAI 通道，`url` 取 `OLLAMA_BASE_URL`，`key` 固定为 `ollama`，`model` 留空时回落 `llama3.2` | 通道固定为 `openai`，`LLM_PROVIDER` 被忽略 |

`ollama` 档复用 OpenAI 通道而不新增 provider：Ollama 的服务端就是 OpenAI 兼容接口，区别只在地址与占位 key。

`LLM_FALLBACK_ENABLED` 的位置不变：仍由 `pet/brain/behavior.py` 的 `_on_llm_retry` 在调用 `activate_fallback()` 前判断，`LLMClient` 不感知该开关。

### §7.2 工厂与连接参数

`build_provider` 定义在 `pet/brain/providers/registry.py`。

```python
@dataclass(frozen=True)
class LLMConnection:
    provider: str            # "openai" | "gemini"
    key: str
    url: str
    model: str

# 按 PROVIDER_SPECS[name].target 延迟解析并分发
def build_provider(connection: LLMConnection, timeout: float | None = None,
                   reasoning_effort: str = "") -> ChatProvider

# 一次性实例：退出时关闭
@contextmanager
def temporary_provider(...) -> Iterator[ChatProvider]
```

未知通道与依赖缺失都抛 `ProviderUnavailable`，不返回 `None`：调用方要么降级、要么把可读原因报到界面，两条路径都不需要再猜「为什么没建成」。生命周期长于一次调用的调用方用 `build_provider` 并自己持有实例；一次性使用（记忆汇总、模型列表）用 `temporary_provider`，不写 `try/finally`。

方案里的 provider 名留空时沿用首选方案的 provider；备选连接没填齐时通道也沿用首选。

组装规则：`BRAIN=ollama` 时两套方案都组装成 `LLMConnection(provider="openai", url=OLLAMA_BASE_URL, key="ollama", model=model or "llama3.2")`；`BRAIN=api` 时按各自方案的通道键组装；`BRAIN=local` 时不组装。

**回退判据**：`activate_fallback` 用整个 `LLMConnection` 比较，两套配置完全相同才拒绝切换。只比 provider 名会让「首选与备选同为 openai、key 或模型不同」这一主要用法失去回退能力。

`capabilities.vision` 的真源在 provider；`LLMClient.has_vision` 读取当前生效 provider 的该属性并与 `config.VISION_ENABLED` 取与。

`LLMClient` 暴露两个能力查询，供上层按能力分支：

| 方法 | 用途 |
|---|---|
| `provider_name(profile)` | 指定方案实际使用的通道名，供工具通道词表选择 |
| `capabilities` | 当前生效 provider 的 `Capabilities` |

嵌入连接不走上述查询：`EmbeddingClient` 固定按 OpenAI 兼容端点构建，与对话通道无关。

### §7.3 通道登记表

`pet/brain/providers/registry.py` 用一张表登记全部通道：

```python
@dataclass(frozen=True)   # 概念形态；实际是字面量字典，见下方导入方向约束
class ProviderSpec:
    name: str                          # "openai"
    label: str                         # 设置界面下拉的显示名
    target: tuple[str, str]            # (模块名, 类名)，延迟解析
    config_keys: tuple[str, ...]       # 随方案换算的配置键，按界面顺序
    shared_keys: tuple[str, ...]       # 两套方案共用的配置键
    optional_deps: tuple[str, ...]     # 例如 ("google-genai>=3.6",)
    url_hint: str                      # 设置界面地址框的占位示例
```

派生关系：

| 消费方 | 从表里取什么 |
|---|---|
| 设置界面的通道下拉 | `channel_choices()` 返回 `(label, name)` |
| 该通道专属配置项的显隐 | `config_keys()` / `config_keys_for()` |
| 地址框占位符 | `url_placeholder()` 取 `url_hint` |
| 缺依赖时的报错文案 | `optional_deps` |
| `build_provider` 的分发 | `target` |
| 通道名归一化 | `normalize_provider()`，未知值原样返回 |

**导入方向约束**：`PROVIDER_SPECS` 是字面量字典而非 dataclass 实例，因为模块级构造会被 `ARCH009` 判为导入期副作用。registry 模块级只依赖标准库：不 import `pet.config`，也不 import 具体 provider 模块（它们会连带 import SDK 与 `config`）。provider 类由 `_load()` 在首次调用时 `importlib.import_module`。这条与 `docs/architecture.md` §11 第 15 条一致：延迟 import 用于打断环或推迟重依赖，这里两者兼有。`tests/test_architecture_contracts.py` 的 `ARCH008`、`ARCH009` 与 `tests/test_import_smoke.py` 覆盖。

因此配置面存在两处登记，各管一件事：`_KEY_META` 是配置项真源（类型、默认值、描述），`PROVIDER_SPECS` 只管「哪个通道读哪些键」。两张表的一致性由 `tests/test_provider_registry.py` 守住，不靠评审。

通道枚举也手写在 `_KEY_META` 里，不从登记表派生：`pet/config.py` 一旦 import `pet.brain.providers.registry`，就会连带加载 `pet.brain` 包的 `__init__`（它 import `behavior` 与 `llm_client`），后者又 import `config`，形成导入环。`test_channel_enum_matches_registry` 守住枚举与 `PROVIDER_SPECS` 一致。

新增通道的完整改动面：新建 provider 模块、在 `PROVIDER_SPECS` 加一行、在 `_KEY_META` 增补该行声明的键、补一组用例。通道下拉的选项与专属键的显隐随之自动生效；界面控件本身仍手写，因为布局需要人工决定。

## §8 状态归属与生命周期

| 状态 | 归属 |
|---|---|
| `_thinking_unsupported`、`_effort_unsupported` | provider 实例字段。端点拒绝后只影响该实例，`rebuild()` 重建客户端时随之清空 |
| 工具通道降档缓存 | `LLMClient.tool_channel` 持有的 `ToolChannelState`，键为 `(provider 名, model)`，回退后键随之改变；`rebuild()` 调用 `reset()` 清空 |
| 当前生效方案 | `llm_client._effective`。每次请求从 `_effective` 取 provider 实例，回退后立即生效 |

生命周期：`LLMClient._build()` 为两套方案各建一个 provider 实例并持有；`rebuild()` 先对旧实例调用 `close()`，再构建新实例并调用 `tool_channel.reset()`；`activate_fallback` 与 `reset_effective` 只切换 `_effective`，不重建实例；`close()` 释放两套方案的底层客户端，OpenAI 通道关闭连接池，Gemini 通道调用 SDK 的关闭方法，SDK 无该方法时不做事。退出流程由 `PetAgent.stop()` 经 `Behavior.close_llm()` 调用。

一次性 provider 必须关闭，两种写法二选一：用 `temporary_provider`，或在把实例交给后台 worker 前确认 `probes.start()` 返回真，否则由调用方关闭。

## §9 网关的两个入口

`pet/brain/llm_gateway.py` 只暴露两个请求入口，名字与 `ChatProvider` 的方法一一对应：

| 入口 | 对应 provider 方法 | 额外承担 |
|---|---|---|
| `complete()` | `complete()` | 非流式重试、备选方案切换、工具通道降档、用量日志 |
| `stream()` | `stream()` | 建流看门狗、建流阶段重试、工具通道降档、调用计数 |

两者签名相同：`(messages, max_tokens, tools, thinking, temperature)`。`stream()` 返回的迭代器由解析器消费，用量与结束原因随 `StreamEvent` 到达，不经网关。

## §10 流式、看门狗与用量

- `pet/brain/parsing.py` 的 `collect_stream` 遍历 `StreamEvent`。单条事件可能同时带正文与结束原因，用量与结束原因在同一轮循环里各自处理。
- `iter_stream_with_timeout` 只负责超时与取消，判定逻辑不变，仍然在 finally 里调用流的 `close()`。
- 建流看门狗仍在 `pet/brain/llm_retry.py`，`create_stream_fn` 由 provider 的 `stream()` 提供。§4.1 的建连时机约束是该看门狗生效的前提。
- 用量按事件累积覆盖：OpenAI 的 usage-only chunk 与 Gemini 每块携带的 `usage_metadata` 都收敛为 `StreamEvent.usage`。Gemini 的值本身是累计值，逐块覆盖即可；OpenAI 只在末块出现，覆盖语义等价。
- `Behavior` 的 `_last_finish_reason` 与 `_note_budget_truncation` 依赖中性 `finish_reason`，逻辑不改。

## §11 配置面

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `LLM_PROVIDER` | str | `openai` | 首选通道，取值 `openai` / `gemini` |
| `LLM_PROVIDER_ALT` | str | `""` | 备选通道，留空沿用首选 |
| `LLM_REASONING_EFFORT` | str | `""` | 首选推理强度，语义见 §5.2 与 [通用推理强度设计](2026-11-25-reasoning-effort-design.md) |
| `LLM_REASONING_EFFORT_ALT` | str | `""` | 备选推理强度；备选连接未填完整时跟随首选 |
| `LLM_REASONING_EFFORTS` | str | `""` | 首选端点支持的档位，逗号分隔；留空按模型预设。仅 settings.json |
| `LLM_REASONING_EFFORTS_ALT` | str | `""` | 备选端点支持的档位；留空按模型预设。仅 settings.json |

既有键的语义：`LLM_URL` 两个通道都读。`openai` 通道按 OpenAI 兼容端点使用；`gemini` 通道作为 `HttpOptions.base_url`，留空用官方端点，填自建代理时只填根地址。`LLM_KEY` 在 `gemini` 通道填 AI Studio API Key。`EMBEDDING_*` 四项固定走 OpenAI 兼容端点。

Vertex 的三个键（项目、区域、服务账号文件）随该通道一起后置，见 §3 非目标。

### §11.1 配置项的登记方式

| 步骤 | 位置 | 是否手工 |
|---|---|---|
| 声明键的类型、默认值、描述 | `pet/config.py` 的 `_KEY_META` | 手工 |
| 声明该通道读哪些键、界面顺序 | `pet/brain/providers/registry.py` 的 `config_keys` / `shared_keys` | 手工，一行 |
| 声明该通道提供的推理档位 | `pet/brain/providers/registry.py` 的 `reasoning_efforts` | 手工，一行 |
| 声明通道本体的类路径与显示名 | `pet/brain/providers/registry.py` 的 `PROVIDER_SPECS` | 手工，一行 |
| 通道下拉的选项 | `channel_choices()` 派生 | 自动 |
| 该通道专属键的显隐 | `config_keys()` 派生 | 自动 |
| 推理强度下拉的选项 | `reasoning_efforts()` 派生 | 自动 |
| 地址框占位符 | `url_hint` 派生 | 自动 |
| 参考文档 | `scripts/gen_docs.py` | 自动 |

`LLM_REASONING_EFFORT` 进两个通道的 `config_keys`，按 `_ALT_SUFFIX` 换算成 `LLM_REASONING_EFFORT_ALT`。两个通道当前 `shared_keys` 都为空。

### §11.2 三套连接的取数规则

`LLMConnection` 只描述对话通道。另两套连接各自组装，都走同一个 `build_provider`：

| 用途 | 组装方式 |
|---|---|
| 对话 | `LLM_PROVIDER` + `LLM_KEY` + `LLM_URL` + `LLM_MODEL` |
| 嵌入 | `EMBEDDING_KEY` + `EMBEDDING_URL` + `EMBEDDING_MODEL`，固定走 OpenAI 兼容端点 |
| 记忆汇总 | `MEMORY_MERGE_URL/KEY/MODEL` 三项齐全时用这三项组装且通道沿用 `LLM_PROVIDER`；不齐全时整个复用当前生效的对话方案 |

`pet/brain/memory_merge.py` 的思考降级由 provider 自己完成，调用点不再重发请求：`openai` 通道在 `_create` 内部完成，`gemini` 通道在端点拒绝档位后按默认档位重发。汇总沿用当前生效方案的推理强度。

### §11.3 设置页

设置页是手写控件，通道可配置后联动分三层（`BRAIN` × 通道 × 方案）。

#### §11.3.1 三层联动

| 维度 | 控件 | 影响范围 |
|---|---|---|
| `BRAIN` | 「调用模式」下拉，`local` / `api` / `ollama` | 决定通道与方案控件是否可用 |
| 通道 | 「首选通道」「备选通道」下拉，选项来自 `PROVIDER_SPECS` | 决定哪些配置键显示 |
| 方案 | 「当前启用」标签与切换按钮 | 只影响运行时取哪一套，不影响显隐 |

`on_mode_changed()` 的判定规则：

```
BRAIN == local   → 通道下拉、方案键、测试按钮全部禁用
BRAIN == ollama  → 通道下拉禁用，附提示「Ollama 固定走 OpenAI 兼容接口」
                   方案键按 openai 的 config_keys 显示
BRAIN == api     → 通道下拉启用，方案键按选中通道的 config_keys 显示
```

`BRAIN` 从 `api` 切到 `ollama` 时 `LLM_PROVIDER` 的值保留不动，只禁用控件，切回时恢复。

#### §11.3.2 连接页签

| 位置 | 控件 | 约定 |
|---|---|---|
| 调用模式 | `BRAIN` 下拉 | 不变 |
| | 「首选通道」下拉，`LLM_PROVIDER` | 位于调用模式下方 |
| 首选方案 | `LLM_URL` | 按通道显示，占位符随通道变化 |
| | `OLLAMA_BASE_URL` | 仅 `BRAIN=ollama` 时可用 |
| | `LLM_KEY` | 占位符随通道变化 |
| | `LLM_MODEL` 与「获取列表」 | 按钮按 `capabilities.model_list` 显隐，见 §11.3.6 |
| | `LLM_TEMPERATURE`、工具三件套、思考开关 | 不变 |
| | 「推理强度」，`LLM_REASONING_EFFORT` | 选项来自端点可用档位；勾选关闭思考时禁用 |
| 模型方案 | 「当前启用」、切换按钮、回退开关 | 不变 |
| | 「备选通道」下拉，`LLM_PROVIDER_ALT` | 位于备选分组首行，选项与首选一致 |
| | `LLM_URL_ALT`、`LLM_KEY_ALT`、`LLM_MODEL_ALT` | 显隐与占位符随通道变化 |
| | 「推理强度」，`LLM_REASONING_EFFORT_ALT` | 选项来自备选端点；备选连接未填完整时显示「跟随首选」并灰置 |
| 测试 | 「测试连接」按钮 | 走 provider 的 `complete()`，读界面当前档位，主动降级时在结果里说明原因 |

`LLM_CACHE_PROMPT` 只读 settings.json：openai 兼容端点普遍自动缓存并忽略 `cache_control`，gemini 通道丢弃该键，界面上不提供开关。

档位行在通道声明了档位时显示，选项随通道、模型名与端点清单重建，端点清单只读 settings.json；当前值不在新集合内时回到首项，不改写配置。端点没有档位时下拉灰置，悬停说明当前模型不支持推理强度设置。

#### §11.3.3 行为页签的记忆分组

| 位置 | 控件 | 约定 |
|---|---|---|
| 启用向量记忆 | `EMBEDDING_ENABLED` | 不变 |
| | `EMBEDDING_URL`、`EMBEDDING_KEY`、`EMBEDDING_MODEL`、`EMBEDDING_DIM` 与「获取列表」 | 固定走 OpenAI 兼容端点，不随对话通道变化 |
| 整点汇总 | `MEMORY_EVENT_MERGE_ENABLED`、`MEMORY_MERGE_*` | 汇总沿用对话通道，界面注明「留空时使用当前启用的对话通道」 |

#### §11.3.4 显隐与占位符的派生

| 关注点 | 落在哪里 |
|---|---|
| 通道下拉的选项 | `channel_choices()` 从 `PROVIDER_SPECS` 派生 |
| 通道专属键的显隐 | `config_keys()` / `config_keys_for()`，由 `_apply_provider_visibility()` 执行 |
| 推理强度选项 | `reasoning_efforts()`，同一方法里按方案各自重建 |
| 地址占位符 | `url_placeholder()` 取登记行的 `url_hint` |
| 页签归属 | `TAB_SPECS` 手写，决定控件落在哪个页签 |

控件与中文标签仍手写：布局与分组要人工决定，`_KEY_META` 描述不了「放在哪个 `QGroupBox` 里」。派生的是选项、显隐与占位符，不是布局。显隐必须在 `bind()` 里连通道下拉的变更信号，用户切换选项即重算，不依赖调用方手动触发。

两个通道的键集合相同，差异只有推理档位：`openai` 多 `xhigh` 与 `max`。

#### §11.3.5 切换通道时的残留

切换通道不清空已填的值。从 `openai` 切到 `gemini` 时 `LLM_URL` 保留，切回来仍是原值，避免用户在两个通道之间来回对比时反复丢配置。

#### §11.3.6 「获取列表」按钮的状态

| 状态 | 条件 | 表现 |
|---|---|---|
| 可用 | `capabilities.model_list` 为真 | 显示且可点 |
| 不可用 | `capabilities.model_list` 为假 | 隐藏，不占位 |

嵌入那一组不随对话通道变化：`EmbeddingClient` 固定按 OpenAI 兼容端点构建，与 `capabilities.embedding` 无关。

#### §11.3.7 不受影响的页签

提示词与通用两个页签不涉及通道，控件与联动都不动，`TAB_SPECS` 里的条目保持原样。

## §12 风险

- `google-genai` 只在函数内导入。`ARCH005` 只扫模块级 import，把 `google` 加入 `HEAVY_IMPORTS` 的作用是让模块级误导入被拦下；工厂处把 `ImportError` 转成 `ProviderUnavailable`。
- `google-genai` 默认 HTTP 客户端是 `httpx2`，与项目现有的 `httpx` 不是同一个包。异常映射以实测类名为准，Gemini 侧不依赖 `httpx` 的类型。
- Gemini 的 `thinking_level` 在更早的代际上不合法，会返回 400。降级路径见 [通用推理强度设计](2026-11-25-reasoning-effort-design.md) §4.5：记住拒因后不再发送该字段，本次请求按默认档位重发一次。该状态归属 provider 实例，不影响其他通道。
- Gemini 把思考计入输出上限，配额需要在真实 key 下复测，判据是是否出现 `length`。
- 不返回调用 id 的通道按序号生成 id，跨轮可能重复。编码层按位置取最近一次同名调用；新增此类通道时，必须同时保证流式与非流式的序号起点不同。
- 图片 base64 的 MIME 由 `{"type": "image"}` 携带，Gemini 对 `image/webp` 与 `image/jpeg` 都支持，`SCREENSHOT_FORMAT` 不需要额外分支。
- `stream()` 若被写成 generator，看门狗与 `close()` 同时失效，表现为服务端无响应时进程卡到总超时。
- Gemini 把思考正文作为 `Part.thought=True` 的 part 与正文混在同一 content 里。编码层必须按该标记分流：标为思考的进 `StreamEvent.reasoning`，其余进 `content`。漏掉这一步会把思考内容当台词播出。
- 事件日志里的历史消息只存 dict，升级不改表结构，回放依赖 provider 现场编码，因此不需要数据迁移。
- `PROVIDER_SPECS` 与 `_KEY_META` 是两个需要同步的位置。约束是 `config_keys` 与 `shared_keys` 里的每个键都必须在 `_KEY_META` 存在，反过来不要求（`EMBEDDING_*` 不属于任何通道行）。这条一致性由用例守住。
- 能力声明可能与实际不符。声明 `native_tools=True` 而端点实际拒绝时，`tool_channel` 的降档仍然生效；声明 `vision=True` 而模型不支持时，会在首次带图请求返回 400 并落到本地兜底。声明只求「默认正确」，例外交给既有降级路径。
- `list_models()` 在 Gemini 侧返回的模型名包含不支持 `generateContent` 的条目（例如只支持嵌入的模型）。实现按 `supported_actions` 过滤，不支持 `generateContent` 的条目不列出。
- 加入 Vertex 时改动面是：新建 provider 模块、`PROVIDER_SPECS` 加一行、`LLMConnection` 增加项目与区域字段、`_KEY_META` 增加三个 `VERTEX_*` 键。编码层与解析层不变，因为 Vertex 与 AI Studio 共用 `google-genai` 的请求体结构。