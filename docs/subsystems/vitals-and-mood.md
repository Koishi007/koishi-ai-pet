# 生理与心理数值（vitals / mood）

桌宠的「状态」由两组数值组成，它们每轮都被翻译成自然语言注入提示词，也会驱动动画与粒子。
代码在 `pet/pulse/vitals.py`、`pet/pulse/mood.py`。

术语见 [glossary](../glossary.md) 的「数值与需求」一节。

## 1. 数值模型

| 数值 | 含义 | 范围 | 初始值 | 落库 |
|---|---|---|---|---|
| `satiety` | 饱食度 | 0~100 | 100 | `vitals` 表（单行 `id=1`） |
| `energy` | 精力 | 0~100 | 100 | 同上 |
| `affection` | 好感度 | 0~100 | 60 | `mood` 表（单行 `id=1`） |
| `joy` | 愉悦度 | 0~100 | 70 | 同上 |
| `sanity` | 理智值 | 0~100 | 80 | 同上 |

两表都在 `pet.db` 里（与记忆共用），单行快照；每次 `save()` 覆盖写。
另有只读的下限档位布尔（`is_hungry` / `is_tired` / `*_low`，阈值 30）进入 `numeric_summary()`。

## 2. 数值的变化来源

### (a) LLM 输出行（主来源）

格式：`Mood: affection±值 joy±值 sanity±值`、`Vitals: satiety±值 energy±值`（`_MOOD_GUIDE` / `_VITALS_GUIDE`）。
解析在 `pet/brain/parsing.py` 的 `parse_mood_line` / `parse_vitals_line`（流式与非流式共用），
**只取第一条**，键名白名单外的忽略，数字必须带符号。
每个任务轮次结束后由 `PetAgent._on_brain_result` 逐键调用 `modify_*` 落地。

**幅度限制**：`affection` / `joy` 单次最多 ±5；`sanity` **不截断**（一行 `Mood: sanity-80` 可以直接生效）；
`satiety` / `energy` 无单次上限。提示词里给了投喂与觅食的建议幅度表，但那是建议、不是约束。

### (b) 动作消耗（每秒一次）

`scheduled_tasks._vitals_tick` 挂在 **fast tick**（默认 1 秒）上，按当前动作查 `ACTION_VITALS_DELTA`
并调用 `Vitals.apply_action_delta`：

| 动作 | satiety | energy | | 动作 | satiety | energy |
|---|---|---|---|---|---|---|
| `bounce` | −0.02 | −0.5 | | `walk` / `drive` | −0.008 | −0.05 |
| `rotate` | −0.005 | −0.015 | | `fishing` | −0.008 | −0.005 |
| `shake_arms` | −0.003 | −0.015 | | `calling` | −0.003 | −0.015 |
| `stretch` | −0.003 | −0.01 | | `thinking` / `look_around` | −0.003 | −0.005 |
| `sit` | −0.003 | 0 | | `finger_heart` | −0.003 | −0.005 |
| `sleep` | −0.003 | **+0.05** | | 表外动作 | 0 | 0 |

表外动作（`shy` / `confuse` / `bathing` / `dejected` / `grim` 等）静默不消耗 - 注意这张表没有测试覆盖，
键名写错不会有任何报警。

### (c) 交互事件

- **摸头**（单击且 200ms 内没有移动）：`modify_sanity(+1.0)`、`modify_joy(+1.0)`，并喷 `hearts` 粒子。
- **投喂 / 自觅食**：只触发一次 `interact` 决策，数值仍由 LLM 的 `Vitals:` 行给。
- 抓起、放下、窗口消失等交互不直接改数值。

### (d) 自然衰减（仅 mood，仅 slow tick）

`Mood.apply_decay`（slow tick，默认 5 分钟）：

- **只处理 `joy` 与 `affection`**：高于基线就减一个步长、低于基线就加一个步长，**不越过基线**；
- 基线来自 `MOOD_{JOY,AFFECTION}_BASELINE`（默认 50 / 50），步长来自 `MOOD_*_DECAY_PER_TICK`（默认 2.0 / 0.2）；
  config 描述写的是「点/300 秒」，实现是「每 slow tick 一步」，只有调度间隔是默认值时才等价；
- `MOOD_GRACE_SECONDS`（默认 60）内的任何 `modify_*` / `set_*` 都会重置免衰减窗口，
  模型每轮小幅调整就能长期压制衰减；
- **`sanity` 不参与衰减**：它完全由事件与模型驱动（见 `MoodDecayConfig` 的注释）；
- **vitals 没有衰减**：饱食与精力只随动作变化，不动就永远不变。

## 3. 阈值与信号

档位硬编码在 dataclass 里（vitals 侧叫 `Thresholds`、mood 侧叫 `MoodThresholds`，30 / 10 两档），不可配置。
`check_thresholds` 在 slow tick 调用，档位信号有防抖（重启时按当前值初始化，避免补发）。

现实是除了 `affection_increased`，其余信号都没有消费者 - `hungry` / `starving` / `tired` /
`exhausted` / `recovered` / `affection_low` / `joy_low` / `sanity_low` / `mood_recovered` 等目前只打日志。
真正驱动行为的是下面两条直读路径：

- 提示词的需求注入用 `< 60`（见 §4）；
- 理智低于 `SANITY_CRITICAL_THRESHOLD`（默认 20）时切 `grim` 动画、每 2 个 fast tick 喷 `dark_hearts`。

也就是说，调 30/10 这套档位不会改变桌宠行为，除非有代码消费这些信号。

## 4. 数值如何影响提示词

本节是**档位、阈值与文案**的权威定义；注入机制（哪些任务注入哪个块、块的拼接与生命周期）见
[context-and-prompts.md](context-and-prompts.md) §1、§3、§4。

`ContextBuilder._build_feeling()` 把数值译成一句话，注入 system 的 `[你现在的状态]` 块（所有任务都注入）：

| 数值 | ≥80 | ≥60 | ≥40 | ≥20 | <20 |
|---|---|---|---|---|---|
| satiety | 肚子不饿，暂时不想吃东西， | （中性，不输出） | 肚子有点空了。 | 饿得肚子咕咕叫。 | 快要饿死了，眼前发黑。 |
| energy | 精神饱满， | （中性，不输出） | 眼皮开始打架了。 | 累得抬不起手。 | 连站都站不稳了，只想瘫着不动。 |
| affection | 特别亲近， | （中性，不输出） | 感觉一般， | 不太想搭理人， | 不想搭理人， |
| joy | 开心得想转圈， | （中性，不输出） | 心情有点闷。 | 心里沉甸甸的，笑不出来。 | 绝望到想消失。 |
| sanity | 由 `SANITY_CRITICAL_THRESHOLD` 派生三档：≥2/3 阈值「有点神神叨叨」、≥1/3 阈值「脑子快炸了」、其余「理智彻底崩坏」+ 安全约束 | | | | |

`ContextBuilder._build_needs_note()` 用同一套刻度判定「未满足的需求」：

- 阈值 `_NEED_THRESHOLD = 60`，**与 `_build_feeling` 的档位对齐**（代码里有注释声明这条约束：
  60 在两处都得是中性档）；
- `sanity` 用 `config.SANITY_CRITICAL_THRESHOLD`（默认 20）而非 60 - 理智平时就在阈值附近徘徊，
  用 60 会把正常状态全算成「惦记」；
- 注入端只在 `autonomous` / `chat` 两个任务生效（`_NEEDS_TASKS`），`interact` 不注入；
  哪些块在哪个任务出现见 [context-and-prompts.md](context-and-prompts.md) §1、§4。

作息需求（`_circadian_need`）与数值无关，纯按钟点：23:00~06:00 为 `bedtime`（超过 2 小时改口「熬夜太久了」）、
13 点为 `drowsy`。system prompt 里不写具体钟点（钟点每轮都变，会破坏 prompt 缓存）。

## 5. 持久化

- 保存时机：slow tick **无条件** `save()`（`_vitals_save` / `_mood_save`），退出时 `close()` 再存一次；
  `apply_decay` 有变化时也会立即保存。**没有脏标记或 debounce**。
- 崩溃/强杀最多丢 300 秒：vitals 丢的是这段时间的动作消耗累计，mood 丢的是 LLM 增量、摸头加成与衰减步进。
- 写失败只告警、值留在内存等下轮；连接 `busy_timeout` 建表后收紧到 500ms。
- 精度：vitals 存 `round(...,3)`，mood 原值直存。

## 6. 不变量与陷阱

1. **两套 sanity 阈值互不相通**：`MoodThresholds.sanity_low/mad`（30/10，只影响信号）与
   `SANITY_CRITICAL_THRESHOLD`（20，影响动画/粒子/需求/感受）。调前者不改行为，调后者才改。
2. **`modify_sanity` 不受 ±5 限制**，改提示词或加校验时需要保留它的表达自由度。
3. **改 `ACTION_VITALS_DELTA` 的键要与动作名完全一致**（动作名真源是 `pet/action/registry.py` 的 `ACTION_NAMES`），
   且这张表没有测试，加动作时需同步，否则新动作不消耗。
4. **改 `SCHEDULER_FAST_MS` 会成比例改变动作消耗强度**（结算是 1 Hz 耦合的）；
   改 `SCHEDULER_SLOW_MS` 会改变衰减实速与保存频率。
5. **改档位或阈值要连带改测试与文档**：阈值边界（`<60` 与 `=60` 的差异）、sanity 用临界值、四类需求映射、
   作息压掉「歇一歇」等都有用例（`tests/test_context_notes.py`）；改提示词里的数值说明会改生成物
   `docs/reference/prompt-blocks.md`，需要运行 `python scripts/gen_docs.py`。
6. **解析逻辑改动要同步流式与非流式两条路径**（`tests/test_brain_parsing.py` 会检查二者一致）。
7. `pet/pulse/*` 的真库读写、衰减与信号**没有直接单测**（测试里用的是 `_FakeVitals` / `_FakeMood`），
   改引擎行为时需手动验证一次或补测试。
