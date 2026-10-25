"""事件日志到 `Message[]` 的投影：纯函数，只依赖事件、Profile 与 Budget。"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from pet.brain.events import ASSISTANT_TURN_VERSION, Event

CHANNELS = ("native", "user-note", "system-note", "none")

_NOTE_PREFIX = "（环境记录）"

# 工具组配额占对话条数的比例
_GROUP_RATIO = 0.4


@dataclass(frozen=True)
class Profile:
    """模型能力：只影响投影形态，不影响存储。"""
    name: str = "native"
    tool_channel: str = "native"
    supports_image: bool = False
    replay_args_mode: str = "minimal"

    def __post_init__(self):
        if self.tool_channel not in CHANNELS:
            raise ValueError(f"unknown tool_channel: {self.tool_channel}")


@dataclass(frozen=True)
class Budget:
    """条数与 token 预算；`now` 由调用方传入以保持纯函数。"""
    max_entries: int = 15
    max_groups: int = -1   # -1 表示按 max_entries 比例派生
    max_tokens: int = 8192
    max_summaries: int = 3
    evict_before_ts: float = 0.0
    now: float = 0.0
    skip_last: int = 0


@dataclass
class _Group:
    group_id: str
    seq: int
    ts: float
    calls: list = field(default_factory=list)
    results: dict = field(default_factory=dict)
    speeches: list = field(default_factory=list)

    @property
    def text(self) -> str:
        return join_non_empty(*(e.payload.get("text", "") for e in self.speeches))

    @property
    def rendered_text(self) -> str:
        """给模型的文本：aside 带 Speech 标签，各自分段。"""
        parts = []
        for e in self.speeches:
            parts.extend(_speech_lines(e.payload.get("text") or ""))
        return join_non_empty(*parts)

    def result_for(self, call: Event) -> Optional[Event]:
        return self.results.get(call.refs.get("tool_call_id", ""))

    @property
    def complete(self) -> bool:
        return all(self.result_for(c) is not None for c in self.calls)


@dataclass
class _Item:
    kind: str          # user | assistant_fields | system | summary | group
    seq: int
    ts: float
    event: Optional[Event] = None
    group: Optional[_Group] = None
    rendered: str = ""  # 结构化字段渲染结果

    def text(self) -> str:
        if self.rendered:
            return self.rendered
        if self.kind == "group":
            return self.group.text
        return (self.event.payload.get("text") or "") if self.event else ""


def project(events: list, profile: Optional[Profile] = None,
            budget: Optional[Budget] = None, stats: Optional[dict] = None) -> list[dict]:
    """把事件日志投影成逻辑消息列表；不做 system 合并。"""
    profile = profile or Profile()
    budget = budget or Budget()
    stats = stats if stats is not None else {}

    live = _drop_superseded(events)
    snapshot, live = _apply_snapshot(live)
    ranges = _covered_ranges(live)
    items = _build_items(live, snapshot, ranges, stats)
    items = _expire_tool_groups(items, budget, stats)
    items = _apply_skip_last(items, budget)
    items = _trim(items, budget, stats)
    return _render(items, profile, budget)


def degrade_messages(messages: list, channel: str = "user-note") -> list:
    """把已投影的 native 工具消息就地降级为文本形态，用于端点拒绝后的重试。"""
    out: list = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if not isinstance(message, dict):
            out.append(message)
            index += 1
            continue
        calls = message.get("tool_calls")
        if message.get("role") == "assistant" and calls:
            briefs: dict = {}
            cursor = index + 1
            while cursor < len(messages) and isinstance(messages[cursor], dict) \
                    and messages[cursor].get("role") == "tool":
                briefs[messages[cursor].get("tool_call_id")] = messages[cursor].get("content") or ""
                cursor += 1
            if message.get("content"):
                out.append({"role": "assistant", "content": message["content"]})
            lines = [f"- {briefs.get(call.get('id'), '操作已完成')}" for call in calls]
            note = f"{_NOTE_PREFIX}\n" + "\n".join(lines)
            if channel == "system-note":
                out.append({"role": "system", "content": note})
            elif channel != "none":
                out.append({"role": "user", "content": note})
            index = cursor
            continue
        if message.get("role") == "tool":
            index += 1
            continue
        out.append(message)
        index += 1
    return out


def _speech_lines(text: str) -> list[str]:
    """一段或多段台词：逐行加 Speech 标签，分段不合并不丢行。"""
    return [f"Speech: {line}" for line in text.splitlines() if line.strip()]


def uses_native_tools(messages: list) -> bool:
    """消息里是否还有 native 工具结构：assistant 的 tool_calls 或 role=tool。"""
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "tool" or message.get("tool_calls"):
            return True
    return False


# 摘要文本里值可能带引号与逗号，只在后接 `键=` 的逗号处切分
_ARGS_SPLIT = re.compile(r", (?=[A-Za-z_][\w]*=)")


def replay_args(call: Event, mode: str = "minimal") -> str:
    """从 args_digest 重建参数 JSON；无法还原的部分省略。"""
    if mode == "empty":
        return "{}"
    digest = (call.payload.get("args_digest") or "").strip()
    if not digest or digest == "-":
        return "{}"
    args = {}
    for chunk in _ARGS_SPLIT.split(digest):
        if "=" not in chunk:
            continue
        key, _, raw = chunk.partition("=")
        if not key:
            continue
        if raw == "{…}":
            continue
        if raw.startswith('"'):
            try:
                args[key] = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                args[key] = raw
            continue
        if raw.endswith("…"):
            args[key] = raw[:-1]
            continue
        try:
            args[key] = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            args[key] = raw
    return json.dumps(args, ensure_ascii=False) if args else "{}"


def format_event_time(ts: float, now: float = 0.0) -> str:
    """事件时间前缀；now 为 0 时只输出时钟。"""
    ref = datetime.fromtimestamp(ts)
    if not now:
        return ref.strftime("%H:%M")
    today = datetime.fromtimestamp(now).date()
    days = (today - ref.date()).days
    if days <= 0:
        return ref.strftime("%H:%M")
    if days == 1:
        return "昨天 " + ref.strftime("%H:%M")
    if days == 2:
        return "前天 " + ref.strftime("%H:%M")
    return ref.strftime("%m-%d %H:%M")


def format_relative_time(ts: float, now: float = 0.0) -> str:
    """距今多久：刚刚 / N 分钟前 / N 小时前 / 昨天 22:10 / 前天 09:12 / 10-20 22:10。

    分钟与小时只取整，提示词里的 5 分钟、30 分钟阈值可直接比较。
    now 为 0 时退化为时钟，供未设时间的调试路径使用。
    """
    if not now:
        return format_event_time(ts, 0)
    ref = datetime.fromtimestamp(ts)
    delta = max(0.0, now - ts)
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    days = (datetime.fromtimestamp(now).date() - ref.date()).days
    if days <= 0:
        return f"{int(delta // 3600)} 小时前"
    if days == 1:
        return "昨天 " + ref.strftime("%H:%M")
    if days == 2:
        return "前天 " + ref.strftime("%H:%M")
    return ref.strftime("%m-%d %H:%M")


def join_non_empty(*parts: str) -> str:
    return "\n".join(p for p in parts if p)


def join_briefs(calls: list, group: _Group) -> str:
    """一组工具的短句列表。"""
    lines = []
    for call in calls:
        result = group.result_for(call)
        brief = (result.payload.get("brief") or "操作已完成") if result else "操作结果未知"
        lines.append(f"- {brief}")
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    """与 BrainMixin 同口径的 token 估算。"""
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    other = len(text or "") - chinese
    return max(1, int(chinese * 1.5 + other * 0.25))


def _drop_superseded(events: list) -> list:
    replaced = set()
    for event in events:
        value = event.refs.get("supersedes")
        if isinstance(value, int):
            replaced.add(value)
        elif isinstance(value, list):
            replaced.update(v for v in value if isinstance(v, int))
    return [e for e in events if e.seq not in replaced]


def _apply_snapshot(events: list) -> tuple[Optional[Event], list]:
    snapshots = [e for e in events if e.type == "snapshot"]
    if not snapshots:
        return None, events
    latest = max(snapshots, key=lambda e: e.seq)
    covered_until = int(latest.payload.get("covered_until") or 0)
    return latest, [e for e in events if e.seq > covered_until]


def _covered_ranges(events: list) -> list[tuple[int, int]]:
    ranges = []
    for event in events:
        if event.type != "summary":
            continue
        covers = event.refs.get("covers")
        if isinstance(covers, (list, tuple)) and len(covers) == 2:
            try:
                ranges.append((int(covers[0]), int(covers[1])))
            except (TypeError, ValueError):
                continue
    return ranges


def _hidden(seq: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= seq <= end for start, end in ranges)


def _build_items(events: list, snapshot: Optional[Event],
                 ranges: list[tuple[int, int]], stats: dict) -> list[_Item]:
    # 第一遍先建组并填充内容：aside 可能排在 tool_call 之前，第二遍必须能查到
    groups: dict[str, _Group] = {}
    for event in events:
        if event.type not in ("tool_call", "tool_result"):
            continue
        if _hidden(event.seq, ranges):
            continue
        gid = event.group_id or f"orphan-{event.seq}"
        group = groups.get(gid)
        if group is None:
            group = _Group(gid, event.seq, event.ts)
            groups[gid] = group
        else:
            group.seq = min(group.seq, event.seq)
        if event.type == "tool_call":
            group.calls.append(event)
        else:
            call_id = event.refs.get("tool_call_id") or event.payload.get("tool_call_id") or ""
            group.results[call_id] = event

    items: list[_Item] = []
    if snapshot is not None:
        for summary in snapshot.payload.get("summaries") or []:
            text = (summary or {}).get("text") if isinstance(summary, dict) else None
            if text:
                items.append(_Item("summary", snapshot.seq, snapshot.ts,
                                   event=Event(snapshot.seq, snapshot.session_id, snapshot.ts,
                                               "summary", None, {"text": text}, {})))

    emitted: set[str] = set()

    def emit_group(gid: str, seq: int, ts: float):
        """组 item 首次出现时插入，保持 seq 顺序。"""
        if gid in emitted:
            return
        emitted.add(gid)
        items.append(_Item("group", seq, ts, group=groups[gid]))

    for event in events:
        if event.type == "failed_turn":
            stats["failed_turns"] = stats.get("failed_turns", 0) + 1
            continue
        if _hidden(event.seq, ranges):
            continue

        if event.type == "assistant_turn":
            if event.payload.get("v") == ASSISTANT_TURN_VERSION:
                rendered = _render_assistant_fields(event.payload)
                if rendered:
                    items.append(_Item("assistant_fields", event.seq, event.ts,
                                       event=event, rendered=rendered))
            continue

        gid = event.group_id or f"orphan-{event.seq}"
        if gid in groups:
            if event.type == "assistant_speech":
                groups[gid].speeches.append(event)
            emit_group(gid, event.seq, event.ts)
            continue

        if event.type == "user_msg":
            items.append(_Item("user", event.seq, event.ts, event=event))
        elif event.type == "system_note":
            items.append(_Item("system", event.seq, event.ts, event=event))
        elif event.type == "summary":
            items.append(_Item("summary", event.seq, event.ts, event=event))

    for group in groups.values():
        missing = [c for c in group.calls if group.result_for(c) is None]
        if missing:
            stats["orphan_groups"] = stats.get("orphan_groups", 0) + 1
    return items


def _expire_tool_groups(items: list[_Item], budget: Budget, stats: dict) -> list[_Item]:
    if not budget.evict_before_ts:
        return items
    kept = []
    for item in items:
        if item.kind == "group" and item.ts < budget.evict_before_ts:
            stats["dropped_groups"] = stats.get("dropped_groups", 0) + 1
            continue
        kept.append(item)
    return kept


def _apply_skip_last(items: list[_Item], budget: Budget) -> list[_Item]:
    if budget.skip_last <= 0:
        return items
    droppable = [i for i, item in enumerate(items)
                 if item.kind in ("user", "assistant_fields")]
    drop = set(droppable[-budget.skip_last:])
    return [item for i, item in enumerate(items) if i not in drop]


def _trim(items: list[_Item], budget: Budget, stats: dict) -> list[_Item]:
    """摘要、工具组、对话各自配额：工具组不会被对话挤掉，反之亦然。"""
    summaries = [it for it in items if it.kind == "summary"]
    groups = [it for it in items if it.kind == "group"]
    dialogue = [it for it in items if it.kind not in ("summary", "group")]

    if 0 <= budget.max_summaries < len(summaries):
        summaries = summaries[-budget.max_summaries:] if budget.max_summaries else []

    group_limit = budget.max_groups
    if group_limit < 0:
        group_limit = max(3, int(budget.max_entries * _GROUP_RATIO))
    if 0 <= group_limit < len(groups):
        stats["dropped_groups"] = stats.get("dropped_groups", 0) + len(groups) - group_limit
        groups = groups[-group_limit:]

    def total() -> int:
        return sum(estimate_tokens(it.text()) for it in dialogue + groups)

    # 对话配额独立于工具组：组多不会压缩 user/assistant 的席位
    while len(dialogue) > budget.max_entries:
        dialogue.pop(0)

    # token 紧张时先丢最旧的工具组：它只对当前任务有效，对话由摘要兜底
    while budget.max_tokens > 0 and total() > budget.max_tokens:
        if len(groups) > 1:
            groups.pop(0)
            stats["dropped_groups"] = stats.get("dropped_groups", 0) + 1
        elif dialogue:
            dialogue.pop(0)
        else:
            break

    return sorted(summaries + groups + dialogue, key=lambda it: it.seq)


def _render(items: list[_Item], profile: Profile, budget: Budget) -> list[dict]:
    out: list[dict] = []
    for item in items:
        if item.kind == "group":
            out.extend(_render_group(item.group, profile))
            continue
        text = item.text()
        if not text:
            continue
        if item.kind == "assistant_fields":
            out.append({"role": "assistant", "content": text})
            continue
        prefix = format_relative_time(item.ts, budget.now)
        content = f"[{prefix}] {text}" if prefix else text
        if item.kind == "user":
            out.append({"role": "user", "content": content})
        elif item.kind in ("system", "summary"):
            out.append({"role": "system", "content": content})
    return out


def _render_assistant_fields(payload: dict) -> str:
    """结构化字段按协议顺序渲染成字段行；空字段跳过。"""
    lines = []
    summary = payload.get("summary")
    if summary:
        lines.append(f"Summary: {summary}")
    emotion = payload.get("emotion")
    if emotion:
        lines.append(f"Emotion: {emotion}")
    for text in payload.get("speech_parts") or []:
        if str(text).strip():
            lines.append(f"Speech: {text}")
    for action in payload.get("actions") or []:
        line = _action_line(action)
        if line:
            lines.append(line)
    memory = payload.get("memory")
    if memory:
        lines.append(f"Memory: {memory}")
    for label in ("Mood", "Vitals"):
        line = _delta_line(label, payload.get(label.lower()))
        if line:
            lines.append(line)
    return "\n".join(lines)


def _action_line(action) -> str:
    """一条 Action 行：args 为位置参数，kwargs 为键值参数。"""
    if not isinstance(action, dict):
        return ""
    name = str(action.get("name") or "").strip()
    if not name:
        return ""
    parts = [name]
    parts.extend(str(value) for value in action.get("args") or [])
    parts.extend(f"{key}={value}" for key, value in (action.get("kwargs") or {}).items())
    return "Action: " + " ".join(parts)


def _delta_line(label: str, values) -> str:
    """Mood / Vitals 行：值统一带符号，整数不带小数点。"""
    if not isinstance(values, dict):
        return ""
    parts = []
    for key, value in values.items():
        try:
            parts.append(f"{key}{float(value):+g}")
        except (TypeError, ValueError):
            continue
    if not parts:
        return ""
    return f"{label}: " + " ".join(parts)


def _render_group(group: _Group, profile: Profile) -> list[dict]:
    text = group.rendered_text
    channel = profile.tool_channel

    if channel == "native" and group.complete and group.calls:
        message = {"role": "assistant", "content": text or None}
        message["tool_calls"] = [
            {"id": c.refs.get("tool_call_id", f"call_{c.seq}"),
             "type": "function",
             "function": {"name": c.payload.get("name", ""),
                          "arguments": replay_args(c, profile.replay_args_mode)}}
            for c in group.calls
        ]
        out = [message]
        for call in group.calls:
            result = group.result_for(call)
            out.append({
                "role": "tool",
                "tool_call_id": call.refs.get("tool_call_id", f"call_{call.seq}"),
                "content": (result.payload.get("brief") or "操作已完成") if result else "操作结果未知",
            })
        return out

    if channel == "none":
        return [{"role": "assistant", "content": text}] if text else []

    note = f"{_NOTE_PREFIX}\n{join_briefs(group.calls, group)}"
    if channel == "system-note":
        out = []
        if text:
            out.append({"role": "assistant", "content": text})
        out.append({"role": "system", "content": note})
        return out
    out = []
    if text:
        out.append({"role": "assistant", "content": text})
    out.append({"role": "user", "content": note})
    return out
