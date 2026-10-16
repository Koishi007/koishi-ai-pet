# 拖拽惯性钟摆设计

> 拖拽移动桌宠时，宠物贴图绕抓取点做惯性摆动：窗口加速时身体相对抓取点滞后偏转，减速时前甩，松手后残余摆动自由衰减回正。摆动是绘制期变换，不移动窗口，不影响重力判定与释放速度计算。

## §0 背景与现状

拖拽链路的现状（`pet/ui/pet_window.py`）：

| 环节 | 现状 | 位置 |
|---|---|---|
| 抓取点 | 窗口内水平居中、纵向 16%（头部） | `pet/ui/pet_window.py:153-155`、`:330-335` |
| 拖拽移动 | `mouseMoveEvent` 中 `move(new_pos)`，窗口整体跟随鼠标 | `pet/ui/pet_window.py:357-370` |
| 释放速度 | `_drag_history` 记录最近 10 个 (坐标, 时间戳)，释放时取 150ms 内差分 | `pet/ui/pet_window.py:384-400` |
| 重力联动 | 拖拽期间 `gravity.enable(False)` 停掉重力定时器，释放时恢复并按速度 `apply_impulse()` | `pet/ui/pet_window.py:336`、`pet/action/gravity.py:55-72` |
| 贴图绘制 | `_SpriteLabel.paintEvent` 自绘：呼吸姿态（脚底锚点缩放）与点击 Q 弹（`tap_scale`）相乘 | `pet/ui/pet_window.py:73-149` |

渲染侧的既有约束：贴图 1:1 撑满窗口（`PET_WIDTH` × `PET_HEIGHT` = 125 × 125，`pet/ui/pet_animations.py:289-305` 的 breath 位移上限 4px 即源于此），任何超出 label 矩形的绘制都会被窗口裁剪；呼吸姿态经 `pose_changed` 信号注入（`pet/ui/pet_animations.py:42`），帧动画经 `frame_changed` → `setPixmap` 注入，两者与绘制期变换正交。

## §1 目标

- 拖拽中窗口加速度驱动身体绕抓取点偏转，方向与加速度相反（惯性滞后）。
- 松手后摆动自由衰减回正，与 `apply_impulse` 的抛出动画叠加；落地时残余角度快速归正，不拖到落地之后，摆动不参与位置计算。
- 摆动是 `_SpriteLabel` 绘制期变换，窗口矩形不变，重力判定（feet、`effective_bottom`）不受影响。
- 与呼吸姿态、点击 Q 弹共存：三者变换串联，互不覆盖。
- 摆动静止后回到零角度，恢复 `_SpriteLabel` 的快速渲染路径。
- 功能与幅度可经配置关闭、调整。

## §2 非目标

- 不做落地冲击摆动：落地表现已由 `fall_down` 动画与 `dust` 粒子承担。
- 不做垂直方向的伸缩摆动（加速度上下甩动时的压扁拉伸），只做单角度自由度。
- 不改窗口尺寸或留白：`GravitySystem` 的 feet 计算依赖窗口高度（`pet/action/gravity.py:161-163`），扩窗会让落地悬空。
- 不复用 `_drag_history`：它服务于释放速度计算，采样由鼠标事件驱动、间隔不均；摆动激励在自身 30ms tick 内做位置差分。

## §3 摆动模型

### §3.1 状态与积分

单摆角度 θ（弧度，右偏为正）与角速度 ω，30ms tick 半隐式欧拉积分：

```python
drive = -ax * AX_GAIN - vx * VX_GAIN   # 惯性甩动 + 移动滞后
omega += (-K * sin(theta) - C * omega + drive) * dt
theta += omega * dt
theta, omega = clamp_pendulum(theta, omega, theta_max)
```

| 常量 | 值 | 含义 |
|---|---|---|
| `_INTERVAL_MS` | 30 | tick 周期，与 `GravitySystem` 同频 |
| `_K` | 25.0 | 恢复系数（= g/L），固有周期 2π/√K ≈ 1.26s |
| `_C_DRAG` | 2.0 | 拖拽中阻尼，ζ = C/(2√K) ≈ 0.2 |
| `_C_FREE` | 5.0 | 松手后阻尼，ζ ≈ 0.5，一次过冲后回正 |
| `_AX_GAIN` | 1e-3 | 加速度映射系数（px/s² → rad/s²）：加减速瞬间的惯性甩动 |
| `_VX_GAIN` | 5e-3 | 速度映射系数（px/s → rad/s²）：匀速拖动时身体保持滞后倾角（600px/s 稳态约 7°） |
| `_LANDING_DECAY` | 0.5 | 落地归正系数：每 tick 角度乘以该值（约 0.15s 竖直） |

两个激励分量方向都与运动方向相反（风阻模型）：加速度分量产生启动与急停的甩动，速度分量让匀速移动全程保持滞后倾角，快速甩动由上限截断。`clamp_pendulum` 在 |θ| 超过 `theta_max` 时截断角度并将 ω 置零，防止贴上限抖动。

积分是模块级纯函数 `step_pendulum(theta, omega, drive, dt, k, c, theta_max)`（`drive` 为换算后的激励角加速度），不依赖 Qt，可脱离事件循环单测。

### §3.2 激励估计

`SwingController` 每个 tick 读取窗口 x 坐标（构造时注入取值回调），两级差分求速度与加速度：

```python
v = (x_now - x_prev) / dt            # 速度直接参与激励
a = (v - v_prev) / dt
ax = ax * (1 - ALPHA) + a * ALPHA    # ALPHA = 0.25，加速度低通
```

差分在 tick 内完成，与鼠标事件频率无关。`begin()` 时速度、加速度、平滑值全部清零并以当前位置为基准，拖拽起始瞬间不产生虚假冲击。

### §3.3 生命周期

| 阶段 | 入口 | 行为 |
|---|---|---|
| 抓取 | `_start_drag()` → `begin()` | 记录基准位置，启动定时器；`GRAB_SWING_ENABLED` 关闭时不启动 |
| 拖拽 | tick | 位置差分求激励，按 §3.1 积分，发 `swing_changed(角度度数)` |
| 松手 | `mouseReleaseEvent()` → `release()` | 激励置零，阻尼切换为 `_C_FREE`，θ/ω 保留继续衰减 |
| 落地 | `gravity.landed` → `land()` | 残余角度按 `_LANDING_DECAY` 指数归正，归零后停表；无残余摆角时不启动任何重绘 |
| 静止 | tick 内判定 | 角度小于 0.5° 且角速度小于 5°/s 连续 12 tick（约 0.36s）后停表，θ 归零并发最后一次 `swing_changed(0)` |
| 再抓取 | `begin()` | 从静止状态重新开始 |

松手后窗口由 `GravitySystem` 接管（下落或甩出），摆动不再读取窗口位置：甩出位移已表现运动本身，摆动只表现残余衰减，激励源断开后行为可预期。

## §4 渲染接入

`_SpriteLabel` 增加 `set_swing(angle_deg)`：存值并 `update()`，与 `set_pose` 同构。

`paintEvent` 的变换顺序（呼吸在前，摆动在后）：

```python
# 既有：呼吸缩放，锚点 = 脚底中点
painter.translate(anchor_foot); painter.scale(sx, sy); painter.translate(-anchor_foot)
# 新增：摆动旋转，锚点 = 抓取点（水平居中, 0.16 * pm_h）
if swing != 0:
    painter.translate(anchor_grab); painter.rotate(-swing); painter.translate(-anchor_grab)
painter.drawPixmap(QPointF(left, top), pixmap)
```

| 项 | 约定 |
|---|---|
| 摆动锚点 | `(left + pm_w/2, top + pm_h * GRAB_ANCHOR_Y)`，`GRAB_ANCHOR_Y = 0.16` 定义在 `_SpriteLabel`，`PetWindow._GRAB_RATIO_Y` 引用同一常量 |
| 旋转方向 | Qt `rotate` 正角在屏幕上为顺时针，锚点下方的身体顺时针会向左偏，与 θ 右偏为正相反，绘制取负角 |
| 信号单位 | `swing_changed` 发角度的度数值（右偏为正），控制器内部积分用弧度 |
| 快速渲染路径 | 判定条件扩为 `pose == (0, 1.0, 1.0) and tap_scale == 1.0 and swing == 0`，静止后不产生额外重绘 |
| 与帧动画的关系 | `setPixmap` 触发的重绘与 `set_swing` 触发的重绘走同一 `paintEvent`，`grabbed` 帧动画正常播出 |

## §5 配置项

| 键 | 类型 | 默认值 | 设置界面 | 说明 |
|---|---|---|---|---|
| `GRAB_SWING_ENABLED` | bool | true | 行为页交互区复选框 | 拖拽惯性钟摆开关 |
| `GRAB_SWING_MAX_DEG` | int | 12 | 无（`hidden: true`，改配置文件） | 摆角上限（度） |

配置项写入 `pet/config.py` 的 `_KEY_META`（category `behavior`），重新生成 `docs/reference/config.md`。

## §6 文件改动清单

| 文件 | 改动 |
|---|---|
| `pet/ui/swing.py`（新增） | `step_pendulum` 纯函数与 `SwingController`（QObject：30ms 定时器、激励估计、静止停表、`swing_changed` 信号） |
| `pet/ui/pet_window.py` | `_SpriteLabel` 增加 `set_swing`、`GRAB_ANCHOR_Y` 常量与旋转分支，快速路径条件扩充；`_GRAB_RATIO_Y` 改为引用该常量；`_setup_ui` 创建控制器并连接信号；`_start_drag` 调 `begin()`，`mouseReleaseEvent` 调 `release()` |
| `pet/config.py` | 新增 §5 两个配置键 |
| `pet/ui/settings_window.py` | 行为页交互区加 `GRAB_SWING_ENABLED` 复选框（`_check` 注册进 `_fields`） |
| `pet/ui/__init__.py` | 模块说明补 `swing` |
| `docs/architecture.md` | `pet/ui/` 行的关键文件补 `swing.py` |
| `docs/subsystems/actions-and-animation.md` | 队列与重力联动一节补摆动与拖拽的关系 |
| `docs/specs/2026-10-14-grab-swing-design.md`（新增） | 本文档 |
| `docs/README.md` | 文档地图登记本文档 |
| `tests/test_grab_swing.py`（新增） | 纯函数与控制器单测，见 §7 |

## §7 验证方式

自动化：

```bash
python -m pytest tests/test_grab_swing.py tests/test_docs.py tests/test_architecture_contracts.py
python scripts/gen_docs.py --check
```

`tests/test_grab_swing.py` 直接驱动控制器的 tick 方法（注入位置序列），不依赖事件循环时序；`QT_QPA_PLATFORM=offscreen` 由 `tests/conftest.py:23` 提供。覆盖：

1. `step_pendulum`：零激励下角度衰减回零；负向激励产生负向角度；角度到达上限时截断且 ω 归零；
2. 控制器：`begin` 后注入匀加速位置序列，`swing_changed` 的角度非零且方向与加速度相反；匀速位置序列下角度稳定在滞后方向的非零值；`release` 后角度衰减至零并停表（定时器停止）；再次 `begin` 从零开始；
3. 开关：`GRAB_SWING_ENABLED` 关闭时 `begin` 不启动定时器、不产生信号；
4. 上限：注入高速位移序列，角度绝对值不超过 `GRAB_SWING_MAX_DEG`；
5. 渲染方向：渲染 `swing = 0 / +10 / -10` 三档，下半部分黑色像素重心满足 `right > base > left`（θ 为正时绘制出的身体在垂线右侧）；
6. 落地归正：有残余摆角时 `land()` 后 10 tick 内到达静止并停表，最后信号为 0；无残余摆角时 `land()` 不启动定时器。

手动检查清单：

1. 抓起后匀速横向移动，身体向移动反方向保持滞后倾角；甩动时滞后加大，急停时前甩再回摆；
2. 缓慢移动时倾角按速度比例减小，全程无抖动；
3. 甩动中松手抛出，下落中残余摆动衰减，落地瞬间身体快速归正竖直，落地后不再有回正动画；
4. 静止抓持数秒后 CPU 占用无摆动相关开销（定时器已停）；
5. 拖拽中呼吸与 Q 弹（先点一下再拖）不消失，三者叠加无跳变；
6. 设置页关闭开关后拖拽无摆动，开启后无需重启恢复；
7. 高 DPI 屏（DPR > 1）上摆动锚点位于头部，无偏移。

## §8 风险与约束

- **旋转裁剪**：贴图 1:1 撑满窗口，默认 12° 时脚部横向偏移约 `0.84 × 125 × sin(12°) ≈ 22px`；主体两侧透明边不足的素材会出现肢体边缘被裁。调小 `GRAB_SWING_MAX_DEG` 或调整素材透明边。
- **参数手感**：`_AX_GAIN`、`_VX_GAIN`、`_K`、阻尼取值为校准起点，实机手感偏硬或偏软时直接调常量，不新增配置键。
- **变换叠加**：呼吸 scale 应用于摆动锚点之前，锚点受呼吸缩放影响产生的偏差在亚像素级（呼吸缩放幅度 ≤ 4%），不构成视觉问题。
- **线程与定时器**：控制器只活在 GUI 主线程；拖拽期间 `GravitySystem` 的定时器已停（`gravity.enable(False)`），两个 30ms 定时器不同时运转。
- **平台覆盖**：绘制与定时器均为 Qt 通用接口，实测只覆盖 Windows。

## §9 结论

改动集中在三处：一个不依赖窗口的摆动控制器（纯函数积分可单测）、`_SpriteLabel` 的一个绘制分支、`PetWindow` 的两行挂钩。窗口矩形、重力判定、释放速度计算、动作队列均不变；配置一个开关加一个幅度上限，文档按生成物规则同步。
