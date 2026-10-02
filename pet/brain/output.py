"""LLM 决策的输出契约：行为输出与取消信号，供解析、工具轮次与编排共同引用。"""

from dataclasses import dataclass, field
from typing import Optional


class CancelledError(Exception):
    """流式调用被新请求协作式取消。"""


@dataclass
class ActionStep:
    name: str
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)


@dataclass
class BehaviorOutput:
    actions: list = field(default_factory=list)
    speech: Optional[str] = None
    speech_parts: list = field(default_factory=list)
    speech_streamed: bool = False
    summary: Optional[str] = None
    memory_line: Optional[str] = None
    emotion: Optional[str] = None
    mood_deltas: Optional[dict] = None  # {"affection": ±值, "joy": ±值, "sanity": ±值}
    vitals_deltas: Optional[dict] = None  # {"satiety": ±值, "energy": ±值}
