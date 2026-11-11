"""终端输入补全的纯逻辑：候选来源、公共前缀、引号与大小写策略。"""

import os
from dataclasses import dataclass, field
from typing import Callable

# 参数类型按首词决定
COMMAND_DIRS = frozenset({"cd", "chdir", "pushd"})
COMMAND_FILES = frozenset({"type", "cat", "more", "less", "notepad"})

# 首词里优先于可执行文件的 Shell 保留字与内建命令
SHELL_WORDS_NT = (
    "assoc", "break", "call", "cd", "chdir", "cls", "color", "copy", "date",
    "del", "dir", "echo", "erase", "exit", "for", "ftype", "goto", "if", "md",
    "mkdir", "mklink", "move", "path", "pause", "popd", "prompt", "pushd",
    "rd", "ren", "rename", "rmdir", "set", "setlocal", "shift", "start",
    "time", "title", "type", "ver", "verify", "vol",
)
SHELL_WORDS_POSIX = (
    "alias", "bg", "break", "cd", "command", "continue", "declare", "echo",
    "eval", "exec", "exit", "export", "false", "fg", "for", "function",
    "getopts", "hash", "history", "if", "in", "jobs", "kill", "let", "local",
    "printf", "pwd", "read", "readonly", "return", "set", "shift", "source",
    "test", "then", "time", "times", "trap", "true", "type", "ulimit",
    "umask", "unalias", "unset", "wait", "while",
)

# 本地命令前缀，与 pet/cli 的 PREFIX 同值
LOCAL_PREFIX = ":"

MAX_CANDIDATES = 500
_SEPARATORS = " \t"


@dataclass(frozen=True)
class Completion:
    """一次补全的结果：替换起点、候选与公共前缀。"""

    start: int
    candidates: tuple[str, ...] = ()
    common_prefix: str = ""


@dataclass
class CompletionContext:
    """补全所需的上下文；list_dir 返回 [(名字, 是否目录)]。

    local_commands 是冒号命令名（不带前缀），local_subcommands 是命令名到子命令的映射，
    local_path_commands 表示该命令的参数按系统程序名与路径补全；local_aliases 是别名到
    命令名的映射，local_flags 是命令名到选项名，local_values 是命令名到「子命令 → 位置
    参数候选」；shell 为空表示不从变量前缀补环境变量名。
    """

    cwd: str = ""
    case_sensitive: bool = False
    shell: str = ""
    path_commands: tuple[str, ...] = ()
    list_dir: Callable[[str], list[tuple[str, bool]]] | None = None
    local_commands: tuple[str, ...] = ()
    local_subcommands: dict[str, tuple[str, ...]] = field(default_factory=dict)
    local_path_commands: tuple[str, ...] = ()
    local_aliases: dict[str, str] = field(default_factory=dict)
    local_flags: dict[str, tuple[str, ...]] = field(default_factory=dict)
    local_values: dict[str, dict[str, tuple[str, ...]]] = field(default_factory=dict)


def complete(buffer: str, cursor: int, ctx: CompletionContext) -> Completion:
    """按光标位置计算候选。"""
    text = buffer or ""
    cursor = max(0, min(int(cursor), len(text)))
    start = token_start(text, cursor)
    token = text[start:cursor]
    quoted = token.startswith('"')
    inner = token[1:] if quoted else token
    if quoted and len(inner) >= 1 and inner.endswith('"'):
        inner = inner[:-1]
    replace_from = start + 1 if quoted else start

    first = first_word(text, start)
    args = text[:start].split()
    if not args and inner.startswith(LOCAL_PREFIX):
        candidates = local_command_candidates(inner, ctx)
    elif first.startswith(LOCAL_PREFIX):
        candidates = local_argument_candidates(inner, first, args, ctx)
    elif (environment := env_candidates(inner, ctx)) is not None:
        candidates = environment
    elif not args and not has_path_sep(inner):
        candidates = command_candidates(inner, ctx)
    else:
        candidates = argument_candidates(inner, first, ctx)
    candidates = tuple(candidates[:MAX_CANDIDATES])
    return Completion(replace_from, candidates, common_prefix(candidates))


def token_start(text: str, cursor: int) -> int:
    """光标所在 token 的起点。"""
    index = cursor
    while index > 0 and text[index - 1] not in _SEPARATORS:
        index -= 1
    return index


def first_word(text: str, limit: int) -> str:
    """光标之前的第一个词，用于判断参数类型。"""
    parts = text[:limit].split()
    return parts[0] if parts else ""


def has_path_sep(token: str) -> bool:
    return "/" in token or "\\" in token


def local_command_candidates(prefix: str, ctx: CompletionContext) -> list[str]:
    """冒号命令名候选：带冒号返回，命令在前别名在后，大小写不敏感。"""
    head = prefix.lower()
    names = list(ctx.local_commands) + list(ctx.local_aliases)
    return [LOCAL_PREFIX + name for name in names
            if (LOCAL_PREFIX + name).lower().startswith(head)]


def local_argument_candidates(token: str, first: str, args: list[str],
                              ctx: CompletionContext) -> list[str]:
    """冒号命令的参数候选：选项、子命令、子命令的位置参数，最后是路径类命令。"""
    name = first[len(LOCAL_PREFIX):].lower()
    name = ctx.local_aliases.get(name, name)
    head = token.lower()
    if token.startswith("-"):
        return [flag for flag in ctx.local_flags.get(name, ())
                if flag.lower().startswith(head)]
    if len(args) <= 1:
        if name == "help":
            return [command for command in ctx.local_commands
                    if command.lower().startswith(head)]
        words = ctx.local_subcommands.get(name, ())
        if words:
            return [word for word in words if word.lower().startswith(head)]
    elif len(args) == 2:
        sub = args[1].lower()
        values = ctx.local_values.get(name, {}).get(sub, ())
        if values:
            return [value for value in values if value.lower().startswith(head)]
    if name not in ctx.local_path_commands:
        return []
    if has_path_sep(token):
        return argument_candidates(token, "", ctx)
    return command_candidates(token, ctx)


def env_candidates(token: str, ctx: CompletionContext) -> list[str] | None:
    """环境变量名候选：按当前 shell 的写法返回；不是变量前缀时返回 None。"""
    shell = (ctx.shell or "").strip().lower()
    if shell in ("bash", "sh") and token.startswith("$"):
        return _env_names(token[1:], "$", "", ctx)
    if shell == "pwsh" and token.lower().startswith("$env:"):
        return _env_names(token[5:], "$env:", "", ctx)
    if shell == "cmd" and token.startswith("%"):
        return _env_names(token[1:], "%", "%", ctx)
    return None


def _env_names(prefix: str, head: str, tail: str, ctx: CompletionContext) -> list[str]:
    """把环境变量名套上该 shell 的前后缀。"""
    return [head + name + tail for name in sorted(os.environ)
            if matches(name, prefix, ctx)]


def shell_words() -> tuple[str, ...]:
    """按平台返回首词优先的 Shell 保留字与内建命令。"""
    return SHELL_WORDS_NT if os.name == "nt" else SHELL_WORDS_POSIX


def command_candidates(prefix: str, ctx: CompletionContext) -> list[str]:
    """首词候选：Shell 保留字、PATH 可执行名、当前目录条目，按此优先级去重。"""
    results = [name for name in shell_words() if matches(name, prefix, ctx)]
    results.extend(name for name in ctx.path_commands if matches(name, prefix, ctx))
    for name, is_dir in sorted(entries(ctx.cwd, ctx),
                               key=lambda item: item[0] if ctx.case_sensitive
                               else item[0].lower()):
        if not visible_name(name, prefix) or not matches(name, prefix, ctx):
            continue
        results.append(name + os.sep if is_dir else name)
    return _dedupe(results, ctx)


def visible_name(name: str, prefix: str) -> bool:
    """点开头的条目只在已输入点时列出。"""
    return not name.startswith(".") or prefix.startswith(".")


def argument_candidates(token: str, first: str, ctx: CompletionContext) -> list[str]:
    """其余 token 的候选：按首词过滤目录或文件。"""
    if token == "..":
        return [".." + os.sep]
    head, leaf = split_token(token)
    directory = base_dir(token, ctx)
    kind = "dir" if first in COMMAND_DIRS else "file" if first in COMMAND_FILES else "any"
    results: list[str] = []
    for name, is_dir in entries(directory, ctx):
        if kind == "dir" and not is_dir:
            continue
        if kind == "file" and is_dir:
            continue
        if not visible_name(name, leaf) or not matches(name, leaf, ctx):
            continue
        results.append(head + name + (os.sep if is_dir else ""))
    return sort_names(_dedupe(results, ctx), ctx)


def split_token(token: str) -> tuple[str, str]:
    """拆成 (用户已输入的目录前缀, 待补全的文件名前缀)。"""
    index = max(token.rfind("/"), token.rfind("\\"))
    if index < 0:
        return "", token
    return token[:index + 1], token[index + 1:]


def base_dir(token: str, ctx: CompletionContext) -> str:
    """候选目录：~ 取主目录，.. 取上一级，绝对路径从磁盘根，其余基于状态栏目录。"""
    head, _leaf = split_token(token)
    if not head:
        return ctx.cwd or os.getcwd()
    expanded = head
    if expanded.startswith("~"):
        expanded = os.path.expanduser("~") + expanded[1:]
    elif expanded.startswith(".."):
        expanded = os.path.join(ctx.cwd, "..", expanded[2:].lstrip("/\\"))
    if not os.path.isabs(expanded):
        expanded = os.path.join(ctx.cwd, expanded)
    return os.path.normpath(expanded)


def entries(directory: str, ctx: CompletionContext) -> list[tuple[str, bool]]:
    """读取目录条目；读不到时返回空列表。"""
    reader = ctx.list_dir or default_list_dir
    try:
        return reader(directory)
    except OSError:
        return []


def default_list_dir(path: str) -> list[tuple[str, bool]]:
    """真实目录读取；不可访问时返回空列表。"""
    result: list[tuple[str, bool]] = []
    try:
        with os.scandir(path) as items:
            for item in items:
                try:
                    result.append((item.name, item.is_dir()))
                except OSError:
                    result.append((item.name, False))
    except OSError:
        return []
    return result


def matches(name: str, prefix: str, ctx: CompletionContext) -> bool:
    """前缀匹配：Windows 不敏感，POSIX 敏感。"""
    if not prefix:
        return True
    if ctx.case_sensitive:
        return name.startswith(prefix)
    return name.lower().startswith(prefix.lower())


def common_prefix(candidates) -> str:
    """候选的公共前缀。"""
    items = list(candidates)
    if not items:
        return ""
    return os.path.commonprefix(items)


def quote_candidate(text: str, quoted: bool = False) -> str:
    """含空格的补全结果加双引号；目录分隔符留在引号外。"""
    if quoted:
        return text
    suffix = os.sep if text.endswith(os.sep) else ""
    body = text[:len(text) - len(suffix)] if suffix else text
    if not any(char in body for char in _SEPARATORS):
        return text
    return f'"{body}"{suffix}'


def _dedupe(results: list[str], ctx: CompletionContext) -> list[str]:
    """按出现顺序去重，保留候选来源的优先级。"""
    unique = []
    seen = set()
    for item in results:
        key = item if ctx.case_sensitive else item.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def sort_names(names: list[str], ctx: CompletionContext) -> list[str]:
    """按平台大小写策略排序。"""
    return sorted(names, key=(None if ctx.case_sensitive else str.lower))
