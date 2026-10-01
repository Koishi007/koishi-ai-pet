"""本地兜底决策：LLM 不可用或抢锁失败时的降级产出。"""

import logging
import random
import re
from datetime import datetime

from pet.brain.output import ActionStep, BehaviorOutput

logger = logging.getLogger(__name__)

# 兜底动作池：动作名必须在 ACTION_NAMES 内，LLM 未介入时的随机产出
_LOCAL_ACTIONS = [
    ("sit", "歇一会儿～"),
    ("drive", "骑上我心爱的小摩托～"),
    ("walk", "蹦蹦跳跳真开心！"),
    ("shake_arms", "耶！太好啦！"),
    ("look_around", "那边有什么好玩的？"),
    ("stretch", "唔…伸个懒腰舒服多了～"),
    ("sleep", "呼…呼… zzz…"),
    ("thinking", "让我想想…"),
    ("bathing", "洗个澡清爽一下～"),
]

# 需要方向与距离参数的移动类动作
_MOVING_ACTIONS = ("drive", "walk")


def _random_step(action: str) -> ActionStep:
    """移动类动作补上方向与距离参数。"""
    if action in _MOVING_ACTIONS:
        return ActionStep(action, args=(random.choice(["left", "right"]), random.randint(300, 800)))
    return ActionStep(action)


def decide_local() -> BehaviorOutput:
    """自主决策的本地兜底：随机挑一个动作与台词。"""
    action, speech = random.choice(_LOCAL_ACTIONS)
    logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] [Behavior] _decide_local → {action} / {speech}")
    return BehaviorOutput(
        actions=[_random_step(action)],
        speech=speech,
        emotion="happy" if action == "shake_arms" else None,
    )


def interact_decide_local(event_hint: str) -> BehaviorOutput:
    """交互决策的本地兜底：按事件提示词给出对应反应。"""
    t = datetime.now().strftime("%H:%M:%S")

    # 检测投喂事件并提取食物名
    if "投喂" in event_hint:
        food_match = re.search(r"投喂了(.+)[。，,]", event_hint)
        food = food_match.group(1) if food_match else "好吃的"
        speech = random.choice([
            f"嗷呜～{food}真好吃！谢谢！",
            f"嗯嗯，{food}好香呀～",
            f"嘿嘿，{food}太棒啦！",
            f"哇，{food}！好开心！",
            f"嚼嚼嚼…{food}美味！",
        ])
        logger.info(f"[{t}] [Behavior] _interact_decide_local(feed {food}) → {speech}")
        return BehaviorOutput(
            actions=[ActionStep("shake_arms", kwargs={"duration": 3})],
            speech=speech,
            emotion="love",
            vitals_deltas={"satiety": 1.5, "energy": 0.5},
            mood_deltas={"joy": 1.5, "affection": 1.0},
        )

    # 检测抓取事件
    if "抓起" in event_hint or "抓住" in event_hint:
        speech = random.choice([
            "哎哎？快放我下来～",
            "呜哇，被抓住了！",
            "诶诶诶？！",
        ])
        logger.info(f"[{t}] [Behavior] _interact_decide_local(grab) → {speech}")
        return BehaviorOutput(
            actions=[ActionStep("shake_arms", kwargs={"duration": 3})],
            speech=speech,
            emotion="grim",
        )

    # 检测释放事件
    if "放下" in event_hint or "释放" in event_hint:
        speech = random.choice([
            "呼…终于落地了。",
            "踏实的感觉真好～",
            "嗯哼，还是地上舒服。",
        ])
        logger.info(f"[{t}] [Behavior] _interact_decide_local(release) → {speech}")
        return BehaviorOutput(
            actions=[ActionStep("stretch", kwargs={"duration": 4})],
            speech=speech,
        )

    # 其他交互事件：通用兜底
    action, speech = random.choice(_LOCAL_ACTIONS)
    logger.info(f"[{t}] [Behavior] _interact_decide_local(generic) → {action} / {speech}")
    return BehaviorOutput(
        actions=[_random_step(action)],
        speech=speech,
        emotion="happy" if action == "shake_arms" else None,
    )


def chat_decide_local(user_message: str) -> BehaviorOutput:
    """对话决策的本地兜底：示意还没接上模型。"""
    return BehaviorOutput(
        actions=[ActionStep("look_around", kwargs={"duration": 5})],
        speech=f"（听到了：{user_message[:10]}...但我还不会回应）",
    )
