"""可被 LLM 调用的动作定义。"""

import math
from dataclasses import dataclass, field
from typing import List

from config import config


# ── 耗时类动作的动态时长计算 ──
# 每次调用时读取 config.SCHEDULER_MID_MS，确保 settings.json 修改后立即生效

# 动作时长参数定义：(名称, 最小秒数, 占调度间隔比例)
_DURATION_ACTION_DEFS = {
    "sit":      (10, 0.4),
    "thinking": ( 5, 0.2),
    "sleep":    (10, 0.4),
}

# 动作序列总时长占调度间隔的比例
_SEQUENCE_RATIO = 0.9


def target_sequence_duration() -> int:
    """动作序列的目标总时长（秒），基于调度间隔动态计算。"""
    mid_s = config.SCHEDULER_MID_MS / 1000
    return int(mid_s * _SEQUENCE_RATIO)


def min_action_count() -> int:
    """动作序列的最少动作数，基于目标总时长动态计算。"""
    return max(4, math.ceil(target_sequence_duration() / 15))


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
            description="骑小电驴。必须指定方向（left/right）和距离（300-1000px）。不可指定 duration。",
            params=["direction: left | right", "distance: 像素，500-1000"],
            usage_example="Action: drive right 800",
        ),
        "walk": ActionDef(
            name="walk",
            category="移动",
            description="行走。一蹦一蹦地走，活泼可爱。必须指定方向（left/right）和距离（300-1000px）。不可指定 duration。",
            params=["direction: left | right", "distance: 像素，500-1000"],
            usage_example="Action: walk right 800",
        ),
        "bounce": ActionDef(
            name="bounce",
            category="移动",
            description="弹跳移动。适合跳跃到其他窗口上。direction=left/right 指定水平方向，distance 水平距离，height 向上跳跃高度",
            params=["direction: left | right", "distance: 水平像素(范围0-800，0代表垂直往上跳，此时方向任意)", "height: 向上跳跃高度（必须大于0）"],
            usage_example="Action: bounce direction=right distance=400 height=200",
        ),
        "shake_arms": ActionDef(
            name="shake_arms",
            category="驻留",
            description="开心摇晃手臂，表达兴奋或喜悦，无需参数。",
            params=[],
            usage_example="Action: shake_arms",
        ),
        "look_around": ActionDef(
            name="look_around",
            category="驻留",
            description="张望/环顾四周，穿插在 walk 和 sit 之间，无需参数。",
            params=[],
            usage_example="Action: look_around",
        ),
        "stretch": ActionDef(
            name="stretch",
            category="驻留",
            description="伸展/伸懒腰，穿插在 walk 和 sit 之间，无需参数。",
            params=[],
            usage_example="Action: stretch",
        ),
        "fishing": ActionDef(
            name="fishing",
            category="驻留",
            description="钓鱼。拿出钓竿左右摇晃，并不能真的钓到鱼",
            params=[],
            usage_example="Action: fishing",
        ),
        "fade_in": ActionDef(
            name="fade_in",
            category="显隐",
            description="淡入显示。从透明到可见，与 fade_out 成对使用（必须先 out 后 in），中间可以夹带其他动作。禁止单独出现。",
            params=[],
            usage_example="Action: fade_in",
        ),
        "fade_out": ActionDef(
            name="fade_out",
            category="显隐",
            description="淡出隐藏。从可见到透明，与 fade_in 成对使用（必须先 out 后 in），中间可以夹带其他动作。禁止单独出现。",
            params=[],
            usage_example="Action: fade_out",
        ),
    }

    # 动态生成带 duration 参数的动作
    for name in _DURATION_ACTION_DEFS:
        lo, hi = duration_range(name)
        dur = default_duration(name)
        desc_map = {
            "sit": "坐下。耗时动作，必须写 duration=秒，适合收尾撑时长。",
            "sleep": "睡觉。耗时动作，必须写 duration=秒，适合安静场景收尾。",
            "thinking": "沉思/思考。耗时动作，必须写 duration=秒，站着不动但表情思考状。",
        }
        result[name] = ActionDef(
            name=name,
            category="驻留",
            description=desc_map[name],
            params=[f"duration: 秒，{lo}-{hi}"],
            usage_example=f"Action: {name} duration={dur}",
        )

    return result


# 每次 import 时构建，但 generate_action_section() 调用时也会重新读取 config
REGISTRY: dict[str, ActionDef] = _build_duration_registry()


def generate_action_section(exclude: list[str] | None = None) -> str:
    """动态生成动作表描述，每次调用时根据当前 config 计算时长范围。"""
    # 重新构建 REGISTRY 以反映最新的 config
    registry = _build_duration_registry()
    _exclude = set(exclude or [])
    categories: dict[str, list[str]] = {"移动": [], "驻留": [], "显隐": []}
    for name, a in registry.items():
        if name in _exclude:
            continue
        params_str = "，".join(a.params) if a.params else "无额外参数"
        if a.params:
            params_str = " 参数：" + params_str
        entry = f"- {a.name}: {a.description}{params_str}"
        if a.usage_example:
            entry += f"\n  示例：{a.usage_example}"
        categories[a.category].append(entry)

    included = len(registry) - len(_exclude)
    lines = [f"=== 可用动作（共 {included} 个）==="]
    for cat_label in ("移动", "驻留", "显隐"):
        if categories[cat_label]:
            lines.append(f"\n--- {cat_label} ---")
            lines.extend(categories[cat_label])
    return "\n".join(lines)


ACTION_NAMES: list[str] = list(REGISTRY.keys())