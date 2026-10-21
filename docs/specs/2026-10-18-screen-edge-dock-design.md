# 屏幕边缘吸附设计

> 屏幕左右边缘可吸附：拖拽松手靠近边缘或甩飞撞到边缘时，桌宠贴到边缘并换成 `*_edge` 姿势，左侧镜像复用同一套素材。贴边限制的是活动范围，不停止调度器与对话；贴边时 system 动作表只保留可用的贴边动作，任务段只要求至少 1 个动作、不限制时长，保留的动作名本地路由到 `*_edge` 素材，入队处仍拦截在途结果与本地兜底里的非贴边动作。模型通过 user 段的贴边状态获知约束，无法完成的请求直接答复做不到；觅食工具在贴边期间禁用，饥饿只保留乞食路径。

## §0 背景与现状

| 项 | 现状 | 位置 |
|---|---|---|
| 贴边素材 | 10 个目录，512×512，主体右缘贴画布右缘；非透明区左界在 x=181~249，可见宽度占窗口 56%~65% | `assets/actions/*_edge/` |
| 素材与动作的对应 | `idle` / `grim` 为系统动画；`sleep` / `thinking` / `shy` / `confuse` / `embarrassed` / `dejected` / `finger_heart` / `shake_arms` 有贴边帧，其余动作没有 | `pet/action/registry.py`、`assets/actions/` |
| 窗口 | `PET_WIDTH × PET_HEIGHT` 默认 125×125，全窗口透明，未设置 input mask；透明区同样接收鼠标事件 | `pet/ui/pet_window.py` |
| 拖拽释放 | 松手恢复重力，速度超过 80px/s 时调用 `apply_impulse()` | `pet/ui/pet_window.py` |
| 甩飞碰撞 | `_tick_flick` 在左右边界按 `_EDGE_BOUNCE` 反弹 | `pet/action/gravity.py` |
| 动作队列 | 按逻辑动作名反射调用；`current_action_name()` 供 `_vitals_tick` 与动作粒子查表 | `pet/action/action_queue.py`、`pet/agent/scheduled_tasks.py` |
| 待机动画 | `_update_idle_anim` 比较 `idle` / `grim`；`_unconsciousness` 只在 `current_action == "idle"` 时计时 | `pet/agent/scheduled_tasks.py` |
| 提示词 | system 为静态前缀 + `<<FEELING>>` 运行时块；user 段承载时间、窗口探测、用户消息与截图 | `pet/brain/prompts.py`、`pet/brain/context_builder.py` |
| 觅食 | `food` 为元工具，`FOOD_ENABLED` 时注册；`_FOOD_GUIDE` 与饥饿需求文案都指向“生成食物再走过去” | `pet/tools/registry.py`、`pet/brain/prompts.py`、`pet/brain/context_builder.py` |

贴边素材的循环语义与动作一致：`sleep_edge` / `thinking_edge` / `finger_heart_edge` 循环，依赖 `duration` 结束；`shy_edge` / `confuse_edge` / `embarrassed_edge` / `dejected_edge` / `shake_arms_edge` 为 `loop: false`，播完即结束；`idle_edge` / `grim_edge` 循环，供系统待机切换。

## §1 目标

- 屏幕左、右边缘各有一个贴边状态；贴边后窗口固定在该边缘，不再移动，也不参与重力与甩飞。
- 左侧贴边镜像复用右侧素材，不新增素材目录。
- 贴边期间只保留可见侧窗口区域参与鼠标命中，内侧透明条不拦截点击。
- 贴边时 system 动作表只列可用的贴边动作；保留的动作名不变，本地把同名动作路由到 `*_edge` 素材，靠边做不了的动作在入队处丢弃。
- 贴边时四个任务段的动作数下限为 1，不要求动作序列总时长；未贴边保持现有的动作数下限（autonomous 另有系统段的总时长要求）。
- 调度器、动作粒子、全部气泡、摸头、文件拖入保持原行为；贴边只限制活动范围。
- 任何任务路径下，user 段都带贴边状态：模型知道自己在贴边、哪些事做不到、用户正在工作或学习。
- 贴边期间禁用 `food` 工具，饥饿只保留向用户讨食的路径。
- 贴边状态只由用户操作产生与解除；重启后回到未贴边状态。

## §2 非目标

- 不做上边缘贴边：没有上方姿势素材。
- 不做沿边缘上下滑动的拖拽模式：任何拖拽都先脱墙，松手靠近边缘时重新吸附。
- 不做多屏相邻边界的特殊判定：当前只面向单屏，窗口所在屏的左、右可用边界即吸附边界。
- 不持久化贴边状态。
- 不新增模型可输出的动作名；贴边动作表由同一注册表过滤得到。
- 不按任务裁剪游戏工具：游戏在贴边期间仍可用，用户主动发起时允许，自主轮由提示词约束。
- 不改气泡的显示与隐藏规则；贴边时情绪气泡锚到可见主体靠内的一侧（贴右靠左、贴左靠右），
  speech 气泡的尾巴对准可见主体（跟着贴边侧走），气泡本体的定位与夹屏逻辑不变。
- 不做走、骑车、跳跃到边缘时的自动吸附：这三类动作保持现有行为。

## §3 状态与触发

### §3.1 状态

`PetWindow` 持有 `_dock_side: "" | "left" | "right"`，公开只读属性 `dock_side` 与 `is_docked`，并在状态变化时发出 `dock_changed(object)`（`"left"` / `"right"` / `None`）。

贴边状态与 `PetState` 正交：`IDLE` / `AUTONOMOUS` / `INTERACTING` 的迁移、看门狗与抢锁逻辑不变。

### §3.2 触发

| 触发 | 条件 | 行为 |
|---|---|---|
| 拖拽松手 | 速度 ≤ 80px/s，且窗口左/右缘距屏幕可用区边界 ≤ `EDGE_DOCK_SNAP_PX` | 吸附到最近的一侧 |
| 甩飞撞边 | `EDGE_DOCK_ENABLED` 为真，`_tick_flick` 到达左/右边界 | 吸附到撞到的一侧；关闭时保持原有反弹 |
| 右键菜单 | 未贴边时“贴到屏幕边缘”，已贴边时“离开边缘” | 先淡出，贴上最近的一侧并把桌宠移到可用区垂直中点，移动动画结束后淡入；或解除贴边 |
| 拖拽已贴边的桌宠 | 按下后移动超过点击阈值 | 先脱墙，松手后按前两条重新判定 |

- 拖拽速度超过 80px/s 时不按松手位置吸附，交给甩飞物理；撞到边缘时由甩飞碰撞决定是否吸附。
- 甩飞撞边吸附时把落点 `y` 夹进屏幕可用区的上/下界：同一 tick 内越过侧边界又越过上下界时，贴边位置不会偏出屏幕或压住任务栏。
- 吸附只由用户操作或甩飞碰撞产生；模型、工具、自主决策都不能让桌宠贴边或离墙。
- `EDGE_DOCK_ENABLED` 为假且未贴边时不显示“贴到屏幕边缘”菜单项；已贴边时“离开边缘”始终可用。
- 拖拽期间（`_grab_local` 非空）不显示“贴到屏幕边缘”：抓取点仍活跃，后续 `move()` 会覆盖贴边位置，松手又会走重力分支，会留下“已贴边但已离开边缘”的混合状态。
- 菜单贴边的淡出（300ms）期间开始拖拽时，淡出回调不执行贴边，只淡入：抓取中调用 `dock_to` 会与拖拽的 `move()` 争夺窗口位置。
- 菜单贴边按窗口中心与屏幕中线比较：中心在中线左侧贴左、否则贴右；正好在中线时贴右（桌宠默认在屏幕中间）。
- 贴边期间 `EDGE_DOCK_ENABLED` 关闭只影响后续触发，已在墙上的状态保留到用户解除。

### §3.3 吸附与解除

`PetWindow.dock_to(side)`：

1. 记录 `_dock_side`，发出 `dock_changed(side)`。
2. `pet_actions.set_dock_side(side)`，`_SpriteLabel.set_flip(side == "left")`。
3. `action_queue.clear()`，`swing_ctrl.land()`，`pet_actions.idle()`（贴边后映射为 `idle_edge`）。
4. `gravity.enable(False)` 并清空下落/甩飞状态（`reset_motion()`），避免 `gravity.falling` 让动作队列把贴边待机当成下落中。
5. 用 180ms 的 `OutCubic` 动画把窗口 x 移到边缘；x 已在边缘时直接完成。默认 `y` 保持不变
   （贴到当前水平高度的边缘）；只有右键菜单传 `center_y=True`，把 `y` 移到可用区垂直中点
   （`geo.center().y() - height // 2`，再夹进上下界）。
6. 动画结束后设置 `QRegion` 掩膜；动画期间保持原窗口形状，避免滑入过程被裁切。

`PetWindow.undock()`：

1. 停止吸附动画，`clearMask()`，`_SpriteLabel.set_flip(False)`。
2. `pet_actions.set_dock_side("")`，`_dock_side` 置空，发出 `dock_changed(None)`。
3. 清空动作队列，停掉正在播放的贴边动作。
4. 需要恢复重力时 `gravity.enable(True)`；桌宠在空中的部分由重力接管。
5. `pet_actions.idle()` 立即回到普通姿势；随后若进入下落，重力切换为 `falling`。

拖拽入口调用 `undock(resume_gravity=False)`，避免解除贴边与拖拽关闭重力之间出现一次 30ms 的下落 tick。

## §4 渲染与命中

### §4.1 镜像

`_SpriteLabel.set_flip(bool)` 保存标志并触发重绘。`paintEvent` 在绘制贴图前做水平翻转：

```python
if self._flip:
    painter.translate(self.width(), 0)
    painter.scale(-1, 1)
```

- 贴图本身不复制、不缓存镜像版本，DPR 重建（`rebuild_frames`）与帧切换不受影响。
- 呼吸缩放与点击 Q 弹的锚点在水平中线上，镜像与它们可叠加；贴边时 `swing_ctrl.land()` 已把残余摆角归零。
- 快速渲染路径的条件加入 `not self._flip`：翻转时必须走完整绘制路径。

### §4.2 点击掩膜

`edge_mask_rect(side, width, height)` 返回可见侧的矩形：

| 侧 | 区域 |
|---|---|
| 右 | `x = width - round(width * EDGE_VISIBLE_RATIO)`，宽 `round(width * EDGE_VISIBLE_RATIO)`，高 `height` |
| 左 | `x = 0`，宽同上，高 `height` |

`EDGE_VISIBLE_RATIO = 0.70`。现有贴边素材的最宽可见区为 `shake_arms_edge` 第 1 / 3 帧的 64.3%，0.70 留出余量；新增贴边素材若主体更靠内，需要重新核对这个比例。

- 掩膜由 `PetWindow.setMask(QRegion(...))` 应用，解除贴边时 `clearMask()`。
- 掩膜只缩小命中区域，不改变绘制；绘制仍由 `_SpriteLabel` 完成。
- 掩膜是矩形，素材内部的透明像素仍可命中；不再细分到逐像素。

### §4.3 姿势

| 动作 | 未贴边素材 | 贴边素材 |
|---|---|---|
| `idle` | `idle` | `idle_edge` |
| `grim` | `grim` | `grim_edge` |
| `sleep` | `sleep` | `sleep_edge` |
| `thinking` | `thinking` | `thinking_edge` |
| `shy` | `shy` | `shy_edge` |
| `confuse` | `confuse` | `confuse_edge` |
| `embarrassed` | `embarrassed` | `embarrassed_edge` |
| `dejected` | `dejected` | `dejected_edge` |
| `finger_heart` | `finger_heart` | `finger_heart_edge` |
| `shake_arms` | `shake_arms` | `shake_arms_edge` |

### §4.4 气泡

聊天、喂食、音乐、语音、表情、文件气泡全部保留，显示与隐藏规则不变。

| 气泡 | 贴边时的定位 |
|---|---|
| 聊天 / 喂食 / 音乐 / 文件 | 不变：按窗口边缘换到内侧 |
| 语音（speech） | 本体仍以窗口中心为基准，夹进屏幕可用区；尾巴横坐标改取可见主体中心（`_tail_x()` 读 `pet.dock_side`，按 `edge_mask_rect` 的可见区计算），两侧各留 12px 边距 |
| 表情（emotion） | `_final_position` 锚到可见主体靠内的一侧：贴右取可见区左缘，贴左取可见区右缘减气泡宽度；纵向仍在桌宠上缘上方 8px，最后夹进屏幕可用区；未贴边仍在桌宠右上角 |

## §5 动作路由

### §5.1 动作表过滤

注册表新增 `EDGE_ACTION_NAMES`：

```python
EDGE_ACTION_NAMES = frozenset({
    "sleep", "thinking", "shy", "confuse",
    "embarrassed", "dejected", "finger_heart", "shake_arms",
})
```

它是贴边可用动作的唯一真源。贴边时 system 里的 `[可用动作]` 只列这八个，其余动作不发给模型：

```python
_edge_action_section = _Lazy(lambda: generate_action_section(
    exclude=[name for name in ACTION_NAMES if name not in EDGE_ACTION_NAMES]
))
```

- 未贴边时仍用现有的 `_action_section`，内容与现在一致。
- `_Lazy` 的 lambda 在首次求值时读取 `ACTION_NAMES`，避免 `prompts` 导入期触发注册表构建。
- `invalidate_action_section()` 同时失效两份表，调度配置变更后按新配置重算。
- 八个动作名不变；素材侧对应关系为 `f"{name}_edge"`，由 `PetActions` 在播放时取。
- `parse_action_line` 仍按完整 `ACTION_NAMES` 校验；在途 LLM 结果或本地兜底出现表外动作时，由入队过滤丢弃。

### §5.2 本地路由

`PetActions` 增加 `set_dock_side(side)` 与 `idle_animation` / `grim_animation` 属性：

- 八个 `EDGE_ACTION_NAMES` 成员在贴边时播放 `f"{name}_edge"`。
- `idle()` / `grim()` 在贴边时播放 `idle_edge` / `grim_edge`。
- 其余动作方法保留原素材名；动作表不再发送它们，入队过滤兜底。

`PetWindow.queue_enqueue_action(name, args, kwargs)` 在贴边时检查 `name not in EDGE_ACTION_NAMES`，命中则记日志并丢弃。动作表已经只发八个动作，这里的过滤针对在途 LLM 结果、本地兜底与工具交互。丢弃只影响该条动作，不影响同批其它动作与后续批次；整批都被丢弃时队列保持空闲，桌宠继续 `idle_edge`。

`_update_idle_anim` 改为比较 `pet_actions.idle_animation` / `grim_animation`，贴边时自动对应 `idle_edge` / `grim_edge`。

`_unconsciousness` 在 `is_docked` 时直接返回：`unconsciousness` 没有贴边素材，且贴边待机不应进入“无意识倒下”。

### §5.3 队列名与数值

`ActionQueue.current_action_name()` 仍返回逻辑动作名（`sleep`、`thinking` 等），因此：

- `_vitals_tick` 的 `ACTION_VITALS_DELTA` 继续按逻辑名结算，`sleep` 在贴边时正常恢复精力。
- `_ACTION_PARTICLES` 继续按逻辑名触发，`sleep` 出现 zzz、`shake_arms` 出现星星；粒子保留，不因贴边关闭。
- 摸头爱心由 `_on_click_confirmed` 直接生成，保留。
- 好感提升的爱心由 `app.py` 的信号触发，保留。

### §5.4 本地兜底

`local_fallback.decide_local` / `interact_decide_local` / `chat_decide_local` 增加贴边分支，动作池只含 `EDGE_ACTION_NAMES` 成员：

| 入口 | 贴边动作池 |
|---|---|
| 自主兜底 | `thinking` / `sleep` / `shy` / `shake_arms` / `confuse` |
| 交互兜底 | 投喂与通用事件用 `shake_arms` / `shy` / `thinking`；抓取、放下事件在脱墙后发生，走原池 |
| 对话兜底 | `thinking` |

`Behavior` 通过 `dock_side_fn` 取得状态并传给三个兜底入口；LLM 不可用或抢锁失败时，贴边不会出现走动台词与走动动作。

## §6 提示词

### §6.1 位置

贴边状态放在 **user 段**，每个任务路径都注入。system 静态前缀不随每轮变化；贴边切换时动作表、`_WINDOW_GUIDE`、任务段的动作数量与 `_FOOD_GUIDE` 一并切换，前缀改变一次；任务段的 few-shot 示例只用两种状态共有的动作，不随贴边切换。`ContextBuilder` 增加 `dock_side_fn`，由 `PetAgent` 注入当前 `_dock_side`；贴边文案由 `prompts.dock_note()` 生成，`ContextBuilder` 只决定是否注入。

| 任务 | 注入点 |
|---|---|
| `autonomous` | `autonomous_vision_user_prompt` / `autonomous_non_vision_user_prompt` 的 `dock_note` 参数，排在 `context` 与「自主决策触发」之前；两个模板的移动分支改成“有窗口且有移动动作可用” |
| `chat` | `chat_vision_user_prompt` / `chat_non_vision_user_prompt` 的 `dock_note` 参数，排在 `=== 用户对你说 ===` 之前 |
| `analyze` | `analyze_vision_user_prompt` / `analyze_non_vision_user_prompt` 的 `dock_note` 参数，排在 `=== 用户交给你看的东西 ===` 之前 |
| `interact` | `build_interact` 把 `dock_note` 拼在 `event_hint` 之前 |

未贴边时 `dock_note` 为空串；除 §6.4 的独立清理外，模板输出与现在一致。参数默认值为空串，现有调用与测试不需要改签名。

### §6.2 贴边段正文

```
[贴边状态]
你现在贴在屏幕{左|右}边缘，你自己下不来。
- 用户要求的事如果需要移动或离开边缘，直接说明做不到，不要假装做了。
```

贴边段只有三要素：侧别与「你自己下不来」、做不到时直接说明。system 侧已经表达的信息不再复述：动作表已经收窄、`food` 工具已经禁用、`[窗口探测]` 数据仍留在 user 段供感知，贴边段都不提。

按任务追加一句：

| 任务 | 追加 |
|---|---|
| `autonomous` | 用户正在专心工作或学习，尽量少打扰：想说话就短说一句，不要为了引起注意反复说话；不要主动提议玩游戏，也不要主动调用会弹窗、发通知、播放媒体或打开网页的工具。 |
| `chat` | 无 |
| `analyze` | 无 |
| `interact` | 无 |

侧别由 `dock_side_fn()` 填入；`{左|右}` 是文档占位，实际文本只有一个字。

贴边状态变化还会往「最近发生了什么」注入一条事件，做法与摸头、抓放一致：`kind` 即事件类型，
文案全部写在事件表 `ContextBuilder._EVENT_LABELS` 里。

| 变化 | kind | 事件文案 |
|---|---|---|
| 贴边 | `dock` | `用户把你放到了屏幕侧边，你下不来了` |
| 离墙 | `undock` | `用户把你从屏幕侧边放下来了` |

autonomous 的 user 模板把移动相关的分支合并成「有窗口且有移动动作可用」与「无窗口且有移动动作可用」；有没有移动动作由动作表里的 `[移动]` 类型决定。这条判断写在模板里，贴边与未贴边共用，贴边段不再单独声明某个步骤不适用。

### §6.3 系统提示词

`build_system_prompt(mode, task, docked=...)` 在贴边时做三处替换：

| 位置 | 贴边时 |
|---|---|
| 动作表 | `_edge_action_section`，只含 `EDGE_ACTION_NAMES` 的八个动作；每行带 `[移动]` / `[驻留]` / `[显隐]` 类型，八个贴边动作全是 `[驻留]` |
| `_WINDOW_GUIDE` | 不注入，避免“走到窗口、跳上顶部”的引导 |
| 四个任务段 | 动作数只要求 `≥1`，不设总时长；`_FOOD_GUIDE` 随 `food` 工具可用性出现或消失 |

- `_SELF_LIFE_GUIDE` / `_MOOD_GUIDE` / `_VITALS_GUIDE` / `_CHAT_INTRO` 写成通用描述，贴边与未贴边共用，不再维护近似副本。`_CHAT_INTRO` 去掉方向/距离一条，避免贴边时与状态段冲突；未贴边由 `_WINDOW_GUIDE` 承载方向/距离引导。
- 组合白名单不变；`_TASK_SECTIONS` 的构建函数增加 `docked` 参数（默认 `False`），现有无参调用保持原输出。贴边时四个任务段只要求 `Action ≥ 1`，不设总时长；`_autonomous_task` 贴边时不引用 `min_action_count()` 与 `target_sequence_duration()`。
- 四个任务段的 few-shot 示例只用贴边与未贴边共有的动作（`thinking` / `sleep` / `shake_arms`），不随贴边切换；动作表列出各自可用动作，示例不再承担示范移动动作的职责。
- 四个任务段的动作数量集中在 `_ACTION_LIMITS`：`autonomous` 未贴边读 `min_action_count()`，`chat` / `analyze` 为 ≥3，`interact` 为 1-2；贴边统一取 `_DOCKED_ACTION_LIMIT`（≥1）。任务段只引用 `_action_limit()` 的返回值。
- 需求念头同步收窄：贴边时 `lonely` 不再说 `凑到你身边`，`drowsy` 只提 `sleep`，不再建议 `sit`；其余需求保留。
- 未贴边时 `_FOOD_GUIDE` 的注入条件是 `config.FOOD_ENABLED and food_tool_available()`；贴边任务段不注入该段，避免模型看到“生成食物再走过去”的引导。
- 饥饿需求的做法改为只保留乞食：`ContextBuilder._need_hint("hungry")` 在 `food_tool_available()` 为假时返回乞食文案（`只能跟你讨点吃的`），不再提自己生成食物。
- 系统前缀只在贴边切换、`food` 工具可用性变化时改变一次，随后保持稳定；切换贴边后第一次请求会 miss 一次 prompt 缓存。

### §6.4 顺带清理：user prompt 的时长赘述

`autonomous_*` 与 `chat_*` 四个 user prompt 各有一句“按输出格式要求凑满时长”。时长约束的权威位置是任务段：`_autonomous_task` 表达总时长，`_chat_task` 不设总时长。这句在 user 段重复了动作规划要求，属于历史遗留的冗余，直接删除，与贴边无关，可独立实施。

删除后：未贴边 autonomous 仍按系统任务段的总时长执行；chat 不再有时长约束；贴边任务段按 §6.3 只要求至少 1 个动作。

同一处把 `analyze_*_user_prompt` 格式提示里的 `Action(≥3个)` 改成 `Action`，动作数只由任务段表达；贴边任务段为 ≥1，未贴边任务段保持 ≥3。

## §7 调度、工具与交互

| 项 | 贴边时 |
|---|---|
| `mid` 自主决策 | 正常触发，不暂停；动作与台词由 §6 的贴边段约束 |
| 看门狗、状态机 | 不变 |
| LLM 工具 | 保持可用；自主轮由贴边段约束，不主动弹窗/发通知/播放媒体/打开网页 |
| `food` 工具 | 禁用；解除贴边时恢复此前的启用状态 |
| 贴边状态变化（贴边 / 离墙） | 中断在途 LLM 调用（`interrupt_current_call(reason="dock state change")`），并清空动作队列 |
| 游戏 | 保持可用；用户主动发起时可玩，自主轮不主动提议 |
| 气泡 | 全部保留，显示规则不变 |
| 粒子 | 全部保留 |
| 摸头 | 保留点击 Q 弹、爱心与数值变化 |
| 文件拖入 | 保留 |
| `unconsciousness` | 不播放 |
| 贴边状态 | 不持久化，重启后为未贴边 |

`PetAgent` 在 `dock_changed` 回调中：

1. 更新 `_dock_side`。
2. 贴边时若 `food` 工具已注册且启用，记录原状态并调用 `TOOL_REGISTRY.set_enabled("food", False)`。
3. 解除贴边时按记录恢复。

`food` 工具被禁用后从 `to_openai_tools()` 的 schema 中消失，`get_method()` 也取不到；在途请求若仍调用 `food__spawn`，按现有工具失败路径返回错误。贴边段不再复述工具状态：模型看不到 `food__spawn`，饥饿需求也切到乞食文案（见 §6.2 与「饿的说明」）。

## §8 配置项

| 键 | 类型 | 默认值 | 设置界面 | 说明 |
|---|---|---|---|---|
| `EDGE_DOCK_ENABLED` | bool | true | 行为页复选框 | 贴边吸附总开关；关闭时甩飞撞边恢复反弹，拖拽松手不吸附 |
| `EDGE_DOCK_SNAP_PX` | int | 24 | 无（`hidden: true`） | 拖拽松手判定吸附的距离阈值（逻辑像素） |

两项都写入 `pet/config.py` 的 `_KEY_META`（category `behavior`，`needs_restart: false`），重新生成 `docs/reference/config.md`。

## §9 文件改动

| 文件 | 职责 |
|---|---|
| `pet/ui/edge_dock.py` | 纯逻辑：`EDGE_VISIBLE_RATIO`、`pick_dock_side`、`dock_x`、`edge_mask_rect`；不依赖 Qt 部件 |
| `pet/action/registry.py` | `EDGE_ACTION_NAMES` 与 `edge_asset(name)` |
| `pet/action/action.py` | `set_dock_side`、`idle_animation` / `grim_animation`、八个动作与 `idle` / `grim` 的素材路由 |
| `pet/action/gravity.py` | `edge_hit(str)` 信号；`_tick_flick` 在 `EDGE_DOCK_ENABLED` 时改为发信号并停止甩飞（落点 `y` 先夹进可用区），关闭时保留反弹 |
| `pet/ui/pet_window.py` | 贴边状态与信号、`dock_to` / `undock`、掩膜、镜像、拖拽释放分支、右键菜单、入队过滤 |
| `pet/agent/pet_agent.py` | 连接 `dock_changed`、维护 `_dock_side`、切换 `food` 工具、贴边变化时中断在途调用并注入 `kind="dock"` 事件、把 `dock_side_fn` 传给 `Behavior` |
| `pet/brain/behavior.py` | 透传 `dock_side_fn`；三个本地兜底入口带贴边标记 |
| `pet/brain/context_builder.py` | 调用 `prompts.dock_note()` 注入四条任务路径的 user 消息；`_EVENT_LABELS` 增加 `dock` / `undock` 两条贴边事件文案；饥饿/孤独需求与短暂小睡建议按贴边状态收窄 |
| `pet/brain/prompts.py` | `dock_note()` / `_with_dock_note()` / `_task_format()` / `_action_limit()`；六个 user prompt 构建函数增加 `dock_note` 参数；autonomous 两个模板的移动分支加“有移动动作可用”条件；`build_system_prompt` 按 `docked` 切换动作表、窗口引导与任务段动作数量；任务示例改用共有动作；`_ACTION_LIMITS` / `_DOCKED_ACTION_LIMIT` 统一动作数量；`_SELF_LIFE_GUIDE` / `_MOOD_GUIDE` / `_VITALS_GUIDE` / `_CHAT_INTRO` 改为通用描述；`_FOOD_GUIDE` 注入条件 |
| `pet/brain/local_fallback.py` | 贴边动作池 |
| `pet/tools/registry.py` | `food_tool_available()` |
| `pet/config.py` | 两个配置键 |
| `pet/ui/settings_window.py` | 行为页 `EDGE_DOCK_ENABLED` 复选框 |
| `pet/ui/__init__.py` | 模块说明补充 `edge_dock` |
| `tests/test_edge_dock.py` | 吸附判定、掩膜矩形、镜像绘制、动作路由、甩飞边界分支 |
| `tests/test_prompts.py` | 四条任务路径的贴边段、未贴边时不出现、贴边动作表与任务段动作数量、示例只用共有动作、自我生活/心情/生理段通用、需求念头收窄、`_FOOD_GUIDE` 条件 |
| `tests/test_architecture_contracts.py` | 登记 `_edge_action_section` 的惰性缓存为既有 ARCH009 债务 |
| `docs/reference/*` | `python scripts/gen_docs.py` 重新生成 |

§6.4 的 user prompt 时长赘述删除也在 `pet/brain/prompts.py`，与贴边无关，不计入上表。

## §10 验证方式

自动化：

```bash
python -m pytest tests/test_edge_dock.py tests/test_prompts.py tests/test_architecture_contracts.py
python scripts/gen_docs.py --check
```

`tests/test_edge_dock.py` 覆盖：

1. `pick_dock_side`：左侧阈值内、右侧阈值内、两侧都不在、恰好等于阈值、窗口宽度大于屏幕可用宽度时的返回值；
2. `dock_x`：左右两侧贴边坐标；`edge_mask_rect`：左右掩膜覆盖可见侧、宽度为 `EDGE_VISIBLE_RATIO`；
3. `EDGE_ACTION_NAMES` 与 `assets/actions/*_edge/` 的目录集合一致；`edge_asset` 返回 `*_edge`；
4. `_SpriteLabel.set_flip(True)` 渲染后，非透明像素重心从右侧移到左侧；
5. `PetActions.set_dock_side("right")` 后 `sleep` 播放 `sleep_edge`、`idle` 播放 `idle_edge`，解除后回到原名；
6. 甩飞边界分支：`EDGE_DOCK_ENABLED` 为真时发出 `edge_hit("left"/"right")` 并停止甩飞（落点 `y` 夹进可用区上/下界），为假时保持反弹；
7. 入队过滤：贴边时 `walk` / `sit` / `fade_out` 被丢弃，`sleep` / `shy` 保留。

提示词用例覆盖：

1. 四条任务路径在 `dock_side_fn` 返回 `"left"` / `"right"` 时 user 段包含贴边段与对应侧别；贴边段只有三要素（侧别 / 用户放上去下不来 / 做不到直说），且仅 `autonomous` 追加一句“少打扰”；
2. 未贴边时 user 段不包含贴边段；除 §6.4 的时长措辞清理外，输出与现有快照一致；
3. 贴边时 system 动作表只含 `EDGE_ACTION_NAMES` 的八个动作，不含 `walk` / `drive` / `sit` / `look_around`；未贴边时含完整动作表与 `walk`；
4. 贴边时 `_WINDOW_GUIDE` 不出现，聊天段不含方向/距离一条；四个任务段的示例在贴边与未贴边都只用共有动作名，不含 `walk` / `drive` / `sit` / `look_around` / `stretch`；
5. 贴边时四个任务段只要求 `Action ≥ 1`，不出现目标总时长与“凑满时长”约束；未贴边时保持现有动作数下限，autonomous 另保留系统段的总时长要求；
6. 贴边 system 不出现 `靠墙` / `钓鱼` / `巡视` / `换地方` / `可玩窗口` / `跳跃`；通用自我生活、心情、生理段在贴边与未贴边共用；`lonely` 不再说 `凑到你身边`，`drowsy` 不再建议 `sit`；
7. 贴边段不出现 `system` 与“后文步骤”；autonomous 的两个 user 模板都带“有移动动作可用”分支；动作表每行带类型标记，贴边表不含 `[移动]` 与 `[显隐]`；
8. 贴边注入 `kind="dock"` 事件、离墙注入 `kind="undock"` 事件，文案取自 `_EVENT_LABELS`；
9. `food` 工具禁用时 `_FOOD_GUIDE` 不出现，饥饿需求只保留乞食文案；恢复后两者回到原样。

顺带清理（与贴边无关）：四个 user prompt 在贴边与未贴边都不含“凑满时长”；未贴边 autonomous 仍按系统任务段的总时长执行，chat 不再有时长约束。

手动检查清单：

1. 拖到左/右边缘 24px 内松手，桌宠贴边并换成对应姿势；左侧是镜像后的姿势，主体贴左缘；
2. 快速甩飞撞到左右边缘时直接吸附，不反弹；关闭 `EDGE_DOCK_ENABLED` 后恢复反弹；
3. 贴边后点击内侧透明条，点击落到下层窗口；点击可见主体仍能触发摸头与右键菜单；
4. 贴边后拖拽立即脱墙，松手靠近边缘重新吸附；点菜单“离开边缘”后回落；
5. 贴边时 `mid` 仍按配置触发，桌宠会说话与换姿势；它不说自己要走过去、不主动开游戏；
6. 贴边时聊天要求“过来/跳上窗口”，回复说明贴着边做不到；
7. 贴边时 `food` 工具不出现在 tools 列表，饥饿时只讨食；投喂气泡仍可用；
8. 粒子（zzz、星星、爱心）在贴边时照常出现；
9. 重启后回到屏幕中央，不恢复贴边；
10. 贴边期间关闭再打开 `EDGE_DOCK_ENABLED`，已在墙上的状态不被强制解除；
11. 点右键菜单“贴到屏幕边缘”：桌宠先淡出，贴上边缘后淡入，落在屏幕垂直中点；

## §11 风险与约束

- **掩膜比例**：`0.70` 覆盖现有最宽贴边帧（`shake_arms_edge` 第 1 / 3 帧）的 64.3%；新增更靠内的贴边素材会露出被裁的一侧，需要同步调整比例或素材边距。
- **边缘占位**：主体宽度约 80px，贴边后会覆盖边缘附近的应用控件（滚动条、关闭按钮）。掩膜只去掉内侧透明条的死区，不缩小主体本身的占位；用户可换边、调整 y 或解除贴边。
- **提示词约束是软约束**：模型仍可能输出不可做的动作或说“这就过去”。入队过滤与本地兜底保证动作不执行，台词不做二次改写；贴边段把“做不到”写成显式要求，降低不一致概率。贴边状态变化会直接中断在途调用，所以旧状态下拼的请求基本不会把结果带到贴边之后。
- **静态前缀变化**：贴边切换时动作表、`_WINDOW_GUIDE`、任务段动作数量与 `_FOOD_GUIDE` 一起变化，第一次请求 miss 一次缓存；通用提示段与 few-shot 示例不随贴边切换，其余 system 内容不变。
- **工具切换竞态**：贴边会中断在途调用，只有“中断请求发出前那一次工具调用”可能仍带 `food`，走工具失败路径；贴边段不复述工具状态，靠 schema 里没有该工具与饥饿需求切乞食来兜住。
- **中断的代价**：贴边 / 离墙时若有在途调用会被取消，已流式播出的半句台词会保留，状态强制回 IDLE；下一轮按新状态重新决策。
- **掩膜与平台**：`setMask` 走 Qt 通用接口，实测覆盖 Windows；macOS / Linux 未验证。
- **无意识化**：贴边待机不进入 `unconsciousness`，长时间停留时只有 `idle_edge` 的眨眼与呼吸。
- **单屏假设**：吸附使用窗口所在屏的可用区左/右边界；多屏相邻边界会被当作可吸附边缘，本设计不做区分。

## §12 结论

贴边由三个本地部分构成：窗口状态与掩膜、动作素材路由、user 段状态说明。system 动作表在贴边时同步收窄到八个贴边动作，任务段只要求至少 1 个动作且不限时长，工具 schema 只少了 `food`；调度器、粒子、气泡与交互全部保留。贴边只把可做动作收窄到八个贴边姿势，并通过禁用 `food` 工具把自主觅食改成乞食。关闭 `EDGE_DOCK_ENABLED` 时，甩飞撞边回到原有反弹行为。
