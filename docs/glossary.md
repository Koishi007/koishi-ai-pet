# 术语表

项目里有一批自造词，它们在代码、提示词、文档里反复出现。这里给出每个词的含义与所在位置，
按「感知与决策 → 数值与需求 → 记忆 → 动作与表现 → 工程词」排列。

## 感知与决策

| 术语 | 含义 | 在哪 |
|---|---|---|
| **感知（perception）** | 桌宠对环境的输入：窗口探测结果、截图、当前时间 | `pet/brain/context_builder.py` |
| **mode（模式）** | 提示词里的感知组合：`autonomous_vision` / `autonomous_non_vision` / `chat_vision` / `chat_non_vision` / `interact` | `pet/brain/prompts.py` 的 `_PERCEPTION_SECTIONS` |
| **task（任务）** | 决策类型：`autonomous`（自主）/ `chat`（对话）/ `interact`（即时交互） | `pet/brain/prompts.py` 的 `_TASK_SECTIONS` |
| **脑线程 / 管线（pipeline）** | 一次决策的完整执行体：组上下文 → 调 LLM → 解析 → 工具轮次 | `pet/agent/pet_agent.py`、`pet/brain/behavior.py` |
| **工具轮次（tool round）** | LLM 一次「请求工具 → 拿到结果 → 继续说话」的往返，受 `LLM_TOOL_MAX_ROUNDS` 限制 | `pet/brain/tool_loop.py` 的 `run_tool_loop` |
| **元工具（meta tool）** | 系统内置、不可禁用、**不占工具轮次配额**的工具：`tool_search`、`food`、`game`、`recall` | `pet/tools/registry.py` |
| **aside** | 工具调用的附带参数：让桌宠在动手的同时说一句话，保证言行一致 | `pet/tools/registry.py` 自动追加 |
| **看门狗（watchdog）** | 检测脑线程「长时间无进展」并在超时后强制恢复的机制 | `pet/agent/scheduled_tasks.py` 的 `_brain_watchdog` |
| **进展（progress）** | 脑线程的心跳：收到流式 chunk、完成工具轮次等都会上报，用于喂看门狗 | `Behavior.note_progress` |

## 数值与需求

| 术语 | 含义 | 在哪 |
|---|---|---|
| **vitals（生理数值）** | 两个维度：`satiety` 饱食度、`energy` 精力。随动作消耗，靠吃饭/睡觉恢复 | `pet/pulse/vitals.py` |
| **mood（心理数值）** | 三个维度：`affection` 好感度、`joy` 愉悦度、`sanity` 理智值 | `pet/pulse/mood.py` |
| **基线（baseline）** | 数值在无事件时缓慢回归的目标值；长期不互动会回落到基线 | `MoodDecayConfig` / `config.MOOD_*` |
| **阈值检查（check_thresholds）** | slow tick 触发的数值越界检查，会发出「饿了/累了/理智低」等信号 | `pet/pulse/*.py` |
| **需求（needs）** | 数值跌破阈值后形成的持续张力，是「惦记」的来源；只统计未满足项 | `context_builder._build_needs_note` |
| **「你惦记着的事」** | 注入 system prompt 的章节：未满足需求 + 该怎么做 + 忽然想起来的旧事 | `context_builder._build_needs_note`、`_memory_event_note` |
| **需求做法（hint）** | 每个需求对应的建议做法（讨食、去睡、撒娇…），集中在 `_NEED_HINTS` | `context_builder._NEED_HINTS` |
| **作息需求（circadian）** | 按时段出现的困倦需求（深夜「困了/熬夜太久了」、午后「犯困」），与数值无关 | `context_builder._circadian_need` |
| **感受描述（feeling）** | 把数值翻译成自然语言的一句话（「饿得肚子咕咕叫」），注入 system 的 `<<FEELING>>` 锚点 | `context_builder._build_feeling` |
| **求关注提示（attention hint）** | 连续 N 轮没和用户互动时注入的提示，档位由 `ATTENTION_THRESHOLDS` 决定 | `prompts.build_attention_hint` |
| **最近发生了什么** | 近期事件备忘（动作结束、工具结果等）在保鲜窗口内注入上下文 | `context_builder._recent_events_note` |
| **觅食（food）** | 由饱食度驱动的自主行为：生成地面食物并走过去吃掉，总开关 `FOOD_ENABLED` | `pet/food/food.py` |

## 记忆

| 术语 | 含义 | 在哪 |
|---|---|---|
| **Memory 行** | LLM 输出里以 `Memory:` 开头的行，是记忆的写入来源 | `pet/brain/memory.py` 的 `save_from_line` |
| **记忆类别（category）** | 记忆的类型，如 `event`（发生的事）、`user_fact`（用户事实）等 | 同上 |
| **重要度（importance）** | 记忆的权重，影响检索排序与淘汰 | 同上 |
| **层级（level）** | `L1`/`L2`/`L3` 三档，越高越容易过期（`MEMORY_L3_EXPIRE_DAYS`） | 同上 |
| **召回（recall）** | 每轮对话注入的记忆片段：核心记忆 + 近期记忆 + 相关性检索（MMR） | `memory.py` 的 `retrieve_context` |
| **旧事（random event）** | 随机抽取的历史事件记忆，作为「忽然想起来」注入「你惦记着的事」章节 | `memory.py` 的 `random_events`，条数 `MEMORY_EVENT_RECALL_COUNT` |
| **访问统计（access stats）** | 记忆被召回时的计数与时间戳，用于计算有效权重；被动注入不更新它 | `memory.py` |

## 动作与表现

| 术语 | 含义 | 在哪 |
|---|---|---|
| **动作（Action）** | LLM 可输出的行为，如 `sit`、`walk`、`sleep`。定义在注册表里 | `pet/action/registry.py` |
| **动作分类** | `移动` / `驻留` / `显隐` 三类，只影响提示词里的展示顺序 | 同上 |
| **时长动作** | 需要指定秒数的动作（`sit`、`sleep`、`thinking`…），可用范围随调度间隔动态计算 | `registry._DURATION_ACTION_DEFS` |
| **动作队列（ActionQueue）** | 串行动作播放器：一个动作播完再播下一个，带超时兜底 | `pet/action/action_queue.py` |
| **重力系统（GravitySystem）** | 让桌宠下落、站在窗口边缘、被抛出 | `pet/action/gravity.py` |
| **动作产出（outcome）** | 动作正常结束后按规则结算的额外结果（一次性事件或窗口期事件），钓鱼玩法由此接入 | `pet/action/outcome.py` |
| **帧动画配置** | 每个动作目录下的 `<name>.json`：`desc`、`tick_counts`（循环总 tick）、`frame_ratios`（每帧占比，和=1.0，按文件名排序对应）、`loop`、`note` | `pet/ui/pet_animations.py` |
| **呼吸（breath）** | 帧动画上叠加的姿态：`amplitude`（位移 px，≤4）、`scale_x`/`scale_y`（0.5~2.0）、`period_ticks` | 同上 |
| **粒子特效（effect）** | 名字 → 生成函数的注册表；动作或事件按名字触发 | `pet/ui/particle.py` 的 `_SPAWNERS` |
| **帧序** | 动作目录里帧文件的排序结果，决定播放顺序 | `pet/ui/pet_animations.py` |
| **素材规格** | 每帧是 512×512 的透明 webp，配一个同名 json，落到 `assets/actions/<name>/`；去背与压码用什么工具都可以 | [subsystems/assets-pipeline.md](subsystems/assets-pipeline.md) |

## 工程词

| 术语 | 含义 | 在哪 |
|---|---|---|
| **调度器（Scheduler）** | 三档定时器：`fast`（默认 1s）、`mid`（默认 5min）、`slow`（默认 5min）；系统空闲时整体暂停 | `pet/agent/scheduler.py` |
| **状态机（StateMachine）** | `IDLE` / `AUTONOMOUS` / `INTERACTING` 三状态，带合法迁移校验 | `pet/agent/state.py` |
| **配置项真源** | `_KEY_META`：每一项的类型、默认值、分组、是否需重启、是否隐藏 | `pet/config.py` |
| **设置页签** | 设置界面的「连接/行为/提示词/通用」四页，归属是界面代码手写的，与 `category` 不同 | `pet/ui/settings_window.py` |
| **参考文档（reference）** | 由 `scripts/gen_docs.py` 从代码生成的文档，CI 校验是否漂移 | `docs/reference/` |
| **单实例锁** | 防止重复启动的 `QLockFile`，与 `settings.json` 同目录 | `pet/single_instance.py` |
| **启动标记 / 崩溃报告** | 运行状态标记与异常落盘，用于下次启动时判断上次是否异常退出 | `pet/crash_reporter.py` |
| **prompt 缓存** | `LLM_CACHE_PROMPT`：保持 system 前缀稳定以命中服务端缓存 | `pet/brain/llm_client.py` |
