"""Action Registry - 可被 LLM 调用的动作定义。"""

from dataclasses import dataclass, field
from typing import List

from config import config

# 耗时类动作的兜底时长，根据调度间隔的 50% 自动推算
_MID_S = config.SCHEDULER_MID_MS / 1000
_TARGET_S = int(_MID_S * 0.5)

DEFAULT_ACTION_DURATIONS = {
    "sit": max(10, int(_TARGET_S * 0.20)),
    "thinking": max(5, int(_TARGET_S * 0.10)),
    "sleep": max(10, int(_TARGET_S * 0.20)),
}


@dataclass
class ActionDef:
    name: str
    category: str
    description: str
    params: List[str] = field(default_factory=list)
    usage_example: str = ""


REGISTRY: dict[str, ActionDef] = {
    "normal_walk": ActionDef(
        name="normal_walk",
        category="移动",
        description="普通行走。丝滑连续移动，走到悬崖边自然掉落。必须指定方向（left/right）和距离（300-1000px）。不可指定 duration。",
        params=["direction: left | right", "distance: 像素，500-1000"],
        usage_example="Action: normal_walk right 800",
    ),
    "jump_walk": ActionDef(
        name="jump_walk",
        category="移动",
        description="弹跳行走。一蹦一蹦地走，活泼可爱。必须指定方向（left/right）和距离（300-1000px）。不可指定 duration。",
        params=["direction: left | right", "distance: 像素，500-1000"],
        usage_example="Action: jump_walk right 800",
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
        description="坐下。耗时动作，必须写 duration=秒（15-60s），适合收尾撑时长。",
        params=["duration: 秒，15-60"],
        usage_example="Action: sit duration=40",
    ),
    "sleep": ActionDef(
        name="sleep",
        category="驻留",
        description="睡觉。耗时动作，必须写 duration=秒（20-40s），适合安静场景收尾。",
        params=["duration: 秒，20-40"],
        usage_example="Action: sleep duration=30",
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
        description="沉思/思考。耗时动作，必须写 duration=秒（10-25s），站着不动但表情思考状。",
        params=["duration: 秒，10-25"],
        usage_example="Action: thinking duration=15",
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
