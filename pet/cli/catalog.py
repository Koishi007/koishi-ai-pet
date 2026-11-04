"""命令目录：内建命令表 + 工具登记的命令，供分发、帮助与补全使用。

工具的命令由工具在 `register(registry)` 里用 `registry.add_command()` 登记，
这里只聚合 `TOOL_REGISTRY.commands()`，不认具体工具。
"""

from __future__ import annotations

from pet.tools.registry import TOOL_REGISTRY

from .builtins import check, clear, policy_snapshot, status
from .types import EXIT_USAGE, LocalCommand, LocalContext, LocalResult

# 帮助里的分组顺序；工具声明的新分组排在后面，按名称排序
GROUP_ORDER = ("通用", "状态与策略", "计时与待办", "检索")


def commands() -> tuple[LocalCommand, ...]:
    """内建命令在前，工具注册的命令按注册顺序在后。"""
    entries = [LocalCommand(
        name=item["name"], group=item["group"], rows=item["rows"],
        handler=item["handler"], paths=bool(item.get("paths")),
        examples=tuple(item.get("examples", ()))) for item in _BUILTIN]
    for tool, command in TOOL_REGISTRY.commands():
        entries.append(LocalCommand(
            name=command.name, group=command.group, rows=command.rows,
            tool=tool, resolve=command.resolve, words=command.words,
            paths=command.paths, examples=command.examples))
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


def method_exists(full_name: str) -> bool:
    """工具方法是否可用；工具被关闭或未加载时为假。

    注册表只在这里取一次，分发层不另持一份引用，改注册表只有一个入口。
    """
    return TOOL_REGISTRY.get_method(full_name) is not None


def find(name: str) -> LocalCommand | None:
    """按名称取命令，未登记返回 None。"""
    for command in commands():
        if command.name == name:
            return command
    return None


def usage(name: str, detail: str = "") -> LocalResult:
    """该命令的用法错误提示，用法取自命令表。"""
    command = find(name)
    usages = (" | ".join(usage_text for usage_text, _summary in command.rows)
              if command else f":{name}")
    text = f"{detail}，用法：{usages}" if detail else f"用法：{usages}"
    return LocalResult(stderr=text, exit_code=EXIT_USAGE)


def _help(ctx: LocalContext, arg: str) -> LocalResult:
    """列出命令：说明、用法、分组清单与示例。"""
    entries = commands()
    groups = _groups(entries)
    ordered = [command for group in groups for command in entries
               if command.group == group]
    rows = [(usage_text, summary) for command in ordered
            for usage_text, summary in command.rows]
    width = max(len(usage_text) for usage_text, _summary in rows)
    lines = [
        "本地命令",
        "以 : 开头的输入由本地 CLI 处理，不启动子进程；其他输入交给系统 shell。",
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


def _groups(entries: tuple[LocalCommand, ...]) -> list[str]:
    """帮助里的分组顺序：登记过的分组按序在前，其余按名称排在后面。"""
    present = {command.group for command in entries}
    known = [group for group in GROUP_ORDER if group in present]
    return known + sorted(present - set(GROUP_ORDER))


# 内建命令：只处理终端自身的能力，不碰工具。处理函数在导入期只登记不调用
_BUILTIN = (
    {"name": "help", "group": "通用",
     "rows": ((":help", "列出本地命令"),), "handler": _help},
    {"name": "clear", "group": "通用",
     "rows": ((":clear", "清屏"),), "handler": clear},
    {"name": "status", "group": "状态与策略",
     "rows": ((":status", "显示权限档位、平台、沙箱根目录和免确认规则条数"),),
     "handler": status},
    {"name": "policy", "group": "状态与策略",
     "rows": ((":policy", "显示免确认清单、解释器配置和危险命令类别"),),
     "handler": policy_snapshot},
    {"name": "check", "group": "状态与策略",
     "rows": ((":check <command>", "以当前模型身份预演命令判定"),),
     "handler": check, "paths": True,
     "examples": (":check python script.py",)},
)
