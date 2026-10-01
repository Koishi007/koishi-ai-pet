<!-- 由 scripts/gen_docs.py 生成，请勿手工编辑 -->

# 动作参考

动作定义在 `pet/action/registry.py`，是 LLM 可输出动作的唯一真源；
`pet/brain/parsing.py` 用它校验 LLM 输出里的动作名。本文件由注册表机械展开。

## 时长与数量（随调度间隔变化）

下表按**默认配置**计算；用户改过 `settings.json` 里的调度间隔后，提示词中的实际范围会随之变化。

- 动作序列目标总时长：`270s`（= `SCHEDULER_MID_MS` × `_SEQUENCE_RATIO`）
- 单轮最少动作数：`11`（受 `LLM_ACTION_MIN_DIVISOR` 影响）

带时长的动作只能在此范围内取值：

| 动作 | 最小秒数 | 占目标时长比例 |
|---|---|---|
| `sit` | 10 | 0.4 |
| `thinking` | 5 | 0.2 |
| `sleep` | 10 | 0.4 |
| `calling` | 5 | 0.2 |
| `finger_heart` | 1 | 0.01 |
| `bathing` | 20 | 0.5 |

## 移动

| 动作 | 参数 | 说明 | 示例 |
|---|---|---|---|
| `drive` | direction=left/right distance=500-1000 | 骑小电驴 | `Action: drive right 800` |
| `walk` | direction=left/right distance=500-1000 | 行走 | `Action: walk right 800` |
| `bounce` | direction=left/right distance=0-800 height>0 | 跳跃 | `Action: bounce right 400 200` |

## 驻留

| 动作 | 参数 | 说明 | 示例 |
|---|---|---|---|
| `shake_arms` | 无参数 | 开心摇晃手臂 | `Action: shake_arms` |
| `look_around` | 无参数 | 张望环顾四周 | `Action: look_around` |
| `stretch` | 无参数 | 睡醒起身后伸懒腰 | `Action: stretch` |
| `fishing` | 无参数 | 钓鱼，说不定能钓到鱼 | `Action: fishing` |
| `rotate` | 无参数 | 开心地原地转圈 | `Action: rotate` |
| `confuse` | 无参数 | 看到或听到奇怪事情的疑惑 | `Action: confuse` |
| `embarrassed` | 无参数 | 做错事后的尴尬 | `Action: embarrassed` |
| `shy` | 无参数 | 双手捧脸表达害羞 | `Action: shy` |
| `dejected` | 无参数 | 失落，情绪低落地蹲着 | `Action: dejected` |
| `sit` | 10-216秒 | 坐下 | `Action: sit 108` |
| `thinking` | 5-108秒 | 沉思 | `Action: thinking 54` |
| `sleep` | 10-216秒 | 睡觉 | `Action: sleep 108` |
| `calling` | 5-108秒 | 打电话消磨时间 | `Action: calling 54` |
| `finger_heart` | 1-6秒 | 表达喜欢的比心 | `Action: finger_heart 2` |
| `bathing` | 20-270秒 | 放松地泡澡 | `Action: bathing 135` |

## 显隐

| 动作 | 参数 | 说明 | 示例 |
|---|---|---|---|
| `fade_in` | 无参数 | 显示自己的身形 | `Action: fade_in` |
| `fade_out` | 无参数 | 突然消失不见 | `Action: fade_out` |

## 注入给 LLM 的样子

`generate_action_section()` 会把上表渲染成提示词里的 `[可用动作]` 段，由 `pet/brain/prompts.py` 的 `_PERCEPTION_SECTIONS` 按模式（视觉/非视觉）拼进 system prompt。
这段文本被 `_Lazy` 缓存（首次求值后不再重算，保 system 前缀稳定）；改完 `settings.json` 里与调度相关的项后，设置界面会调用 `invalidate_action_section()` 让它按新配置重算。

```text
[可用动作] 共 20 个
- drive [direction=left/right distance=500-1000] 骑小电驴 | 示例: drive right 800
- walk [direction=left/right distance=500-1000] 行走 | 示例: walk right 800
- bounce [direction=left/right distance=0-800 height>0] 跳跃 | 示例: bounce right 400 200
- shake_arms 无参数 开心摇晃手臂
- look_around 无参数 张望环顾四周
- stretch 无参数 睡醒起身后伸懒腰
- fishing 无参数 钓鱼，说不定能钓到鱼
- rotate 无参数 开心地原地转圈
- confuse 无参数 看到或听到奇怪事情的疑惑
- embarrassed 无参数 做错事后的尴尬
- shy 无参数 双手捧脸表达害羞
- dejected 无参数 失落，情绪低落地蹲着
- sit [10-216秒] 坐下 | 示例: sit 108
- thinking [5-108秒] 沉思 | 示例: thinking 54
- sleep [10-216秒] 睡觉 | 示例: sleep 108
- calling [5-108秒] 打电话消磨时间 | 示例: calling 54
- finger_heart [1-6秒] 表达喜欢的比心 | 示例: finger_heart 2
- bathing [20-270秒] 放松地泡澡 | 示例: bathing 135
- fade_in 无参数 显示自己的身形
- fade_out 无参数 突然消失不见
```
