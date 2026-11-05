"""终端本地命令的数据类型与返回码"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

PREFIX = ":"

EXIT_OK = 0
EXIT_TOOL = 1
EXIT_USAGE = 2


@dataclass(frozen=True)
class LocalResult:
    """本地命令结果，字段对应终端回显块；clear 为真表示清屏且不回显。"""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = EXIT_OK
    clear: bool = False


@dataclass(frozen=True)
class LocalContext:
    """本地命令的运行环境：终端当前目录、共用的 TerminalRunner 与应用控制"""

    cwd: str
    runner: object
    control: object | None = None


@dataclass(frozen=True)
class LocalCommand:
    """一条终端命令：内建命令带 handler，工具注册的命令带 tool 与 resolve。"""

    name: str
    group: str
    rows: tuple[tuple[str, str], ...]
    handler: Callable[[LocalContext, str], LocalResult] | None = None
    tool: str = ""
    resolve: Callable[[str], tuple[str, dict]] | None = None
    words: tuple[str, ...] = ()
    paths: bool = False
    examples: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    values: dict[str, tuple[str, ...]] = field(default_factory=dict)
