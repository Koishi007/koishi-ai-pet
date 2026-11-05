"""命令目录：内建命令表 + 工具登记的命令，供分发、帮助与补全使用"""

from __future__ import annotations

from pet.tools.registry import TOOL_REGISTRY

from .builtins import (check, clear, policy_snapshot, quit_app, restart_app,
                       status)
from .format import key_values
from .table import BUILTIN_SPECS, usage as table_usage
from .types import PREFIX, LocalCommand, LocalContext, LocalResult

TOOL_GROUP = "工具"
GROUP_ORDER = ("通用", "恋恋", "状态与策略", TOOL_GROUP)


def commands() -> tuple[LocalCommand, ...]:
    """内建命令在前，工具注册的命令统一归入工具分组、按注册顺序在后。"""
    entries = [LocalCommand(
        name=item["name"], group=item["group"], rows=item["rows"],
        handler=_HANDLERS[item["name"]], paths=bool(item.get("paths")),
        examples=tuple(item.get("examples", ())),
        aliases=tuple(item.get("aliases", ())),
        flags=tuple(item.get("flags", ())),
        values=dict(item.get("values", {}))) for item in BUILTIN_SPECS]
    for tool, command in TOOL_REGISTRY.commands():
        entries.append(LocalCommand(
            name=command.name, group=TOOL_GROUP, rows=command.rows,
            tool=tool, resolve=command.resolve, words=command.words,
            paths=command.paths, examples=command.examples,
            aliases=command.aliases, flags=command.flags, values=command.values))
    return tuple(entries)


def command_names() -> tuple[str, ...]:
    """全部命令名，供补全使用。"""
    return tuple(command.name for command in commands())


def subcommands() -> dict[str, tuple[str, ...]]:
    """带子命令候选的命令，供补全使用。"""
    return {command.name: command.words for command in commands() if command.words}


def path_commands() -> tuple[str, ...]:
    """参数按系统程序名与路径补全的命令，供补全使用。"""
    return tuple(command.name for command in commands() if command.paths)


def aliases() -> dict[str, str]:
    """别名到命令名的映射，供补全解析参数。"""
    return {alias: command.name for command in commands() for alias in command.aliases}


def flags() -> dict[str, tuple[str, ...]]:
    """命令名到选项名，供补全使用。"""
    return {command.name: command.flags for command in commands() if command.flags}


def values() -> dict[str, dict[str, tuple[str, ...]]]:
    """命令名到「子命令 → 位置参数候选」，供补全使用。"""
    return {command.name: command.values for command in commands() if command.values}


def method_exists(full_name: str) -> bool:
    """工具方法是否可用；工具被关闭或未加载时为假。

    注册表只在这里取一次，分发层不另持一份引用，改注册表只有一个入口。
    """
    return TOOL_REGISTRY.get_method(full_name) is not None


def find(name: str) -> LocalCommand | None:
    """按名称或别名取命令，未登记返回 None。"""
    for command in commands():
        if command.name == name or name in command.aliases:
            return command
    return None


def usage(name: str, detail: str = "") -> LocalResult:
    """该命令的用法错误提示，用法取自命令表（含工具登记的命令）。"""
    return table_usage(name, detail, find(name))


def _help(ctx: LocalContext, arg: str) -> LocalResult:
    """列出命令；带参数时只显示该命令。"""
    target = (arg or "").strip().lstrip(PREFIX).lower()
    if target:
        return _command_help(target)
    entries = commands()
    groups = _groups(entries)
    ordered = [command for group in groups for command in entries
               if command.group == group]
    rows = [(usage_text, summary) for command in ordered
            for usage_text, summary in command.rows]
    width = max(len(usage_text) for usage_text, _summary in rows)
    lines = [
        "终端命令",
        "以 : 开头的输入在应用内处理，不启动子进程；其他输入交给系统 shell。",
        "",
        "用法",
    ]
    lines.extend(usage_text for usage_text, _summary in rows)
    lines.extend(["", "命令"])
    for index, group in enumerate(groups):
        if index:
            lines.append("")
        lines.append(group)
        for command in entries:
            if command.group != group:
                continue
            for usage_text, summary in command.rows:
                lines.append(f"{usage_text.ljust(width)}  {summary}")
    examples = [text for command in ordered for text in command.examples]
    if examples:
        lines.extend(["", "示例"])
        lines.extend(examples)
    return LocalResult("\n".join(lines))


def _command_help(name: str) -> LocalResult:
    """单个命令的分组、别名、用法与示例。"""
    command = find(name)
    if command is None:
        return usage("help", f"未知命令「{name}」")
    rows = [("分组", command.group)]
    if command.aliases:
        rows.append(("别名", "、".join(PREFIX + alias for alias in command.aliases)))
    lines = [f"{PREFIX}{command.name}"]
    lines.extend(key_values(rows, indent="  "))
    lines.extend(["", "用法"])
    lines.extend(f"  {usage_text}  {summary}" for usage_text, summary in command.rows)
    if command.examples:
        lines.extend(["", "示例"])
        lines.extend(f"  {example}" for example in command.examples)
    return LocalResult("\n".join(lines))


def _groups(entries: tuple[LocalCommand, ...]) -> list[str]:
    """帮助里的分组顺序完全由内建常量决定；组内按登记顺序。"""
    present = {command.group for command in entries}
    return [group for group in GROUP_ORDER if group in present]


# 内建命令的处理函数按名字绑定；纯数据表在 table.BUILTIN_SPECS
_HANDLERS = {
    "help": _help,
    "clear": clear,
    "quit": quit_app,
    "restart": restart_app,
    "status": status,
    "policy": policy_snapshot,
    "check": check,
}
