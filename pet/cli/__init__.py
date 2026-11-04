"""终端本地命令：冒号前缀的分发、帮助与补全数据。

实现分在 `types`（数据类型）、`catalog`（命令目录与帮助排版）、`builtins`
（内建命令）与 `dispatch`（分发）四个模块，这里只导出公共 API。
"""

from __future__ import annotations

from .catalog import (GROUP_ORDER, command_names, commands, path_commands,
                      subcommands)
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
    "command_names",
    "commands",
    "is_local",
    "path_commands",
    "run",
    "subcommands",
]
