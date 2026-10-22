"""事件日志到 `Message[]` 的投影：纯函数，只依赖事件、Profile 与 Budget。"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from pet.brain.events import Event

CHANNELS = ("native", "user-note", "system-note", "none")

_NOTE_PREFIX = "（环境记录）"

_FAKE_LABEL = re.compile(r"^\s*\[(工具调用|已执行)\]")
_TOOL_SHAPE = re.compile(r"→|\{|\w+__\w+")


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
    turn: Optional[Event] = None
    calls: list = field(default_factory=list)
    results: dict = field(default_factory=dict)
    speeches: list = field(default_factory=list)

    @property
    def text(self) -> str:
        parts = [self.turn.payload.get("text", "") if self.turn else ""]
        parts.extend(e.payload.get("text", "") for e in self.speeches)
        return join_non_empty(*parts)

    def result_for(self, call: Event) -> Optional[Event]:
        return self.results.get(call.refs.get("tool_call_id", ""))

    @property
    def complete(self) -> bool:
        return all(self.result_for(c) is not None for c in self.calls)


@dataclass
class _Item:
    kind: str          # user | assistant | speech | system | summary | group
    seq: int
    ts: float
    event: Optional[Event] = None
    group: Optional[_Group] = None

    def text(self) -> str:
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
    return _render(items, profile, budget, stats)


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


def sanitize_assistant_text(text: str) -> tuple[str, int]:
    """把伪调用行替换为 `[已省略]`，返回 (文本, 命中行数)。"""
    if not text:
        return text, 0
    hits = 0
    lines = []
    for line in text.splitlines():
        if _FAKE_LABEL.match(line) and _TOOL_SHAPE.search(line):
            hits += 1
            lines.append("[已省略]")
        else:
            lines.append(line)
    return ("\n".join(lines), hits) if hits else (text, 0)


def replay_args(call: Event, mode: str = "minimal") -> str:
    """从 args_digest 重建参数 JSON；无法还原的部分省略。"""
    if mode == "empty":
        return "{}"
    digest = (call.payload.get("args_digest") or "").strip()
    if not digest or digest == "-":
        return "{}"
    args = {}
    for chunk in digest.split(", "):
        if "=" not in chunk:
            continue
        key, _, raw = chunk.partition("=")
        if not key:
            continue
        if raw == "{…}":
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
    from datetime import datetime
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
    groups: dict[str, _Group] = {}
    items: list[_Item] = []

    if snapshot is not None:
        for summary in snapshot.payload.get("summaries") or []:
            text = (summary or {}).get("text") if isinstance(summary, dict) else None
            if text:
                items.append(_Item("summary", snapshot.seq, snapshot.ts,
                                   event=Event(snapshot.seq, snapshot.session_id, snapshot.ts,
                                               "summary", None, {"text": text}, {})))

    for event in events:
        if event.type == "failed_turn":
            stats["failed_turns"] = stats.get("failed_turns", 0) + 1
            continue
        if _hidden(event.seq, ranges):
            continue

        if event.type == "tool_call":
            gid = event.group_id or f"orphan-{event.seq}"
            group = groups.get(gid)
            if group is None:
                group = _Group(gid, event.seq, event.ts)
                groups[gid] = group
                items.append(_Item("group", event.seq, event.ts, group=group))
            else:
                group.seq = min(group.seq, event.seq)
            group.calls.append(event)
            continue

        if event.type == "tool_result":
            gid = event.group_id or ""
            group = groups.get(gid)
            if group is None:
                group = _Group(gid or f"orphan-{event.seq}", event.seq, event.ts)
                groups[gid or group.group_id] = group
                items.append(_Item("group", event.seq, event.ts, group=group))
            call_id = event.refs.get("tool_call_id") or (event.payload.get("tool_call_id") or "")
            group.results[call_id] = event
            continue

        if event.type == "assistant_turn":
            gid = event.group_id or ""
            if gid and gid in groups:
                groups[gid].turn = event
                continue
            items.append(_Item("assistant", event.seq, event.ts, event=event))
            continue

        if event.type == "assistant_speech":
            gid = event.group_id or ""
            if gid and gid in groups:
                groups[gid].speeches.append(event)
                continue
            items.append(_Item("speech", event.seq, event.ts, event=event))
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
    droppable = [i for i, item in enumerate(items) if item.kind in ("user", "assistant", "speech")]
    drop = set(droppable[-budget.skip_last:])
    return [item for i, item in enumerate(items) if i not in drop]


def _trim(items: list[_Item], budget: Budget, stats: dict) -> list[_Item]:
    summaries = [it for it in items if it.kind == "summary"]
    body = [it for it in items if it.kind != "summary"]

    if budget.max_summaries >= 0 and len(summaries) > budget.max_summaries:
        summaries = summaries[-budget.max_summaries:] if budget.max_summaries else []

    def total() -> int:
        return sum(estimate_tokens(it.text()) for it in body)

    while body and (len(body) > budget.max_entries
                    or (budget.max_tokens > 0 and total() > budget.max_tokens)):
        if len(body) <= 1:
            break
        idx = next((i for i, it in enumerate(body) if it.kind == "group"), 0)
        dropped = body.pop(idx)
        if dropped.kind == "group":
            stats["dropped_groups"] = stats.get("dropped_groups", 0) + 1

    return sorted(summaries + body, key=lambda it: it.seq)


def _render(items: list[_Item], profile: Profile, budget: Budget, stats: dict) -> list[dict]:
    out: list[dict] = []
    for item in items:
        if item.kind == "group":
            out.extend(_render_group(item.group, profile, stats))
            continue
        text = item.text()
        if not text:
            continue
        if item.kind in ("assistant", "speech"):
            text, hits = sanitize_assistant_text(text)
            if hits:
                stats["sanitized_lines"] = stats.get("sanitized_lines", 0) + hits
        prefix = format_event_time(item.ts, budget.now)
        content = f"[{prefix}] {text}" if prefix else text
        if item.kind == "user":
            out.append({"role": "user", "content": content})
        elif item.kind in ("assistant", "speech"):
            out.append({"role": "assistant", "content": content})
        elif item.kind in ("system", "summary"):
            out.append({"role": "system", "content": content})
    return out


def _render_group(group: _Group, profile: Profile, stats: dict) -> list[dict]:
    text, hits = sanitize_assistant_text(group.text)
    if hits:
        stats["sanitized_lines"] = stats.get("sanitized_lines", 0) + hits
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
