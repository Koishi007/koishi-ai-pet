"""终端命令策略：档位、免确认命令、危险模式、程序解析与沙箱目录。"""

import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass

from pet.config import config
from pet.terminal.events import Decision

logger = logging.getLogger(__name__)

# 解释器名单：auto 档把免确认降级为询问
INTERPRETERS = (
    "python", "python3", "py", "node", "npm", "npx",
    "pwsh", "powershell", "cmd", "bash", "sh", "wsl",
)

# 子进程环境固定透传的键
ENV_KEYS = (
    "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
    "HOME", "USERPROFILE", "LANG", "LC_ALL", "PYTHONIOENCODING",
)

_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")

# 可执行文件扩展名：免确认命令里的程序名去扩展名时只认这些
_EXEC_EXTS = (".exe", ".com", ".bat", ".cmd", ".ps1", ".sh")

# 内置危险模式：类别名 + 正则，空配置时整体生效
DEFAULT_DENY_PATTERNS = (
    ("删除", r"rm\s+-[rf]{1,2}\s+/|del\s+/[fsq]|format\s+[a-z]:|diskpart"),
    ("磁盘", r"vssadmin\s+delete|cipher\s+/w|mkfs|>\s*/dev/sd"),
    ("关机", r"shutdown|restart-computer|stop-computer"),
    ("账户", r"net\s+user"),
    ("注册表", r"reg\s+delete|bcdedit"),
    ("下载即执行", r"invoke-expression|\biex\b|-encodedcommand|"
                   r"(curl|wget|iwr|irm)[^|]*\|\s*(sh|bash|iex|powershell)|"
                   r"chmod\s+-R\s+777\s+/"),
)

CUSTOM_CATEGORY = "自定义"


@dataclass(frozen=True)
class Verdict:
    """一条命令的策略结论。decision 取 allow / ask / deny。

    abs_cwd 是本次判定解析出的绝对工作目录，交给 Runner 直接执行，
    避免 Runner 再算一遍 sandbox_cwd 产生两份可能不一致的真相。
    """

    decision: Decision
    reason: str = ""
    matched: tuple[str, ...] = ()
    resolved: str = ""
    untrusted: bool = False
    abs_cwd: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.decision, Decision):
            object.__setattr__(self, "decision", Decision(self.decision))


def desktop_dir() -> str:
    """当前用户的桌面目录。"""
    if sys.platform == "win32":
        import ctypes

        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.shell32.SHGetFolderPathW(0, 0, 0, 0, buf) == 0 and buf.value:
            return buf.value
    return os.path.expanduser("~/Desktop")


def sandbox_root() -> str:
    """终端初始目录与模型根的默认值；配置留空时取桌面。"""
    configured = (config.TERMINAL_SANDBOX_ROOT or "").strip()
    if not configured:
        return os.path.abspath(desktop_dir())
    return os.path.abspath(os.path.expanduser(configured))


def effective_root(shared: str = "") -> str:
    """模型命令实际使用的根：终端当前目录优先，缺失或无效时回落到配置根。

    用户在终端里 cd 到的目录会成为模型的工作目录根，这是用户主动交出的范围；
    配置里的根只决定终端初始目录与没有窗口时的默认值。这里只约束初始 cwd 与
    相对路径解析，不是 OS 级隔离：子进程仍持有当前用户的全部权限。
    """
    root = sandbox_root()
    candidate = (shared or "").strip()
    if not candidate:
        return root
    candidate = os.path.realpath(os.path.expanduser(candidate))
    return candidate if os.path.isdir(candidate) else root


def parse_allow_entry(text) -> tuple[str, list[str]]:
    """免确认命令条目归一化：返回 (程序名, 参数模式)；非法条目返回 ("", [])。

    只写程序名的条目匹配不到任何调用；`*` 只在末位表示剩余参数任意。
    """
    tokens = str(text or "").split()
    if not tokens:
        return "", []
    program = program_key(tokens[0])
    if not program or tokens[0] == "*":
        logger.warning("[TerminalPolicy] 忽略非法免确认命令条目：%r", text)
        return "", []
    return program, tokens[1:]


def program_key(token: str) -> str:
    """程序名归一化：basename、去可执行扩展名、小写。"""
    name = os.path.basename(str(token or "").strip().strip('"'))
    stem, ext = os.path.splitext(name)
    if ext.lower() in _EXEC_EXTS:
        name = stem
    return name.lower()


def extra_allow() -> list[tuple[str, list[str]]]:
    """配置里填写的免确认命令。"""
    configured = [str(item) for item in (config.TERMINAL_ALLOW or []) if str(item).strip()]
    return [parse_allow_entry(item) for item in configured]


def allowed_entries() -> list[tuple[str, list[str]]]:
    """生效的免确认清单：只有配置里填写的条目，重复项合并。"""
    entries: list[tuple[str, list[str]]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for program, pattern in extra_allow():
        key = (program, tuple(pattern))
        if not program or key in seen:
            continue
        seen.add(key)
        entries.append((program, pattern))
    return entries


def entry_matches(entry: tuple[str, list[str]], program: str,
                  args: list[str]) -> bool:
    """参数模式与 argv[1:] 做前缀匹配，`*` 只在末位表示剩余参数任意。"""
    pattern_program, pattern = entry
    if pattern_program != program:
        return False
    if not pattern:
        return False
    fixed = list(pattern)
    if fixed[-1] == "*":
        fixed = fixed[:-1]
        return args[:len(fixed)] == fixed
    return args[:len(fixed)] == fixed


def allows(program: str, args: list[str]) -> bool:
    """程序与参数是否命中免确认清单。"""
    return any(entry_matches(entry, program, args) for entry in allowed_entries())


def deny_patterns() -> list[tuple[str, str]]:
    """生效的危险模式，每项 (类别名, 正则)；配置非空时整体替换内置清单。"""
    configured = [str(item) for item in (config.TERMINAL_DENY_PATTERNS or []) if str(item).strip()]
    if not configured:
        return list(DEFAULT_DENY_PATTERNS)
    return [(CUSTOM_CATEGORY, pattern) for pattern in configured]


def deny_categories() -> list[str]:
    """危险模式覆盖的类别名，不返回正则原文。"""
    categories: list[str] = []
    for category, _pattern in deny_patterns():
        if category not in categories:
            categories.append(category)
    return categories


def match_deny_patterns(command: str) -> list[str]:
    """命令命中的危险类别，按清单顺序去重。"""
    text = command or ""
    if not text:
        return []
    matched: list[str] = []
    for category, pattern in deny_patterns():
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
        except re.error:
            logger.warning("[TerminalPolicy] 忽略无法编译的危险模式：%r", pattern)
            continue
        if compiled.search(text) and category not in matched:
            matched.append(category)
    return matched


def resolve_program(argv0: str, cwd: str, sandbox_root_path: str = "") -> tuple[str, str]:
    """解析可执行的绝对路径，返回 (路径, 错误码)。

    先查目标目录的同名文件，再查系统 PATH；两处都没有返回 bad_command。
    """
    if not argv0:
        return "", "bad_command"
    if cwd:
        local = probe_in_dir(argv0, cwd)
        if local:
            return os.path.abspath(local), ""
    found = shutil.which(argv0)
    if found:
        return os.path.abspath(found), ""
    return "", "bad_command"


def probe_in_dir(argv0: str, cwd: str) -> str:
    """按 PATHEXT 在 cwd 下探测同名文件；未命中返回空串。"""
    if not argv0 or not cwd:
        return ""
    if os.sep in argv0 or (os.altsep and os.altsep in argv0):
        candidate = argv0 if os.path.isabs(argv0) else os.path.join(cwd, argv0)
        return candidate if os.path.isfile(candidate) else ""
    exact = os.path.join(cwd, argv0)
    if os.path.isfile(exact):
        return exact
    if os.name != "nt":
        return ""
    extensions = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep)
    for extension in extensions:
        candidate = os.path.join(cwd, argv0 + extension.strip().lower())
        if os.path.isfile(candidate):
            return candidate
    return ""


def _norm_path(path: str, platform: str) -> str:
    """realpath 后按平台决定是否折平大小写。"""
    resolved = os.path.realpath(path)
    if platform in ("win32", "darwin", "cygwin"):
        return resolved.lower()
    return resolved


def is_writable_path(path: str, roots, platform: str = "") -> bool:
    """path 是否落在任一目录树内。

    platform 取 sys.platform 的取值，决定大小写策略；比较带目录边界。
    """
    if not path:
        return False
    platform = platform or sys.platform
    target = _norm_path(path, platform)
    for root in roots:
        if not root:
            continue
        normalized = _norm_path(str(root), platform)
        try:
            if os.path.commonpath([target, normalized]) == normalized:
                return True
        except ValueError:
            continue
    return False


def sandbox_cwd(cwd: str, sandbox_root_path: str, source: str) -> tuple[str, str]:
    """把 cwd 解析成绝对路径；模型来源越界返回 cwd_denied。"""
    if source == "user":
        target = os.path.expanduser(cwd) if cwd else os.path.expanduser("~")
        return os.path.abspath(target), ""
    root = os.path.abspath(sandbox_root_path) if sandbox_root_path else sandbox_root()
    target = os.path.expanduser(cwd) if cwd else root
    if not os.path.isabs(target):
        target = os.path.join(root, target)
    target = os.path.realpath(target)
    if is_writable_path(target, [root], sys.platform):
        return target, ""
    return target, "cwd_denied"


def build_env() -> dict[str, str]:
    """子进程环境：固定键与 TERMINAL_ENV_ALLOW，密钥类键即使列出也不透传。"""
    allowed = {str(key).strip() for key in (config.TERMINAL_ENV_ALLOW or []) if str(key).strip()}
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key not in ENV_KEYS and key not in allowed:
            continue
        if is_secret_key(key):
            continue
        env[key] = value
    return env


def is_secret_key(key: str) -> bool:
    """环境变量名是否带密钥语义。"""
    upper = str(key or "").upper()
    return any(marker in upper for marker in _SECRET_MARKERS)


def clamp_timeout(value, default_s: float) -> float:
    """命令级超时换算：0 或负数取默认值，正数夹到 [1, 上限]。"""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return float(default_s)
    if seconds <= 0:
        return float(default_s)
    return max(1.0, min(seconds, float(config.TERMINAL_MAX_COMMAND_TIMEOUT_S)))


def check(source: str, argv: list[str], builtin: str, command: str,
          cwd: str = "", shell: str = "", root: str = "") -> Verdict:
    """按档位判定一条命令；source="user" 首步放行。

    root 是终端窗口的当前目录，经 effective_root 解析成模型工作目录根；返回的
    Verdict.abs_cwd 是本次判定唯一的工作目录，Runner 不再重复解析。
    shell 非空表示整条命令交给系统 shell，此时跳过程序解析，auto 档一律询问。
    """
    mode = (config.TERMINAL_MODE or "ask").strip().lower()
    shell_mode = bool(shell)
    program = program_key(argv[0]) if argv else ""
    root = effective_root(root)
    abs_cwd, cwd_err = sandbox_cwd(cwd, root, source)
    if source == "user":
        return Verdict(Decision.ALLOW, resolved=argv[0] if argv else "",
                       abs_cwd=abs_cwd)
    if mode == "deny":
        return Verdict(Decision.DENY, "terminal_disabled", abs_cwd=abs_cwd)

    resolved = argv[0] if argv else ""
    untrusted = False
    if not builtin and not shell_mode:
        resolved, err = resolve_program(argv[0] if argv else "", abs_cwd)
        if err:
            return Verdict(Decision.DENY, err, (), resolved,
                           abs_cwd=abs_cwd)
        if is_writable_path(resolved, [root, abs_cwd], sys.platform):
            untrusted = True
            if mode == "auto":
                return Verdict(Decision.DENY, "untrusted_program", (),
                               resolved, True, abs_cwd)
    if cwd_err:
        return Verdict(Decision.DENY, cwd_err, (), resolved, untrusted,
                       abs_cwd=abs_cwd)

    matched = tuple(match_deny_patterns(command))
    if mode == "auto":
        # 危险模式、解释器、整条 shell 命令一律转询问
        if matched or shell_mode:
            return Verdict(Decision.ASK, "", matched, resolved, untrusted, abs_cwd)
        if not builtin and program in INTERPRETERS:
            return Verdict(Decision.ASK, "", matched, resolved, untrusted, abs_cwd)
        if allows(program, list(argv[1:])):
            return Verdict(Decision.ALLOW, "", matched, resolved, untrusted, abs_cwd)
    return Verdict(Decision.ASK, "", matched, resolved, untrusted, abs_cwd)
