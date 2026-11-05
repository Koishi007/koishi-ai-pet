"""内建命令的纯数据表与用法文本"""

from __future__ import annotations

from .types import EXIT_USAGE, PREFIX, LocalResult

# 帮助里的分组顺序；工具声明的新分组排在后面，按名称排序
GROUP_ORDER = ("通用", "恋恋", "状态与策略", "计时与待办", "检索")

# 处理函数不在这里登记：catalog 按 name 绑定 handler，避免表里出现函数引用
BUILTIN_SPECS = (
    {"name": "help", "group": "通用",
     "rows": ((":help [command]", "列出全部命令，或查看单个命令的用法"),),
     "aliases": ("?",)},
    {"name": "clear", "group": "通用",
     "rows": ((":clear", "清屏"),)},
    {"name": "quit", "group": "通用",
     "rows": ((":quit", "退出应用"),)},
    {"name": "restart", "group": "通用",
     "rows": ((":restart", "重启应用"),)},
    {"name": "status", "group": "恋恋",
     "rows": ((":status [--json]", "显示恋恋的状态、生理、心理与记忆"),),
     "flags": ("--json",)},
    {"name": "policy", "group": "状态与策略",
     "rows": ((":policy", "显示免确认清单、解释器配置和危险命令类别"),)},
    {"name": "check", "group": "状态与策略",
     "rows": ((":check <command>", "以当前模型身份预演命令判定"),),
     "paths": True,
     "examples": (":check python script.py",)},
)


def usage(name: str, detail: str = "", command=None) -> LocalResult:
    """该命令的用法错误提示；command 为空时只查内建表。

    catalog 传入完整的 LocalCommand（含工具登记的命令），builtins 只传名字。
    """
    rows = getattr(command, "rows", None)
    if rows is None:
        spec = next((item for item in BUILTIN_SPECS
                     if item["name"] == name or name in item.get("aliases", ())), None)
        rows = spec["rows"] if spec else ()
    usages = (" | ".join(text for text, _summary in rows)
              if rows else f"{PREFIX}{name}")
    text = f"{detail}，用法：{usages}" if detail else f"用法：{usages}"
    return LocalResult(stderr=text, exit_code=EXIT_USAGE)
