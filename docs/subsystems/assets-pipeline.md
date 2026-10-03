# 素材规格

桌宠的每一帧动画都是 `assets/actions/<动作名>/` 下的透明 webp，配一个同名 json。
本文是**素材契约的唯一入口**：规格、目录命名、配置字段、入库检查清单都写在这里；
素材在运行期怎么被加载消费（容错、缓存、队列副作用）见 [actions-and-animation.md](actions-and-animation.md) §3。

白底原图转成透明 webp 的过程不限定工具 - 去背、缩放、压码都可以自选。
仓库只收最终产物，不收原图。

## 1. 规格

| 项 | 要求 |
|---|---|
| 格式 | webp（代码也认 png / jpg / jpeg / bmp，仓库统一用 webp） |
| 尺寸 | 512×512（代码不强校验，但现有 48 帧都是这个尺寸，不宜偏离太多） |
| 背景 | 透明；主体完整不贴边，四周留相近边距 |
| 体积 | 现有单帧约 35~52 KB，可作参考上限 |

原图建议用 ≥1024×1024 的白底图，去背后的透明图再缩到 512×512，主体不至于糊。
去背工具只管去背景、不改构图，所以主体贴边、水印压在主体上这类问题需在入库前处理
（水印落在白底上的会随背景一起去掉）。

## 2. 目录、命名与配置契约

- 一个动作 = 一个目录：`assets/actions/<动作名>/`；
- 帧文件按**文件名字典序**即播放顺序，惯例是 `1.webp`、`2.webp`…；帧数 ≥10 时需零填充
  （否则 `10.webp` 会排在 `2.webp` 前面）；
- 必须有配置 `<动作名>.json`，**文件名与目录同名**，否则整个动作不加载；
- 动作名与素材目录名**不一定相同**：`walk` 用 `walk_left` / `walk_right`，`drive` 用 `driving_left` / `driving_right`，
  `fade_in` / `fade_out` 完全没有素材。

```json
{
  "desc": "坐下动画",
  "tick_counts": 60,
  "frame_ratios": [0.9, 0.1],
  "loop": true,
  "breath": {"amplitude": 2, "scale_x": 1.0, "scale_y": 1.02, "period_ticks": 60},
  "note": "自由备注"
}
```

| 字段 | 要求 | 说明 |
|---|---|---|
| `desc` | 字符串 | 人读描述，代码不读 |
| `tick_counts` | 正整数（默认 30） | 一个循环的总 tick 数；一个 tick = `round(1000 / PET_FPS)` ms（默认 15 FPS → 67ms） |
| `frame_ratios` | 与帧数等长的正数数组，和 = 1.0 | 每帧占比，按文件名序对应；缺省为等分 |
| `loop` | 布尔（默认 `true`） | 是否循环；动作的 `duration` 参数**只对循环动画生效** |
| `breath` | 可选对象 | `amplitude` 0~4px、`scale_x` / `scale_y` 0.5~2.0、`period_ticks` 0~900 |
| `note` | 字符串 | 自由备注，代码不读 |

上表是**契约**：写成别的样子不会报错，而会被运行期容错悄悄改掉（等分、归一化、夹紧、跳过），
具体后果见 [actions-and-animation.md](actions-and-animation.md) §3。

当前素材规模（抽样）：26 个动作目录、26 个 json、58 个 webp 帧（全部 512×512）；
多数动作 1~2 帧，`stretch` 3 帧、`fall_down` 4 帧、`rotate` 5 帧、`unconsciousness` 6 帧、`fishing` / `shake_arms` 8 帧。
唯一的命名例外是 `sleep/`（单帧叫 `sleep.webp`）。

## 3. 入库检查清单

1. **素材是否符合动作语义**：新动作需先确定它属于移动 / 驻留 / 显隐（决定实现方式），
   以及是否需要时长参数（需要则进 `_DURATION_ACTION_DEFS`）。
2. **规格与配置**：对照 §1 的规格与 §2 的字段契约逐项核对（尺寸、透明边距、帧序、字段取值）。
   写偏了不会报错，只会被运行期容错悄悄改成等分 / 归一化 / 夹紧。
3. **构图**：素材在窗口里按 `PET_WIDTH × PET_HEIGHT`（默认 125×125）等比拉伸绘制，
   原图留白过多会让角色显小；建议参照现有素材，主体四周留出相近边距。
4. **注册动作**：在 `pet/action/registry.py` 注册，否则模型输出的动作名会被丢弃；
   再按需挂特效、消耗、产出，遗漏后果见 [actions-and-animation.md](actions-and-animation.md) §5。
5. **测试与文档**：运行 `python -m pytest` 与 `python scripts/gen_docs.py`；
   粒子/动作相关的用例在 `tests/test_particles.py`、`tests/test_action_*.py`。

## 4. 常见问题

| 现象 | 原因与处理 |
|---|---|
| 新动作不播 | 缺 json / 目录里没有图 / 没在 registry 注册，三种原因各自的后果见 [actions-and-animation.md](actions-and-animation.md) §5 |
| 改了素材没生效 | 帧动画配置有缓存且无失效入口，**重启**应用（缓存行为见 [actions-and-animation.md](actions-and-animation.md) §3） |
| 角色被裁掉一角 | 原图主体贴边，需重新生成或先补边距 |
| 成品里残留水印 | 水印压在主体上会保留，落在白底上的才随背景去掉，必要时手动修图 |
| 原图是否提交 | 不提交：仓库只收压缩后的 webp 与 json（`.gitignore` 也不跟踪原图目录） |

## 5. 不变量

- **帧序与配置同名是硬要求**：改名等于改播放顺序；json 文件名与目录不一致时整个动作不加载（见 §2）。
- **webp 是仓库唯一的正式格式**（png/jpg/bmp 代码也认，但仓库约定只用 webp）。
- 素材与代码是**成对**的：注册了动作却没有素材 → 该动作永远播不出来；反之素材不会被自动发现。
