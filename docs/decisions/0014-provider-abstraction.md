# 0014 模型供应商抽象

- 状态：已采纳
- 相关代码：`pet/brain/providers/`、`pet/brain/llm_types.py`、`pet/brain/llm_errors.py`、
  `pet/brain/llm_gateway.py`、`pet/brain/tool_channel.py`、`pet/brain/llm_retry.py`、`pet/brain/parsing.py`
- 相关文档：[provider 抽象设计](../specs/2026-11-23-llm-provider-abstraction-design.md)、
  [architecture.md](../architecture.md) §4、§11

## 背景

决策链路需要同时支持 OpenAI 兼容端点与 Google Gemini。把 `LLM_URL` 指向 Gemini 的
OpenAI 兼容端点只需改配置，但拿不到 `thinking_budget`，兼容层对 `extra_body.thinking`、
`stream_options.include_usage` 也没有对应支持，进程首次请求返回 400 后降级重试。
两家接口在消息形态、工具声明、异常类型上的差异会渗进解析与重试。

## 决定

在 `pet/brain` 内定义一个供应商协议 `ChatProvider`，内部只流通中性类型：消息、`StreamEvent`、
`Completion`、`ProviderError` 族。OpenAI 与 Gemini 各自实现编码与异常映射，
解析、重试、工具通道、编排层不出现供应商字样。

约束随决定一起确定：

- 抽象点只有 provider 一处。消息形态、工具声明、工具历史都先归约成中性结构，再由 provider 生成各家 payload；
  不在调用点写供应商分支。
- 降级判据只看中性错误：思考参数是否被拒由 `thinking_rejected()` 回答，工具字段是否被拒由
  `ProviderBadRequest.body` 与通道词表判断，两者都不看 SDK 异常类型。判断依据随 provider 实例存放，
  不设进程级全局；工具通道的降档缓存同样由 `LLMClient` 持有的 `ToolChannelState` 实例保存。
- 参数被拒的降级一律在 provider 内部完成，调用方只看到最终结果或错误；网关只处理「工具协议整体
  不可用」这一层，因为它要改写整个消息体。网关的两个入口 `complete()` / `stream()` 与
  `ChatProvider` 的方法同名同签名。
- 供应商 SDK 只在通道构造时导入，模块级不加载。`google-genai` 是必装依赖；通道名未知或依赖缺失时
  `build_provider` 抛 `ProviderUnavailable`，消息里带该通道声明的依赖名。
- 重试只有一处：`pet/brain/llm_retry.py` 的 tenacity 重试与备选方案切换。provider 构造 SDK 客户端时
  关闭 SDK 自带的内部重试，避免两层叠乘放大请求数与耗时。
- 本阶段只落 openai 与 gemini 两个通道。Vertex 需要 GCP 项目、区域与服务账号，
  认证模型与 AI Studio 的 API Key 不同，留作后续通道。
- 能力用 `Capabilities` 声明，不用一串布尔字段，也不靠运行期试探发现。视觉、原生工具、
  思考、嵌入、模型列表各占一项，消费方按声明分支。
- 推理强度按方案配置：`LLM_REASONING_EFFORT` 与 `LLM_REASONING_EFFORT_ALT`，值是中性档位。通道在
  `PROVIDER_SPECS` 的 `reasoning_efforts` 声明自己提供哪些档位，协议字段由 provider 翻译，界面选项
  由声明派生。不按模型名推断代际，端点返回 400 时 provider 丢掉该字段、按默认档位重发一次，设置页的
  连通性测试同时把原因告知用户。取舍见 [0015](0015-reasoning-effort-per-channel.md)。
- 协议只保留对话必需的方法。`embed()` 与 `list_models()` 不设协议，由 `Capabilities` 的
  `embedding` / `model_list` 声明，调用方先看声明再调方法：没有嵌入模型的通道不写抛异常的空实现。
- 一次性 provider 用 `temporary_provider` 上下文管理器，实例交给后台线程前先确认线程已起。
  泄漏的连接池不会自己回收；长期持有的 provider 由 `LLMClient.close()` 在退出流程释放。
- 通道用 `PROVIDER_SPECS` 登记，配置键与界面控件的显隐从登记派生。行内分两类键：`config_keys`
  随方案换算成备选键名，`shared_keys` 两套方案共用。新增通道的改动面是
  「provider 模块 + 表里一行 + `_KEY_META` 增补该行声明的键 + 一组用例」。
- 通道属于方案不属于全局。`LLM_PROVIDER` 描述首选方案、`LLM_PROVIDER_ALT` 描述备选方案，
  `LLM_ACTIVE_PROFILE` 只决定哪套方案生效。`BRAIN` 保持 local / api / ollama 三档作为总开关，
  ollama 复用 OpenAI 通道，既有连接配置不变。
- 本决定取代 [2026-10-22 投影设计](../specs/2026-10-22-session-event-log-projection-design.md) §4.8 的
  `MessageEncoder`：provider 覆盖同一范围并额外承担流式解码与异常映射。§4.8 记录的协议约束表继续有效。

## 后果

- 新增供应商只改四处：provider 模块、登记表一行、`_KEY_META` 的配置键、用例。解析与编排不动；
- 约束：不返回调用 id 的通道按序号生成 id，序号只在同一段历史内唯一；编码层按位置取最近一次
  同名调用，跨轮重复时才不会串；
- 约束：工具被拒的词表按通道分开。Gemini 用 `model` 角色表达工具历史，词表要求它与角色列举或
  函数同现，否则「模型不支持某参数」会被误判成通道不支持工具；
- 代价：多一层编码与异常映射，两个 provider 都要跟着各自 SDK 的版本维护映射表；
- 代价：解析层只认 `StreamEvent`，新增通道的用例必须覆盖事件切分而不是 chunk 形状；
- 代价：Gemini 的思考计入输出上限，`pet/brain/budget.py` 的配额需要在真实 key 下复测；
- 代价：`PROVIDER_SPECS` 与 `_KEY_META` 形成两个需要同步的位置，靠一条用例守住一致性；
- 收益：思考降级、工具通道降级、重试分类、流式看门狗这些既有能力对两个通道同时生效，
  不依赖某一家 SDK 的错误语义；
- 收益：通道下拉的选项、专属键的显隐与地址占位符由登记表派生；界面控件本身仍手写，因为布局要人工决定。
