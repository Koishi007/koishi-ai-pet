"""Action Registry - 可被 LLM 调用的动作定义。"""

from dataclasses import dataclass, field
from typing import List

# 耗时类动作的兜底
DEFAULT_ACTION_DURATIONS = {
    "sit": 10,
    "thinking": 5,
    "sleep": 12,
}


@dataclass
class ActionDef:
    name: str
    category: str
    description: str
    params: List[str] = field(default_factory=list)
    usage_example: str = ""


REGISTRY: dict[str, ActionDef] = {
    "walk": ActionDef(
        name="walk",
        category="移动",
        description="水平行走。必须指定方向（left/right）和距离（300-1000px），约 7px/ms。不可指定 duration。",
        params=["direction: left | right", "distance: 像素，500-1000"],
        usage_example="Action: walk right 800",
    ),
    "bounce": ActionDef(
        name="bounce",
        category="移动",
        description="弹跳移动。适合跳跃到其他窗口上。direction=left/right 指定水平方向，distance 水平距离，height 向上跳跃高度）",
        params=["direction: left | right", "distance: 水平像素(范围0-800，0代表垂直往上跳，此时方向任意)", "height: 向上跳跃高度（必须大于0）"],
        usage_example="Action: bounce direction=right distance=400 height=200",
    ),
    "sit": ActionDef(
        name="sit",
        category="驻留",
        description="坐下。耗时动作，必须写 duration=秒（5-15s），适合收尾撑时长。",
        params=["duration: 秒，5-15"],
        usage_example="Action: sit duration=10",
    ),
    "sleep": ActionDef(
        name="sleep",
        category="驻留",
        description="睡觉。耗时动作，必须写 duration=秒（10-15s），适合安静场景收尾。",
        params=["duration: 秒，10-15"],
        usage_example="Action: sleep duration=12",
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
    "thinking": ActionDef(
        name="thinking",
        category="驻留",
        description="沉思/思考。耗时动作，必须写 duration=秒（3-8s），站着不动但表情思考状。",
        params=["duration: 秒，3-8"],
        usage_example="Action: thinking duration=5",
    ),
    "fade_in": ActionDef(
        name="fade_in",
        category="显隐",
        description="淡入显示。窗口从透明到可见，与 fade_out 成对使用（必须先 out 后 in），中间可以夹带其他动作。禁止单独出现。",
        params=[],
        usage_example="Action: fade_in",
    ),
    "fade_out": ActionDef(
        name="fade_out",
        category="显隐",
        description="淡出隐藏。窗口从可见到透明，与 fade_out 成对使用（必须先 out 后 in），中间可以夹带其他动作。禁止单独出现。",
        params=[],
        usage_example="Action: fade_out",
    ),
}


def generate_action_section() -> str:
    categories: dict[str, list[str]] = {"移动": [], "驻留": [], "显隐": []}
    for name, a in REGISTRY.items():
        params_str = "，".join(a.params) if a.params else "无额外参数"
        if a.params:
            params_str = " 参数：" + params_str
        entry = f"- {a.name}: {a.description}{params_str}"
        if a.usage_example:
            entry += f"\n  示例：{a.usage_example}"
        categories[a.category].append(entry)

    lines = [f"=== 可用动作（共 {len(REGISTRY)} 个）==="]
    for cat_label in ("移动", "驻留", "显隐"):
        lines.append(f"\n--- {cat_label} ---")
        lines.extend(categories[cat_label])
    return "\n".join(lines)

ACTION_NAMES: list[str] = list(REGISTRY.keys())
