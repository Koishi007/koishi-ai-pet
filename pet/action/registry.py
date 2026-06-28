"""可被 LLM 调用的动作定义。"""

import math
from dataclasses import dataclass, field
from typing import List

from pet.config import config


# ── 耗时类动作的动态时长计算 ──
# 每次调用时读取 config.SCHEDULER_MID_MS，确保 settings.json 修改后立即生效

# 动作时长参数定义：(名称, 最小秒数, 占调度间隔比例)
_DURATION_ACTION_DEFS = {
    "sit":          (10, 0.4),
    "thinking":     ( 5, 0.2),
    "sleep":        (10, 0.4),
    "calling":      ( 5, 0.2),
    "finger_heart": ( 1, 0.01),
}

# 动作序列总时长占调度间隔的比例
_SEQUENCE_RATIO = 0.9


def target_sequence_duration() -> int:
    """动作序列的目标总时长（秒），基于调度间隔动态计算。"""
    mid_s = config.SCHEDULER_MID_MS / 1000
    return int(mid_s * _SEQUENCE_RATIO)


def min_action_count() -> int:
    """动作序列的最少动作数，基于目标总时长动态计算。

    除数由 config.LLM_ACTION_MIN_DIVISOR 控制（默认 25）：
    数值越大，单个动作平均时长越长，动作数下限越低。
    """
    return max(4, math.ceil(target_sequence_duration() / config.LLM_ACTION_MIN_DIVISOR))


def default_duration(action: str) -> int:
    """返回某个动作的兜底时长（秒），根据调度间隔动态计算。"""
    if action not in _DURATION_ACTION_DEFS:
        raise KeyError(f"Unknown duration action: {action}")
    floor, ratio = _DURATION_ACTION_DEFS[action]
    return max(floor, int(target_sequence_duration() * ratio))


def duration_range(action: str) -> tuple[int, int]:
    """返回某个动作的时长范围（最小秒, 最大秒），基于调度间隔动态计算。"""
    if action not in _DURATION_ACTION_DEFS:
        raise KeyError(f"Unknown duration action: {action}")
    floor, _ = _DURATION_ACTION_DEFS[action]
    lo = floor
    hi = max(floor + 5, default_duration(action) * 2)
    return lo, hi


@dataclass
class ActionDef:
    name: str
    category: str
    description: str
    params: List[str] = field(default_factory=list)
    usage_example: str = ""


def _build_duration_registry() -> dict[str, ActionDef]:
    """动态构建 REGISTRY，duration 范围和示例根据当前调度间隔生成。

    generate_action_section() 调用时也会重新读取 config，
    所以 settings.json 修改后 prompt 中的范围也会即时更新。
    """
    result = {
        "drive": ActionDef(
            name="drive",
            category="移动",
            description="骑小电驴",
            params=["direction=left/right", "distance=500-1000"],
            usage_example="Action: drive right 800",
        ),
        "walk": ActionDef(
            name="walk",
            category="移动",
            description="行走",
            params=["direction=left/right", "distance=500-1000"],
            usage_example="Action: walk right 800",
        ),
        "bounce": ActionDef(
            name="bounce",
            category="移动",
            description="跳跃",
            params=["direction=left/right", "distance=0-800", "height>0"],
            usage_example="Action: bounce right 400 200",
        ),
        "shake_arms": ActionDef(
            name="shake_arms",
            category="驻留",
            description="开心摇晃手臂",
            params=[],
            usage_example="Action: shake_arms",
        ),
        "look_around": ActionDef(
            name="look_around",
            category="驻留",
            description="张望环顾四周",
            params=[],
            usage_example="Action: look_around",
        ),
        "stretch": ActionDef(
            name="stretch",
            category="驻留",
            description="睡醒起身后伸懒腰",
            params=[],
            usage_example="Action: stretch",
        ),
        "fishing": ActionDef(
            name="fishing",
            category="驻留",
            description="钓鱼，不能真的钓到鱼",
            params=[],
            usage_example="Action: fishing",
        ),
        "rotate": ActionDef(
            name="rotate",
            category="驻留",
            description="开心地原地转圈",
            params=[],
            usage_example="Action: rotate",
        ),
    }

    # 动态生成带 duration 参数的动作
    for name in _DURATION_ACTION_DEFS:
        lo, hi = duration_range(name)
        dur = default_duration(name)
        desc_map = {
            "sit": "坐下",
            "sleep": "睡觉",
            "thinking": "沉思",
            "calling": "打电话消磨时间",
            "finger_heart": "表达喜欢的比心",
        }
        result[name] = ActionDef(
            name=name,
            category="驻留",
            description=desc_map[name],
            params=[f"{lo}-{hi}秒"],
            usage_example=f"Action: {name} {dur}",
        )

    # 显隐动作放最后，保持 移动→驻留→显隐 的展示顺序
    result["fade_in"] = ActionDef(
        name="fade_in",
        category="显隐",
        description="显示自己的身形",
        params=[],
        usage_example="Action: fade_in",
    )
    result["fade_out"] = ActionDef(
        name="fade_out",
        category="显隐",
        description="突然消失不见",
        params=[],
        usage_example="Action: fade_out",
    )

    return result


# 每次 import 时构建，但 generate_action_section() 调用时也会重新读取 config
REGISTRY: dict[str, ActionDef] = _build_duration_registry()


def generate_action_section(exclude: list[str] | None = None) -> str:
    """动态生成动作表描述，每次调用时根据当前 config 计算时长范围。

    紧凑格式：每个动作单行，保留动作名、参数名、取值范围与示例值，
    仅删除冗余描述性文字以压缩 prompt 体积。
    """
    # 重新构建 REGISTRY 以反映最新的 config
    registry = _build_duration_registry()
    _exclude = set(exclude or [])
    included = len(registry) - len(_exclude)
    lines = [f"=== 可用动作（共 {included} 个）==="]
    for name, a in registry.items():
        if name in _exclude:
            continue
        if a.params:
            entry = f"- {a.name} [{' '.join(a.params)}] {a.description}"
        else:
            entry = f"- {a.name} 无参数 {a.description}"
        # 示例去掉 "Action: " 前缀；无参数动作示例与动作名相同，省略
        ex = a.usage_example
        if ex.startswith("Action: "):
            ex = ex[len("Action: "):]
        if ex and ex != a.name:
            entry += f" | 示例: {ex}"
        lines.append(entry)
    return "\n".join(lines)


ACTION_NAMES: list[str] = list(REGISTRY.keys())