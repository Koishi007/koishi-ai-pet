"""来电通知：calling 动作开始时给用户弹一条系统通知。

产出在动作开始时就结算（timing=TIMING_START）：铃一响就该通知，等通话动画播完再弹已经晚了。
"""

import logging

from pet.action.outcome import TIMING_START, register
from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)

# 通知标题、正文与停留时长（毫秒）
NOTIFY_TITLE = "恋恋来电"
NOTIFY_TEXT = "恋恋给你打电话啦，关心一下她吧~"
NOTIFY_DURATION_MS = 5000


def notify_call() -> None:
    """发一条来电通知；只通知不注入事件，故返回 None。"""
    try:
        TOOL_CTX.notify(NOTIFY_TITLE, NOTIFY_TEXT, NOTIFY_DURATION_MS)
    except Exception:
        # 通知失败不该牵连动作本身，也不该冒泡进 Qt 事件循环
        logger.exception("[Calling] 来电通知发送失败，已跳过")
    return None


register("calling", notify_call, timing=TIMING_START)
