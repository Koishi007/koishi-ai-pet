# 0015 推理强度按通道与端点声明

- 状态：已采纳
- 相关代码：`pet/brain/reasoning.py`、`pet/brain/providers/openai_provider.py`、
  `pet/brain/providers/gemini_provider.py`、`pet/brain/providers/registry.py`、
  `pet/brain/llm_client.py`、`pet/ui/settings_tabs.py`
- 相关文档：[通用推理强度设计](../specs/2026-11-25-reasoning-effort-design.md)、
  [0014 模型供应商抽象](0014-provider-abstraction.md)

## 背景

OpenAI 兼容端点用 `reasoning_effort` 表达推理强度，Gemini 用 `thinking_level`，取值集合不同：
Gemini 只有四档，高于 `high` 的 `xhigh` 与 `max` 只有部分网关接受。同一通道下各网关的白名单也不同，
例如 MiMo 接受 `none/low/medium/high`，`minimal` 与 `xhigh` 返回 400；DeepSeek 接受
`none/low/high/max`。两个通道都没有可查档位的接口。

## 决定

- 配置存中性档位 `minimal` / `low` / `medium` / `high` / `xhigh` / `max`，空串表示不下发。词表在
  `pet/brain/reasoning.py`，只依赖标准库，界面与 provider 都能导入。
- 通道在 `PROVIDER_SPECS` 的 `reasoning_efforts` 声明提供的档位，provider 持有 id → 协议值的映射表。
- 端点接受范围按三层取值：`LLM_REASONING_EFFORTS` 配置清单 → `MODEL_EFFORT_PRESETS` 的模型名前缀
  预设 → 通道声明。预设用包含匹配，表序即优先级。前两层只收窄通道声明，结果决定界面选项与是否下发。
- 端点清单是高级配置，`hidden` 为真，只从 `settings.json` 生效；设置页只呈现收窄后的下拉，不提供输入框。
- 档位按方案分键：`LLM_REASONING_EFFORT` / `LLM_REASONING_EFFORTS` 与各自对应的 `_ALT`。首选与备选可能是
  不同通道的不同模型，各自按自己的模型算可用档位；备选连接缺任一项时整个方案沿用首选，档位跟随首选
  并在界面灰置。
- 档位只在 `thinking_active()` 为真时下发；关闭思考时不下发档位，思考开关本身照常下发。
- 端点拒绝时不前置报错：发送前丢掉端点范围外的档位并记日志，端点返回 400 时丢掉该字段重发一次并
  记住。`openai` 通道的思考开关与档位是两个独立字段，各自被拒、各自去掉。
- 不保留 `GEMINI_THINKING_LEVEL` 的迁移读取。该键随供应商抽象一起引入，未进入已发布配置面。

## 后果

- 收益：新增通道只需 provider 内一张映射表、登记行一行与一组用例，界面不改。
- 收益：知名模型的档位范围由预设兜住，下拉不再出现必然被拒的项；普通用户不需要理解端点能力表。
- 收益：私有网关与中转站可以在 `settings.json` 里用清单对齐，不用改代码。
- 收益：预设只收保守集合，漏给的档位由清单补，不会发出端点不接受的取值。
- 代价：声明分三层，排查时要知道取值顺序；预设随厂商文档维护，滞后时靠清单覆盖。
- 代价：端点清单没有界面入口，排障要手改 `settings.json`。
- 代价：备选连接未填完整时档位跟随首选，要独立设置必须把地址、Key 与模型名都填上。
- 约束：`_KEY_META` 的 enum、`settings_spec` 的文案表、各通道的 `reasoning_efforts`、
  `MODEL_EFFORT_PRESETS` 四处需要同步，由用例守住。
