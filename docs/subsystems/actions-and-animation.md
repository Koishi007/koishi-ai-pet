# 动作、动画与粒子

从 LLM 输出一行 `Action:` 到画面播完、结算、飘出粒子，涉及的模块与约束都记在这里。
相关文件：`pet/action/`（动作与队列）、`pet/ui/pet_animations.py`（帧动画）、`pet/ui/particle.py`（粒子）。
动作清单见 [reference/actions.md](../reference/actions.md)，特效清单见 [reference/effects.md](../reference/effects.md)。

## 1. 一次动作的完整链路

| 步骤 | 位置 |
|---|---|
| 行识别（流式 / 非流式） | `pet/brain/parsing.py`：流式走 `BehaviorParser.collect_stream`，非流式走 `parse_behavior`，共用同一套行标签 |
| 动作名与参数校验 | `parse_action_line`：名字不在 `ACTION_NAMES` 里直接丢弃并告警；`k=v` 与位置参数会尝试转 int |
| 无动作兜底 | 非流式解析器会在一条 `Action` 都没有时补 `sit 5s`；流式路径没有这个兜底 |
| 批量下发 | `PetAgent._on_brain_result`：先发 `action_batch_started`（重置本轮产出去重），再逐条 `_emit_action` |
| 时长注入 | `PetAgent._emit_action`：`has_duration()` 为真且没给秒数时用 `default_duration()` |
| 入队 | `agent.action_requested` → `PetWindow.queue_enqueue_action` → `ActionQueue.enqueue` |
| 调度与执行 | `ActionQueue._run_next`：`getattr(self._actions, name)` 反射调用；找不到方法静默跳过；方法抛异常时不发开始信号直接推进 |
| 结束判定 | 见下表 |
| 超时兜底 | `_anim_timeout_ms()` / `_on_action_timeout()`：**超时算正常结束**并继续结算 |
| 产出结算 | `_settle_current` → `PetWindow._on_action_finished` → `outcome.outcome_for()` → 写事件 / 播特效 |
| 事件进上下文 | `note_event` / `note_once_event` → 注入 Behavior 的近期事件与一次性事件 |

### 三类结束条件

| 类型 | 判据 | 有无超时 |
|---|---|---|
| 窗口属性动画（`move_to` / `bounce` / `fade_in` / `fade_out`） | `QPropertyAnimation.finished` | **无**（只靠 finished） |
| `drive` | 自定义 `walk_finished` 信号 | 有 |
| 帧动画（驻留类） | `PetAnimator.animation_finished` | 有 |
| 素材缺失导致起不来 | `not anim.is_playing` → 立即推进，不干等 | 不适用 |

超时时长：`max(1000, ACTION_TIMEOUT_MS)`，带 `duration` 的动作是 `duration*1000 + 2000`。
`clear()` / `stop()` / `pause()` **不算完成**，不会发 `action_finished`（拖拽打断即走这条）；
但「动作播完的瞬间正在下落」时必须先结算再挂起，否则产出会随 pause 丢失。

## 2. 动作类型的实现差异

| 类型 | 动作 | 做法 |
|---|---|---|
| 移动·帧动画驱动 | `walk` | 先播 `walk_<dir>` 帧动画，再返回一个 sentinel 属性动画做位移（每 50px 一跳、hop 150ms、间隔 250ms）；用 `QTimer.singleShot(0)` 规避「起手同步 stop 导致信号错过」 |
| 移动·自接管物理 | `drive` | 返回字符串 `"drive"`，自己起 30ms 定时器做 3px/tick 的推进，复用重力的地面/窗口检测；到目标或触边后发 `walk_finished` |
| 移动·位移 | `bounce` | 帧动画 + OutCubic 属性动画，最高点被屏幕顶部夹住 |
| 显隐 | `fade_in` / `fade_out` | 只改 `windowOpacity`；`fade_out` 有 15 秒安全兜底把窗口找回 |
| 驻留 | 其余动作 | 只调 `PetAnimator.play()`，靠 `animation_finished` 结束；期间 `suppress_idle=True` 防止待机动画覆盖 |

**时长动作**只有六个（`sit` / `thinking` / `sleep` / `calling` / `finger_heart` / `bathing`），
范围随时间间隔动态计算：目标总时长 = `SCHEDULER_MID_MS/1000 × 0.9`（默认 270s），
兜底时长 = `max(最小秒数, 目标 × 比例)`，区间 = `(最小, max(最小+5, 兜底×2))`。
`duration` **只对 `loop: true` 的动画生效**；其它动作即使模型写了数字也只能当 kwargs 传下去。

**队列与重力联动**：下落中队列 `pause()`，落地 `resume()` 并喷 `dust`；
下落超过 `FALL_DOWN_SECONDS`（默认 1.5s）时，落地先播 `fall_down`，播完再 `resume()`；
动作结束时先手动跑一次重力 tick，
若恰好进入下落则「先结算、再挂起」；拖拽时 `pause + clear + grabbed()`，松手 `resume()`，
速度超过 80px/s 会以 `apply_impulse()` 抛出。

## 3. 帧动画的加载与运行

素材侧契约（目录、命名、json 字段与取值）见 [assets-pipeline.md](assets-pipeline.md) §2；
本节只覆盖运行期的消费方式：

- **tick 与 FPS 耦合**：一个 tick = `round(1000 / PET_FPS)` ms（默认 15 FPS → 67ms），
  所以 `tick_counts × tick` 决定一次循环或一次性播放的时长 - 改 `PET_FPS` 会整体改变动画速度。
- **容错都在加载期静默修**：`frame_ratios` 与帧数对不上（或有非正数）→ 丢弃改等分；
  长度对但总和偏离 1 超过 0.01 → 按比例归一化；`tick_counts` 非正或小于帧数 → 抬到帧数；
  `breath` 数值越界 → 夹紧到边界，类型不对才整块忽略。以上都只打 warning，画面上看不出被改过。
- **加载失败即跳过**：缺 json / JSON 损坏 / 目录无帧图 → `play()` 返回 False，
  队列打 warning 后跳过该动作；启动时若连 `idle` 都没有，会退化成 emoji 占位。
- 动作配置**有缓存且没有失效入口**：换素材或改 json 之后需要重启才生效。
- 呼吸姿态的位移只向上抬、缩放锚点由绘制方保证在脚底。

## 4. 粒子

- 注册表是 `particle.py` 的 `_SPAWNERS`（10 个特效）；默认纵向位置在 `ParticleWidget._DEFAULT_Y`，
  未登记的特效按宠物窗口高度的 1/3 兜底，`dust` 用 `-1` 表示脚底。
- 粒子窗口比宠物大 `_MARGIN = 100px`，**超出即被裁** - `fish` 的上浮高度就是按这条约束反推的
  （1.4px/tick × 1.5s ≈ 70px，保证 emoji 完整淡出）。
- tick 30ms；前 70% 生命周期不透明，后 30% 线性淡出；空且无加载动画时自动停表并隐藏。
- 触发来源：
  - **动作映射**：`scheduled_tasks._ACTION_PARTICLES`（`shake_arms`/`rotate` → `stars`、
    `finger_heart`/`shy` → `hearts`、`confuse` → `question_marks`、`calling` → `notes`、
    `sleep` → `zzz`、`bathing` → `bubbles`、`dejected` → `spiral`），间隔单位是 **fast tick**（默认 1 秒）；
  - **状态驱动**：理智 < `SANITY_CRITICAL_THRESHOLD` 时每 2 个 fast tick 发 `dark_hearts`，并切 `grim` 动画；
  - **事件驱动**：好感提升 → `hearts`；落地 → `dust`；钓鱼命中 → `fish`（由产出结算给出）。
- 调试面板：「粒子特效测试」按钮由 `ParticleWidget.effect_names()` 自动生成（新增特效无需改面板）；
  「动画测试」列出 `available_actions()` 并可选 FPS 与循环。

## 5. 不变量与陷阱

**新增动作**需要同步的位置与漏改后果：

| 步骤 | 漏改后果 |
|---|---|
| 放素材 `assets/actions/<name>/` + 同名 json（契约与检查清单见 [assets-pipeline.md](assets-pipeline.md) §2、§3） | `play()` 返回 False，动作被队列跳过（打 warning） |
| 在 `PetActions` 写方法 | 反射取不到 → 静默跳过（不报错） |
| 在 `pet/action/registry.py` 注册（带时长还要进 `_DURATION_ACTION_DEFS`） | 模型输出这个动作名会被丢弃 |
| 可选：`_ACTION_PARTICLES`（特效）、`ACTION_VITALS_DELTA`（消耗）、`outcome.register()`（产出） | 无特效 / 不消耗 / 无事件 |
| 运行 `python scripts/gen_docs.py` | `tests/test_docs.py` 的漂移检查会失败 |

**新增粒子特效**只需登记 `_SPAWNERS`（需要非默认位置再补 `_DEFAULT_Y`），调试面板自动列出；
若希望某个动作触发，还需在 `_ACTION_PARTICLES` 或产出的 `effect` 里引用同名。

其它容易踩的：

1. **属性动画路径没有超时保护**，只靠 `finished`；新增位移动作时需自行保证结束时机。
2. **动作名 ≠ 素材目录名**：`walk` → `walk_left/right`、`drive` → `driving_left/right`；
   `fade_in/fade_out` 没有素材；`idle` / `falling` / `grim` / `grabbed` 是系统动画，不在动作注册表里。
3. **`grim` 不是模型能输出的动作**，由理智驱动切换。
