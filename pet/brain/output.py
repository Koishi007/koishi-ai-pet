"""LLM 决策的输出契约：行为输出、字段白名单与取消信号，供解析、存储与编排共同引用。"""

import re
from dataclasses import dataclass, field
from typing import Optional

from pet.action.registry import ACTION_NAMES

# 协议允许的情绪名；提示词与 UI 共用这一份顺序
EMOTIONS = (
    "happy", "excited", "sad", "angry", "surprised", "thinking", "sleepy",
    "love", "cool", "shy", "scared", "hungry", "curious", "proud",
    "bored", "crazy",
)
EMOTION_NAMES = frozenset(EMOTIONS)

# Mood / Vitals 各自允许的增量字段
MOOD_FIELDS = ("affection", "joy", "sanity")
VITALS_FIELDS = ("satiety", "energy")

_EMOTION_SPLIT = re.compile(r"[,，、\s]+")


def normalize_emotion(raw: str) -> list[str]:
    """情绪行：逗号/顿号/空白分隔，只保留白名单内的名字，去重保序。"""
    names = []
    for part in _EMOTION_SPLIT.split(raw or ""):
        name = part.strip().lower()
        if name in EMOTION_NAMES and name not in names:
            names.append(name)
    return names


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
    used_tools: bool = False  # 执行过有副作用的工具，不再重试格式修复
    summary: Optional[str] = None
    memory_line: Optional[str] = None
    emotion: Optional[str] = None
    mood_deltas: Optional[dict] = None  # {"affection": ±值, "joy": ±值, "sanity": ±值}
    vitals_deltas: Optional[dict] = None  # {"satiety": ±值, "energy": ±值}
    action_fallback: bool = False  # 动作来自兜底 sit，不算模型输出
    raw: Optional[str] = field(default=None, repr=False)  # 本次响应原文；入库只做审计，不进 repr

    def has_fields(self) -> bool:
        """是否解析出至少一个协议字段。兜底动作不算，未解析出字段的正文不进历史。"""
        actions = [] if self.action_fallback else self.actions
        return bool(self.summary or self.speech or self.speech_parts or actions
                    or self.memory_line or self.emotion or self.mood_deltas
                    or self.vitals_deltas)

    def assistant_fields(self) -> dict:
        """结构化落库字段：空字段与兜底动作不写入。"""
        fields = {}
        if self.summary:
            fields["summary"] = self.summary
        if self.emotion:
            fields["emotion"] = self.emotion
        if self.speech_parts:
            fields["speech_parts"] = list(self.speech_parts)
        elif self.speech:
            fields["speech_parts"] = [self.speech]
        if self.actions and not self.action_fallback:
            fields["actions"] = [_action_fields(step) for step in self.actions]
        if self.memory_line:
            fields["memory"] = self.memory_line
        if self.mood_deltas:
            fields["mood"] = dict(self.mood_deltas)
        if self.vitals_deltas:
            fields["vitals"] = dict(self.vitals_deltas)
        return fields


def _action_fields(step: ActionStep) -> dict:
    """一条动作：位置参数与键值参数分开，便于投影重建 Action 行。"""
    fields = {"name": step.name}
    if step.args:
        fields["args"] = list(step.args)
    if step.kwargs:
        fields["kwargs"] = dict(step.kwargs)
    return fields


def sanitize_assistant_fields(fields: dict) -> dict:
    """落库前校验：类型不符、取值非法或保留键的字段直接丢弃。"""
    clean = {}
    summary = fields.get("summary")
    if isinstance(summary, str) and summary.strip():
        clean["summary"] = summary.strip()
    emotion = fields.get("emotion")
    if isinstance(emotion, str):
        names = normalize_emotion(emotion)
        if names:
            clean["emotion"] = ", ".join(names)
    parts = fields.get("speech_parts")
    if isinstance(parts, list):
        kept = [part.strip() for part in parts if isinstance(part, str) and part.strip()]
        if kept:
            clean["speech_parts"] = kept
    actions = fields.get("actions")
    if isinstance(actions, list):
        kept = [action for action in (_sanitize_action(item) for item in actions) if action]
        if kept:
            clean["actions"] = kept
    memory = fields.get("memory")
    if isinstance(memory, str) and memory.strip():
        clean["memory"] = memory.strip()
    for key, allowed in (("mood", MOOD_FIELDS), ("vitals", VITALS_FIELDS)):
        values = fields.get(key)
        if not isinstance(values, dict):
            continue
        kept = {str(name): value for name, value in values.items()
                if str(name) in allowed and isinstance(value, (int, float))
                and not isinstance(value, bool)}
        if kept:
            clean[key] = kept
    return clean


def _sanitize_action(action) -> Optional[dict]:
    """一条动作的落库形态：名字必须在动作表里，参数只保留标量。"""
    if not isinstance(action, dict):
        return None
    name = action.get("name")
    if not isinstance(name, str) or name.strip() not in ACTION_NAMES:
        return None
    clean = {"name": name.strip()}
    args = action.get("args")
    if isinstance(args, (list, tuple)):
        kept = [value for value in args
                if isinstance(value, (int, float, str)) and not isinstance(value, bool)]
        if kept:
            clean["args"] = kept
    kwargs = action.get("kwargs")
    if isinstance(kwargs, dict):
        kept = {str(key): value for key, value in kwargs.items()
                if isinstance(value, (int, float, str)) and not isinstance(value, bool)}
        if kept:
            clean["kwargs"] = kept
    return clean
