# 文档索引

## 按目的选路径

| 目标 | 读这些 |
|---|---|
| 安装、配置模型、用内置工具 | [../README.md](../README.md) |
| 建立整体心智模型：线程怎么跑、一次决策经过什么 | [architecture.md](architecture.md) |
| 查某个配置项的类型/默认值/是否需重启 | [reference/config.md](reference/config.md) |
| 查动作、工具、粒子特效、提示词块的清单 | [reference/](reference/) |
| 搞懂 system prompt 是怎么拼出来的 | [subsystems/context-and-prompts.md](subsystems/context-and-prompts.md) + [reference/prompt-blocks.md](reference/prompt-blocks.md) |
| 给桌宠加一项工具能力 | [tool-development.md](tool-development.md) |
| 深入某一子系统（记忆、数值、动作、提示词、素材） | [subsystems/](subsystems/) |
| 参与开发：环境、测试、提交、PR | [../CONTRIBUTING.md](../CONTRIBUTING.md) |
| 发版、理解更新脚本行为 | [operations/release.md](operations/release.md) |
| 排查问题（日志、崩溃报告在哪） | [operations/troubleshooting.md](operations/troubleshooting.md) |
| 看懂 vitals / mood / needs / outcome 这些词 | [glossary.md](glossary.md) |
| 知道某个设计「为什么这么做」 | [decisions/](decisions/) |
| 看一次较大改动的设计取舍与实施计划 | [specs/](specs/) |
| 看某个版本改了什么 | [../CHANGELOG.md](../CHANGELOG.md) |

## 文档地图

| 文件 | 角色 | 类型 | 内容 | 维护方式 |
|---|---|---|---|---|
| [architecture.md](architecture.md) | 架构解释 | 手写 | 分层与线程模型、启动链路、数据流、状态机、模块职责、红线与常见改动入口 | 改架构时人工更新 |
| [glossary.md](glossary.md) | 事实参考 | 手写 | 项目专有术语表 | 新增术语时人工补充 |
| [tool-development.md](tool-development.md) | 操作指南 | 手写 | 工具开发：目录约定、`register()` 模板、参数与返回值约定、`TOOL_CTX` 能力、`aside`、启用方式 | 改工具约定时人工更新 |
| [reference/config.md](reference/config.md) | 事实参考 | 生成 | 配置项全量（按设置页签分组）+ 字段含义 | `pet/config.py` 的 `_KEY_META` |
| [reference/actions.md](reference/actions.md) | 事实参考 | 生成 | 动作表：分类、参数、示例、时长范围 | `pet/action/registry.py` |
| [reference/tools.md](reference/tools.md) | 事实参考 | 生成 | 工具分组、方法、参数 | `pet/tools/**/__init__.py` 与 `pet/tools/registry.py` |
| [reference/effects.md](reference/effects.md) | 事实参考 | 生成 | 粒子特效名、生成函数、默认位置 | `pet/ui/particle.py` 的 `_SPAWNERS` |
| [reference/prompt-blocks.md](reference/prompt-blocks.md) | 事实参考 | 生成 | 提示词块、感知段/任务段组合、合法组合白名单 | `pet/brain/prompts.py` |
| [reference/modules.md](reference/modules.md) | 事实参考 | 生成 | 每个模块的一句话职责 | 模块 docstring |
| [subsystems/context-and-prompts.md](subsystems/context-and-prompts.md) | 架构解释 | 手写 | 上下文与提示词组装、三条任务的差异、动态块生命周期 | 改提示词结构时人工更新 |
| [subsystems/memory.md](subsystems/memory.md) | 架构解释 | 手写 | 记忆数据模型、写入与召回、维护与容量、陷阱 | 改记忆策略时人工更新 |
| [subsystems/vitals-and-mood.md](subsystems/vitals-and-mood.md) | 架构解释 | 手写 | 数值来源、衰减、阈值信号、到提示词的映射 | 改数值手感时人工更新 |
| [subsystems/actions-and-animation.md](subsystems/actions-and-animation.md) | 架构解释 | 手写 | 动作链路、队列与超时、帧动画的加载与运行、粒子 | 改动作系统时人工更新 |
| [subsystems/assets-pipeline.md](subsystems/assets-pipeline.md) | 事实参考 | 手写 | 素材规格、目录与命名、配置字段契约、入库检查清单 | 改素材规格或流程时人工更新 |
| [operations/release.md](operations/release.md) | 操作指南 | 手写 | 版本与 tag、更新脚本行为、发布检查清单、变更记录 | 改发布流程时人工更新 |
| [operations/troubleshooting.md](operations/troubleshooting.md) | 操作指南 | 手写 | 现象对照表、诊断命令、上报所需信息 | 遇到新问题时补充 |
| [decisions/](decisions/README.md) | 决策记录 | 手写 | 设计决策记录（ADR），索引见该目录的 README | 做出取舍时新增一条 |
| [specs/2026-09-29-context-injection-pool-unify-design.md](specs/2026-09-29-context-injection-pool-unify-design.md) | 设计文档 | 手写 | 候选池容量与每轮注入上限合并的根因、方案取舍、被否决的备选方案 | 动手前写，落地后补齐结论 |
| [specs/2026-09-29-context-injection-pool-unify-plan.md](specs/2026-09-29-context-injection-pool-unify-plan.md) | 实施计划 | 手写 | 该改动拆成的任务、每步的命令与验收标准 | 执行时逐项勾选 |
| [specs/2026-09-30-agent-oriented-architecture-feedback-design.md](specs/2026-09-30-agent-oriented-architecture-feedback-design.md) | 设计文档 | 手写 | 把架构红线做成面向 Agent 的契约测试：规则表、失败格式、误报缓解、allowlist 收敛边界 | 动手前写，落地后补齐结论 |
| [specs/2026-10-06-file-drop-intake-design.md](specs/2026-10-06-file-drop-intake-design.md) | 设计文档 | 手写 | 拖入文件交互：可读判定与解码链、动作通道映射、拒绝与降级规则 | 动手前写，落地后补齐结论 |
| [specs/2026-10-06-file-drop-intake-plan.md](specs/2026-10-06-file-drop-intake-plan.md) | 实施计划 | 手写 | 该改动拆成的七个任务：纯逻辑包、判定层、气泡、事件转发、附件参数、工具扩展点、收尾 | 执行时逐项勾选 |
| [../CHANGELOG.md](../CHANGELOG.md) | 事实参考 | 生成 | 按版本分组的变更记录 | `python scripts/gen_changelog.py` |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | 操作指南 | 手写 | 开发流程、测试、提交与 PR 规范 | 流程变化时人工更新 |

**角色边界**（防止同一件事在多处各写一遍）：

- **架构解释**回答「是什么 / 为什么这样切分」，只保留能建立心智模型的那一层，细节一律链接出去；
- **操作指南**承载可执行步骤（命令、检查清单、排错流程），面向「我要做某件事」；
- **事实参考**承载会随代码变化的清单与字段（默认值、字段表、特效名），尽量由脚本生成；
- **决策记录**只写取舍的理由与代价，不复述做法。

复述别处的细节时，只保留「结论 + 链接」，权威定义放回上面对应的那一类。

## 文档如何更新

- **生成物**：`python scripts/gen_docs.py` 重新生成 [reference/](reference/)；
  `python scripts/gen_docs.py --check` 只校验，不一致返回退出码 1（CI 里跑的就是它，见 `tests/test_docs.py`）。
  生成文件开头有「请勿手工编辑」标记，手工改动会被下一次生成覆盖。
- **手写文档**：没有生成式校验，改动相关代码时需同步更新；`tests/test_docs.py` 会保证
  每个包都在 `architecture.md` 的模块职责表里出现过、文档内的相对链接都指向存在的文件，
  文风约定（无人称、陈述句、破折号写法）由 `tests/test_docs.py` 的 `test_handwritten_docs_style` 检查。
- 生成的文档里不含时间戳与版本号：内容只由代码决定，否则 `--check` 永远判定为不一致。
- **变更记录**：`python scripts/gen_changelog.py` 从 git 历史生成 [`CHANGELOG.md`](../CHANGELOG.md)，
  发布前刷新一次；它不接 CI，原因见 [operations/release.md](operations/release.md) §4。
- **ADR**：做出设计取舍时在 [decisions/](decisions/README.md) 新增一条，并在该目录 README 的索引里登记
  （`tests/test_docs.py` 会检查索引完整性）。
- `docs/` 里还有几份历史本地草稿（`plan.md`、`iat_ws_python3.py`、`superpowers/`、
  `ChatHistoryWindow_*.md`），被 `.gitignore` 忽略、不进仓库；`tests/test_docs.py` 的忽略
  清单与 `.gitignore` 保持一致，要新增忽略项时两边都得改。

## 三条捷径

1. 初次阅读代码：`architecture.md` §1 的分层图与 §4 的决策数据流给出全局，`§9` 用于按包定位职责。
2. 新增功能：`architecture.md` §12「常见改动入口」给出改动位置与连带步骤。
3. 判断改动是否踩线：`architecture.md` §11「关键约定与不变量」列出红线，其中已由测试守住的部分标注了 rule id。

## 文风约定

手写文档统一遵守下面几条，评审与 `tests/test_docs.py` 都按此检查：

- **无人称**：不写「你」「您」这类第二人称，也不写命令句（「请先运行…」）；
  事实与后果用陈述句表达（「运行…会重新生成…」）。提示词章节名等被引用为术语的原文（如「你惦记着的事」）保留不变。
- **陈述优先**：规则能一句话说清时不拆成祈使步骤；确需顺序时用编号列表，条目本身仍写陈述句。
- **标题名词化**：小标题写主题（「配置项参考」「发布检查清单」），不写动作指令。
- **破折号统一为 ` - `**：行文中不用 `——` / `—`。
- **不用拟人化与口语化形容**：写机制本身，不写「打架」「白拿」「断片」「跑偏」「吞掉」「孤儿配置」
  这类比喻或俏皮话，改用准确的因果描述（「两处指令冲突」「凭空多出加成」「上下文突然中断」
  「台词偏离事件」「丢失产出」「无引用配置」）。
  已经是通用技术术语的说法保留（如饥饿 starvation、兜底、看门狗、设计债），产品文案（感受描述、台词）也不在此列。
- **单句段落合并**：相邻的单句段落能合并时合并，避免零碎断点。
- **不重复**：同一事实只在一个权威位置展开，其余位置保留结论与链接，角色分工见上文「角色边界」。
