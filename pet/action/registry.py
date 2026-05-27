"""Action Registry
可被llm调用的动作
1. REGISTRY 字典中添加 ActionDef
2. 在 pet/action/action.py 中实现对应方法
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class ActionDef:
    """动作定义。"""
    name: str                             # 动作名（与 action.py 中方法名一致）
    category: str                         # 类别：移动 / 驻留 / 显隐
    description: str                      # 一段话描述，注入 prompt
    params: List[str] = field(default_factory=list)  # 参数列表（空=队列驱动，仅需 duration）
    usage_example: str = ""               # 用法示例


REGISTRY: dict[str, ActionDef] = {
    # ── 移动类 ──
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
        description="弹跳移动到目标位置。适合跳跃到其他窗口上。dx=水平偏移（正=右），dy=垂直偏移（正=下，负=上，通常为负值表示向上跳）。默认 duration=800。",
        params=["dx: 水平像素偏移", "dy: 垂直像素偏移（通常为负值）", "duration: 毫秒（可选，默认 800）"],
        usage_example="Action: bounce dx=300 dy=-200 duration=800",
    ),

    # ── 驻留类（队列驱动，必须 duration=秒）──
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
        description="张望/环顾四周。耗时动作，必须写 duration=秒（5-10s），穿插在 walk 和 sit 之间。",
        params=["duration: 秒，5-10"],
        usage_example="Action: look_around duration=10",
    ),
    "stretch": ActionDef(
        name="stretch",
        category="驻留",
        description="伸展/伸懒腰。耗时动作，必须写 duration=秒（3-6s），穿插在 walk 和 sit 之间。",
        params=["duration: 秒，3-6"],
        usage_example="Action: stretch duration=4",
    ),
    "thinking": ActionDef(
        name="thinking",
        category="驻留",
        description="沉思/思考。耗时动作，必须写 duration=秒（3-8s），站着不动但表情思考状。",
        params=["duration: 秒，3-8"],
        usage_example="Action: thinking duration=5",
    ),

    # ── 显隐类 ──
    "fade_in": ActionDef(
        name="fade_in",
        category="显隐",
        description="淡入显示。窗口从透明到可见，与 fade_out 成对使用（先 out 后 in），中间可以夹带其他动作。禁止单独出现。",
        params=[],
        usage_example="Action: fade_in",
    ),
    "fade_out": ActionDef(
        name="fade_out",
        category="显隐",
        description="淡出隐藏。窗口从可见到透明，必须先 out 后 in，中间可以夹带其他动作，与 fade_in 成对使用。禁止单独出现。",
        params=[],
        usage_example="Action: fade_out",
    ),
}


def generate_action_section() -> str:
    """生成 prompt 中注入的动作描述块。

    返回格式：
        可用动作（共 N 个）：
        
        === 移动 ===
        - walk: ...
        - bounce: ...
        
        === 驻留 ===
        - sit: duration=秒（5-15s）...
        ...
        
        === 显隐 ===
        - fade_in: ...
    """
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


# 便捷导出：动作名列表
ACTION_NAMES: list[str] = list(REGISTRY.keys())
