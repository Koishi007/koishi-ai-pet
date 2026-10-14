"""动作产出（outcome）"""

from dataclasses import dataclass
from typing import Callable, Optional

# 处理器返回要注入的文案；返回空串或 None 视为本次无产出
OutcomeHandler = Callable[[], Optional[str]]

# 特效处理器：返回要播放的粒子特效名；返回空串或 None 表示本次无特效
EffectHandler = Callable[[], Optional[str]]

# 结算时机
TIMING_END = "end"      # 动作正常结束时结算（默认）
TIMING_START = "start"  # 动作开始执行时立刻结算

_TIMINGS = (TIMING_END, TIMING_START)


@dataclass(frozen=True)
class Outcome:
    """一个动作的产出定义。"""
    handler: OutcomeHandler
    once: bool = True  # True 只注入一轮；False 进入窗口期
    effect: Optional[EffectHandler] = None  # 命中时附带的粒子特效（可选）
    timing: str = TIMING_END  # 结算时机：见 TIMING_END / TIMING_START


_OUTCOMES: dict[str, Outcome] = {}


def register(action_name: str, handler: OutcomeHandler, once: bool = True,
             effect: Optional[EffectHandler] = None,
             timing: str = TIMING_END) -> None:
    """为动作注册产出，同名后注册的覆盖先注册的"""
    if timing not in _TIMINGS:
        raise ValueError(f"未知结算时机 {timing!r}，应为 {TIMING_END!r} 或 {TIMING_START!r}")
    _OUTCOMES[action_name] = Outcome(handler, once, effect, timing)


def outcome_for(action_name: str) -> Optional[Outcome]:
    """取动作的产出定义，未注册返回 None。"""
    return _OUTCOMES.get(action_name)


def registered_actions() -> set[str]:
    """已注册产出的动作名集合（供调试与测试）。"""
    return set(_OUTCOMES)
