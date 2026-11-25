# 通用推理强度设计

> 把推理强度从 Gemini 专属旋钮提升为与通道无关的配置：配置里只存中性档位，每个通道声明自己提供哪些档位并把它翻译成协议字段；界面选项由通道声明派生，端点拒绝该档位时按默认档位重发，并在连通性测试里说明原因。

## §0 现状

| 项 | 现状 | 位置 |
|---|---|---|
| 配置键 | `GEMINI_THINKING_LEVEL`，str，取值 `""` / minimal / low / medium / high | `pet/config.py` 的 `_KEY_META` |
| 归属 | Gemini 登记行的 `shared_keys`，两套方案共用一个值 | `pet/brain/providers/registry.py` |
| 传递 | `build_provider(..., thinking_level=)` 传给 provider 构造参数 | `registry.py`、`llm_client.py`、`memory_merge.py`、`settings_actions.py` |
| Gemini 下发 | 有档位时 `ThinkingConfig(thinking_level=...)`；关闭思考时 `thinking_budget=0` | `gemini_provider.py` |
| OpenAI 下发 | 只发 `extra_body.thinking.type`，没有档位字段 | `openai_provider.py` |
| 降级 | 400 且 body 命中 thinking 词表时去掉该字段重发一次，实例记住 | 两个 provider 的 `_create` |
| 界面 | 手写档位元组，仅在选中 gemini 时显示 | `settings_spec.py`、`settings_tabs.py` |

现状的三条限制：

1. 档位名与 Gemini 绑定。OpenAI 兼容端点即使支持 `reasoning_effort` 也没有入口，用户只能改模型或关思考。
2. 档位元组在界面手写，与通道实际提供的集合之间没有一致性约束。新增通道要再抄一份，抄漏了也不会报错。
3. 「关闭思考」与「档位」是两条独立输入，同时生效时档位无意义。现状由两个 provider 各自隐式处理，界面不做提示。

## §1 目标

- 每个方案一个配置键、一个下拉，对两个通道同时生效。
- 配置只存中性档位。协议字段与取值由通道负责，调用方与界面不出现 `thinking_level` / `reasoning_effort` 这类字段名。
- 通道声明自己提供哪些档位，界面选项与显隐从声明派生，不再手写第二份。
- 端点拒绝某个档位时不让整轮请求失败：按该通道默认档位重发，把原因报到连通性测试。
- 关闭思考、预算、工具通道、缓存、重试的行为不变。

## §2 非目标

- 不做按模型查询档位。两个通道都没有可查的接口：Gemini 的 `list_models` 只返回 `supported_actions`，OpenAI 兼容端点没有能力查询接口。
- 不做档位夹紧与别名映射。`minimal` 不会在旧模型上退化成 `low`，不在通道声明集合内的取值一律不下发。
- 不引入 `off` 档。关闭思考已由 `LLM_THINKING_DISABLED` 与每次调用的 `thinking=False` 表达，再加一个同义档位会形成两处真源。
- 不保留 `GEMINI_THINKING_LEVEL` 的迁移读取。该键随供应商抽象一起引入、尚未提交，不构成已发布的配置面。
- 不新增第三方通道；新增通道时的改动面见 §4.2。

## §3 参考 dsh 的做法

DeepSeek Harness 把推理强度拆成「核心只认不透明 id，适配器负责声明与翻译」两层：

| 关注点 | dsh | 本项目落点 | 差异原因 |
|---|---|---|---|
| 档位标识 | 不透明品牌类型 `ReasoningEffortId`，取值由适配器定义 | 固定中性词表，通道声明子集 | 本项目没有第三方适配器与插件机制，词表固定即可；不透明 id 的收益在可扩展性，这里用不上 |
| 能力声明 | `resolveModel()` 返回有序的 `reasoning.efforts`，含展示名与说明，以及 `defaultEffort`；无推理能力的模型不声明 | 声明写在 `PROVIDER_SPECS` 的登记行，粒度是通道 | 界面列选项时还没有可用连接，构造不出 provider；也没有模型目录可查 |
| 校验 | 显式档位不在声明列表内即在 I/O 前抛 `UNSUPPORTED_REASONING_EFFORT`，不夹紧 | 发送前校验，不在通道集合内就不下发并记日志，按默认档位继续 | 桌宠无人值守，抛错会让整轮决策落到本地兜底，代价大于多一轮请求 |
| 协议值 | 适配器持有 wire 拼写：deepseek 用 `thinking.type` + `output_config.effort`，pi-ai 用 `thinkingLevelMap`，存在 `off→none`、`minimal→low` 这类改名 | 每个 provider 一张 id → 协议值的小表 | 同理 |
| 界面 | 由声明的 efforts 渲染，名称与说明来自声明 | 选项由所选通道的声明派生，文案在 `settings_spec` | 控件手写，布局需人工决定；沿用现有的「文案与存储值分离」约定 |

参考仓库 `https://github.com/deepseek-ai/deepseek-harness`：

| 主题 | 文件 |
|---|---|
| 不透明 id 与请求头事实 | `packages/llm/llm/src/brand.ts`、`packages/llm/llm/src/call-config.ts` |
| 档位元数据与校验 | `packages/llm/llm/src/types.ts` 的 `LlmReasoningEffortInfo`、`packages/llm/llm/src/index.ts` 的 `resolveCallWithInfo` |
| 单通道映射 | `packages/llm/llm-deepseek/src/model-info.ts`、`serialize.ts` |
| 按模型声明 | `packages/llm/llm-pi-ai/src/adapter.ts` 的 `reasoningInfo` |
| 取舍记录 | `.agents/notes/archived/architecture/2026-07-24-adapter-owned-reasoning-effort-capabilities.zh.md`、`.agents/notes/archived/feature/2026-08-08-pi-ai-per-model-reasoning-declarations.zh.md` |

## §4 设计

### §4.1 中性档位词表

新增 `pet/brain/reasoning.py`，放 id、端点预设与归一化，不放界面文案：

```python
EFFORT_IDS = ("minimal", "low", "medium", "high", "xhigh", "max")

def normalize(value: str | None) -> str:
    """去空白转小写；不在 EFFORT_IDS 内返回空串。"""

def preset_efforts(model: str | None) -> tuple[str, ...] | None:
    """按模型名前缀匹配预设，模型名包含该片段即命中；没有命中返回 None。"""
```

- `""` 表示不下发，语义与现状「未选档位交给模型默认」一致。
- `xhigh` 与 `max` 高于 `high`，只有部分网关接受；真实差异由厂商实现决定。声明权归通道：openai 兼容通道声明两者，Gemini 都不声明。
- 文案归 `pet/ui/settings_spec.py`，与 `_KEY_META` 的 `enum` 是值集合真源。两处集合都不从词表派生，一致性由用例守住：`pet/config.py` 一旦 import `pet.brain.reasoning` 就会连带加载 `pet.brain` 包的 `__init__`，后者 import `behavior` 与 `llm_client`，再 import `config` 即成环；`settings_spec` 被 `scripts/gen_docs.py` 运行时导入，同样不该带上 `behavior` 的依赖。
- 模块只依赖标准库，provider 与界面都能导入。

**端点预设**：同一 channel 下各网关接受的档位不同，用 `MODEL_EFFORT_PRESETS` 按模型名前缀声明，匹配到就用它收窄下拉与下发范围；空元组表示该端点没有档位控制，未匹配表示不知道，退回通道声明的全集。

| 模型前缀 | 档位 | 依据 |
|---|---|---|
| `gpt-5.2`、`gpt-5.1` | low、medium、high、xhigh | GPT-5.2 支持 none 到 xhigh，none 由思考开关表达 |
| `gpt-5` | minimal、low、medium、high | xhigh 从 GPT-5.2 起提供 |
| `o1`、`o3`、`o4` | low、medium、high | 较早的推理模型 |
| `deepseek` | low、high、max | 文档白名单 `none/low/high/max`，minimal 映射 low、medium/xhigh 映射 high |
| `mimo` | low、medium、high | 实测白名单 `none/low/medium/high`，minimal 与 xhigh 返回 400 |
| `glm-5.3` | low、high、max | 智谱文档 |
| `glm-5.2` | high、max | 智谱文档 |
| `glm` | 无档位 | GLM-5.1 及更早不支持 `reasoning_effort` |
| `qwen4` | low、high、xhigh | 百炼文档 |
| `qwen3.8` | low、medium、xhigh | 百炼文档 |
| `qwen` | 无档位 | 更早的 Qwen 只支持 thinking_budget |

匹配用包含关系，`Qwen/Qwen4-Max` 这类带命名空间的模型名也能命中；表序即优先级。

预设只收厂商文档档位白名单里的保守集合：宁可少给一项，也不发必然被拒的值。新增条目按文档取值，不按推测。

### §4.2 通道声明

`PROVIDER_SPECS` 每行增加 `reasoning_efforts`，按 `EFFORT_IDS` 顺序声明该通道对外提供的档位：

| 通道 | `reasoning_efforts` | id → 协议值 | 落在哪 |
|---|---|---|---|
| openai | minimal、low、medium、high、xhigh、max | 恒等，写进请求体顶层 `reasoning_effort` | provider 模块的映射表 |
| gemini | minimal、low、medium、high | 恒等，写进 `ThinkingConfig(thinking_level=...)` | provider 模块的映射表 |

两个通道今天的映射都是恒等，映射表的意义在新增通道与将来出现改名时：dsh 的 pi-ai 适配器就存在 `off→none`、`minimal→low` 这类非恒等条目。登记行只声明「提供哪些档位」，协议值归 provider，调用方不读这张表。

两个通道的集合不同是刻意的：Gemini 的 `thinking_level` 只有四档，声明 `xhigh` 或 `max` 会让下拉给出一个必然被拒的选项；OpenAI 兼容通道面向第三方网关，`xhigh` 与 `max` 在部分端点上有效，不接受该值的端点由 §4.5 的降级兜住。

新增 `registry.reasoning_efforts(name)`：返回该行声明的元组，未知通道返回空元组。登记行与 provider 映射表的键集合由用例比对。

为什么放登记表而不是 `Capabilities`：`Capabilities` 是实例回答的事实，而界面渲染下拉时还没有 key 与地址，构造不出实例。登记表是静态事实，与现有「通道下拉、专属键显隐、地址占位符都由登记派生」的约定一致。

新增通道时的改动面：provider 模块内一张映射表、`PROVIDER_SPECS` 加一行 `reasoning_efforts`、`_KEY_META` 无需改，档位是共用的值域；再加一组用例。界面不改。

### §4.3 配置面

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `LLM_REASONING_EFFORT` | str | `""` | 首选方案的推理强度：留空交给模型默认 |
| `LLM_REASONING_EFFORTS` | str | `""` | 首选端点支持的档位，逗号分隔；留空按模型预设。仅 settings.json，高级配置 |
| `LLM_REASONING_EFFORT_ALT` | str | `""` | 备选方案的推理强度，按备选自己的通道与模型算 |
| `LLM_REASONING_EFFORTS_ALT` | str | `""` | 备选端点支持的档位；留空按模型预设。仅 settings.json，高级配置 |
| `GEMINI_THINKING_LEVEL` | 无 | 无 | 从 `_KEY_META` 删除 |

改名原因：键名带通道名，在跨通道之后与事实矛盾。保留旧名会让「哪些通道读它」说不清。

**按方案分键**：档位是模型属性，首选与备选可能是不同通道的不同模型。共用一份取值意味着「首选 gemini 用 high、备选 OpenAI 用 low」这类常见组合无法表达。四个键都进各通道登记的 `config_keys`，`_ALT_SUFFIX` 增加档位到 `_ALT` 的换算，显隐与地址、Key、模型名走同一条派生路径。

**端点清单**：模型前缀只能覆盖知名厂商，私有网关与中转站无从推断。`LLM_REASONING_EFFORTS` 按方案手写端点接受的档位，优先于预设；只收窄不扩展，写了通道没声明的 id 会被忽略。三层取值顺序：配置清单 → 模型预设 → 通道声明。

两个端点清单键的 `hidden` 为真，只从 `settings.json` 生效，设置页不提供入口：取值集合是排障用的高级配置，摆到界面上会让普通用户面对一个没有上下文的输入框。

**备选档位的归属**：两套方案各自取值，档位按自己的通道与模型算，互不读取对方的配置。备选未填齐时只是不参与调用，档位照常可选：连接是否完整与能选哪些档位是两件事。首选与备选可以是不同通道的不同模型。

`LLM_REASONING_EFFORT` 不再属于 Gemini 的 `shared_keys`；该行 `shared_keys` 变空。

### §4.4 请求构造

两个通道共同的规则：

1. `thinking_active(thinking)` 为假时一律不发档位，思考开关本身照现状下发：openai 发 `type: disabled`，gemini 发 `thinking_budget=0`。关闭思考时档位没有意义，这一条在 provider 内实现，不依赖调用方。
2. 档位为空串时不发。
3. 档位不在端点可用集合内时不发，记一条 warning。端点可用集合 = 通道声明 ∩ 端点清单或模型预设，见 §4.3。
4. 档位不改变 `max_tokens`、温度、工具声明。

openai 通道：

```python
# _EFFORTS：中性 id -> 本通道协议值，模块级常量
wire = self._EFFORTS.get(effort)          # None 表示本通道不提供该档位
body = {}
if self.capabilities.thinking and not self._thinking_unsupported:
    body["thinking"] = {"type": "enabled" if thinking_active(thinking) else "disabled"}
if wire and thinking_active(thinking) and not self._effort_unsupported:
    body["reasoning_effort"] = wire
if body:
    kwargs["extra_body"] = body
```

`reasoning_effort` 走 `extra_body` 而不作为 `chat.completions.create` 的具名参数：`pyproject.toml` 的依赖下界是 `openai>=1.0.0`，该具名参数是后续版本才加入的，走 `extra_body` 与 SDK 版本无关。`extra_body` 会把键合并进请求体顶层，与具名参数到达服务端后的位置相同。

gemini 通道：`_level` 改名为 `_effort`，取值经同一结构的映射表写进 `ThinkingConfig(thinking_level=...)`，其余下发方式不变。

### §4.5 降级阶梯

两个字段各自独立被拒、各自被去掉，单次请求最多重发两次：

| 被拒内容 | 判据 | 动作 | 状态存放 |
|---|---|---|---|
| 档位 | 400 / 422 且 body 命中档位词表 | 去掉 `reasoning_effort` 重发，保留 thinking | `_effort_unsupported` |
| 思考 | 400 / 422 且 body 命中思考词表 | 去掉 `thinking` 重发 | `_thinking_unsupported` |

- **判据顺序**：先判档位再判思考。档位判据是错误文本含 `effort`，覆盖 `reasoning_effort` 与 `output_config.effort` 两种写法；思考判据是含 `thinking`。`thinking.effort is not supported` 这类消息同时命中两套词表，先判档位才能只去掉一个字段。
- **记忆粒度**：状态记在 provider 实例上，`rebuild()` 后清空。这与现状一致，见 provider 抽象设计 §8。
- **gemini 侧**：`thinking_config` 被拒即视为档位被拒，沿用现有的「去掉重发一次」。它没有第二个字段可降，阶梯只有一级。两个 provider 的实例标志统一叫 `_effort_unsupported`。
- **重发上限**：两个字段都逐步被去掉是兼容端点的最坏情况，最多三次请求；之后该实例不再发这两个字段。

`thinking_rejected(exc)` 只回答思考字段：`_rejection_field(exc)` 先查 `effort`，命中即返回档位，思考判据因此不会吞掉档位拒绝。

### §4.6 协议与工厂

| 位置 | 改动 |
|---|---|
| `ChatProvider.thinking_level_rejected()` | 改名 `reasoning_effort_rejected()`，语义：该实例是否已因端点拒绝而停发推理强度 |
| `build_provider` / `temporary_provider` 的 `thinking_level=` | 改名 `reasoning_effort=`，缺省 `""` |
| `llm_client.resolve_reasoning_efforts(profile)` | 新增：通道声明按端点清单或模型预设收窄 |
| `llm_client.resolve_reasoning_effort(profile)` | 新增：首选按 `LLM_REASONING_EFFORT`，备选取 `LLM_REASONING_EFFORT_ALT`；不在端点可用集合内返回空串 |
| `llm_client._build_profile` | 传 `resolve_reasoning_effort(profile)` |
| `memory_merge._call_llm` | 传连接所属方案的档位；汇总沿用对话档位 |
| `LLMTestWorker` | 参数改名；降级说明按档位文案生成 |
| `SettingsActions.test_llm` | 读当前启用方案的界面值，连接未填完整时停在「未配置」并给出原因，见 §4.7 |

改名而不是新增参数：`thinking_level` 描述的是 Gemini 的字段名，通用之后这个名字会误导新增通道的维护者。

### §4.7 设置界面

| 位置 | 控件 | 约定 |
|---|---|---|
| 首选方案，模型名称之后 | 「推理强度」下拉，`LLM_REASONING_EFFORT` | 选项 = `("模型默认推理强度", "")` + 端点可用档位 |
| 备选方案，模型名称之后 | 「推理强度」下拉，`LLM_REASONING_EFFORT_ALT` | 选项按备选端点；首项文案与首选一致 |
| 显隐 | 通道声明了档位即显示 | 端点没有档位时下拉灰置并提示；连接是否填齐不影响可用状态 |
| 联动 | 勾选「关闭思考模式」时两个下拉都禁用，取消勾选恢复 | 悬停提示「关闭思考时不下发推理强度」 |

- **选项重建时机**：切换首选或备选通道、修改首选或备选模型名时重算，两侧按各自的通道与模型算。当前值仍在新集合内就保持选中；不在则显示首项「模型默认推理强度」；不写回配置，也不改动 `settings.json`。
- **测试连接读界面值**：读当前启用方案那份下拉的当前值，并按端点可用集合过滤，与运行期 `resolve_reasoning_effort` 一致；该方案连接未填完整时不发请求。
- 控件字段名：`effort_combo` / `effort_label`，备选侧加 `alt_` 前缀。

### §4.8 文案

| 用途 | 文案 |
|---|---|
| 首选下拉首项 | 模型默认推理强度 |
| 下拉首项 | 模型默认推理强度 |
| 档位项 | 最省 (minimal) / 低 (low) / 中 (medium) / 高 (high) / 极高 (xhigh) / 最高 (max) |
| 端点无档位时的悬停提示 | 当前模型不支持推理强度设置 |
| 关闭思考时的悬停提示 | 关闭思考时不下发推理强度 |
| 关闭思考时 | 两个下拉禁用，不另加文案 |
| 连通性测试降级说明 | 该模型不接受推理强度「高 (high)」，已按默认档位连通。 |

降级说明用档位文案而不是存储值，与下拉一致。

## §5 与既有设计的边界

| 项 | 是否改 |
|---|---|
| `LLM_THINKING_DISABLED`、`budget.thinking_active()` | 不改。档位只在 `thinking_active()` 为真时下发 |
| 输出预算 `budget.py` 的配额公式 | 不改。档位不参与配额，Gemini 的思考长度仍在真实 key 下复测 |
| 工具通道、缓存标记、重试、看门狗 | 不改 |
| `Capabilities.thinking` | 不改，仍表示「通道接受思考开关」 |
| 事件日志与投影 | 不改。档位是请求参数，不进事件表 |

## §6 改动清单

| 文件 | 改动 |
|---|---|
| `pet/brain/reasoning.py` | 新增：`EFFORT_IDS`、`MODEL_EFFORT_PRESETS`、`normalize()`、`parse_efforts()`、`preset_efforts()`、`effective_efforts()` |
| `pet/config.py` | 增 `LLM_REASONING_EFFORT`、`LLM_REASONING_EFFORTS` 及两个 `_ALT` 键，后两者 `hidden`；删 `GEMINI_THINKING_LEVEL` |
| `pet/brain/providers/__init__.py` | 协议方法改名 |
| `pet/brain/providers/registry.py` | 两行加 `reasoning_efforts`；`config_keys` 加档位键；`_ALT_SUFFIX` 加档位；`shared_keys` 清空；新增 `reasoning_efforts()`；工厂参数改名 |
| `pet/brain/providers/openai_provider.py` | 档位字段、`_rejection_field()`、两级降级阶梯 |
| `pet/brain/providers/gemini_provider.py` | 构造参数更名、映射表模块常量、方法更名 |
| `pet/brain/llm_client.py` | `resolve_reasoning_efforts()`、`resolve_reasoning_effort()` 的端点过滤、`_build_profile` |
| `pet/brain/memory_merge.py` | 传生效方案的档位 |
| `pet/ui/settings_spec.py` | `THINKING_LEVEL_CHOICES` → `EFFORT_CHOICES`，含 xhigh 与 max；`effort_label()`、`TAB_SPECS` 换两个键 |
| `pet/ui/settings_tabs.py` | 两个下拉、选项随通道与模型重建、显隐、与关闭思考的联动 |
| `pet/ui/settings_fields.py` | `SettingsWidgets` 字段更名 |
| `pet/ui/settings_actions.py`、`settings_workers.py` | 读界面档位并按端点集合过滤、降级说明 |
| `tests/` | 见 §7。新增 `test_reasoning_effort.py`；`test_gemini_thinking_budget.py` → `test_provider_gemini_thinking.py`，`test_settings_thinking_budget.py` → `test_settings_reasoning_effort.py`；`test_settings_visibility.py`、`test_provider_registry.py`、`test_provider_openai.py` 同步 |
| `docs/reference/config.md` | `python scripts/gen_docs.py` 重新生成 |
| `docs/specs/2026-11-23-llm-provider-abstraction-design.md` | §5.2、§11、§11.3 的档位条目改成本文 |
| `docs/decisions/0014-provider-abstraction.md` | 「推理强度只有 `GEMINI_THINKING_LEVEL` 一个旋钮」改为按通道声明 |
| `docs/decisions/0015-reasoning-effort-per-channel.md` | 新增：档位由通道声明、配置存中性值且按方案分键 |
| `docs/decisions/README.md`、`docs/README.md` | 索引登记 |

## §7 测试计划

| 用例 | 断言点 |
|---|---|
| `normalize()` | 大小写、首尾空白归一；未知名与 `None` 返回 `""` |
| 词表一致性 | `_KEY_META` 两个档位键的 enum 都是 `[""] + EFFORT_IDS`；各通道 `reasoning_efforts` ⊆ `EFFORT_IDS` |
| 声明一致性 | 各通道的 `reasoning_efforts` 与 provider 模块映射表的键集合相等 |
| OpenAI 下发 | 档位随 `extra_body` 发出；`thinking=False` 时不发；未声明的档位不发 |
| OpenAI 降级 | 档位被拒时只去掉 `reasoning_effort`，`thinking` 仍在，实例记住；思考被拒时只去掉 `thinking`；两个都被拒时调用次数为 3，且之后不再发这两个字段 |
| 判据 | `thinking.effort is not supported` 归为档位拒绝而不是思考拒绝 |
| Gemini 下发与降级 | 现有用例更名后语义不变 |
| 方案取值 | 首选与备选各自取自己的键，互不读取对方的值 |
| 端点预设 | 按模型前缀命中预设；未命中退回通道声明 |
| 端点清单 | 配置清单优先于预设；与通道声明取交集；中文逗号与未知值；键为 hidden |
| 端点过滤 | 预设外的档位解析为空串，不下发 |
| 界面显隐 | 通道声明了档位即显示；端点没有档位时下拉灰置、悬停提示非空 |
| 界面选项 | 选项按端点可用档位；改模型名或清单即重算；当前值不在集合内时回到首项 |
| 联动 | 勾选「关闭思考模式」时两个下拉都禁用 |
| 连接测试 | 使用当前启用方案下拉的当前值；该方案连接未填完整时不发请求 |

断言按契约标识写：档位 id、请求字段是否存在、调用次数、实例标志位。不断言下拉文案与提示文案。

## §8 风险

- **两个字段同时下发可能与某些网关冲突**。逐字段去掉并记住，代价是最多多两轮请求；换来的是不必为每个端点维护一份方言配置。
- **预设会滞后于厂商更新**。新模型或新档位发布后，预设可能少给一项；用 `LLM_REASONING_EFFORTS` 覆盖即可，不需要改代码。判据是厂商文档的档位白名单。
- **未命中预设的端点仍声明全集**。私有网关与少见模型会拿到六档，选到不被接受的值时走 400 降级；在 `settings.json` 填一次端点清单即可消除。
- **`extra_body` 的合并位置**依赖 openai SDK 的行为，升级 SDK 后需复测 `reasoning_effort` 是否仍在请求体顶层。判据：对兼容端点发一次请求，抓包或看服务端回显。
- **Gemini 的档位支持按代际不同**：`minimal` 与 `medium` 在 2.5 代不合法。现状不按模型名推断，靠 400 降级；本文沿用，不新增代际表。
- **降级是静默的**：只在连通性测试与日志里可见。自主决策期间被静默降级时，用户不会立刻知道；这是无人值守场景下与 dsh「前置拒绝」的取舍差异，记录在 §3。
- **备选未填完整时它不参与调用**：档位仍按备选自己的通道与模型算，只是没有 provider 可用；要让它生效必须把该通道要求的连接字段都填上。
- **档位进入 openai 通道后请求体更大**，但只有键值对，不影响配额与缓存前缀。
