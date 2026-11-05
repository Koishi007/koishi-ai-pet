"""终端本地命令：冒号前缀的分发、帮助与补全数据。

实现分在 `types`（数据类型）、`format`（输出排版）、`catalog`（命令目录与帮助排版）、
`builtins`（内建命令）与 `dispatch`（分发）五个模块，这里只导出公共 API。
"""

from __future__ import annotations

from .catalog import (GROUP_ORDER, aliases, command_names, commands, flags,
                      path_commands, subcommands, values)
from .dispatch import is_local, run
from .types import (EXIT_OK, EXIT_TOOL, EXIT_USAGE, PREFIX, LocalCommand,
                    LocalContext, LocalResult)

__all__ = [
    "GROUP_ORDER",
    "PREFIX",
    "EXIT_OK",
    "EXIT_TOOL",
    "EXIT_USAGE",
    "LocalCommand",
    "LocalContext",
    "LocalResult",
    "aliases",
    "command_names",
    "commands",
    "flags",
    "is_local",
    "path_commands",
    "run",
    "subcommands",
    "values",
]
