"""命令执行核心：解析、内建命令路由、进程树终止、解码与截断。"""

import locale
import logging
import os
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 终止与收尾的时间预算，方法超时按这两个值推导
KILL_WAIT_S = 5
OUTPUT_JOIN_S = 2

# 子进程不创建控制台窗口
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_READ_CHUNK = 8192
# 截断占位符与错误流分隔行，测试与界面共用
TRUNCATE_NOTICE = "\n<省略 {count} 字符>\n"
STDERR_HEADER = "--- stderr ---"

# Windows 内建命令不在 PATH 里，需要显式交给 cmd 执行；只收只读命令
BUILTINS = frozenset({
    "dir", "type", "echo", "cls", "ver", "where", "findstr",
    "tree", "vol", "path", "attrib", "more", "sort",
})


@dataclass
class Parsed:
    """一条命令的解析结果；routed 是实际执行的 argv。"""

    argv: list[str] = field(default_factory=list)
    builtin: str = ""
    routed: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    """一次子进程执行的结果；code 非空表示执行异常，exit_code 只描述命令自身。"""

    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    truncated: bool = False
    error: str = ""
    code: str = ""


def parse_command(command: str, shell: str = "") -> tuple[Parsed, str]:
    """拆分命令并生成执行用 argv，返回 (解析结果, 错误码)。"""
    text = str(command or "")
    if shell:
        routed = shell_argv(shell, text)
        if routed is None:
            return Parsed(), "bad_command"
        return Parsed(argv=[text], builtin="", routed=routed), ""

    if os.name == "nt":
        if not quotes_paired(text):
            return Parsed(), "bad_command"
        try:
            argv = shlex.split(text, posix=False)
        except ValueError:
            return Parsed(), "bad_command"
        argv = [strip_wrapping_quotes(token) for token in argv]
    else:
        try:
            argv = shlex.split(text, posix=True)
        except ValueError:
            return Parsed(), "bad_command"

    argv = [token for token in argv if token != ""]
    if not argv:
        return Parsed(), "bad_command"

    builtin = ""
    routed = list(argv)
    if os.name == "nt":
        name = argv[0].strip().lower()
        if name in BUILTINS:
            builtin = name
            routed = ["cmd", "/d", "/s", "/c", subprocess.list2cmdline(argv)]
    return Parsed(argv=argv, builtin=builtin, routed=routed), ""


def shell_argv(shell: str, command: str) -> list[str] | None:
    """按平台把整条命令交给系统 shell；未知取值返回 None。"""
    name = str(shell or "").strip().lower()
    if os.name == "nt":
        if name == "pwsh":
            return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
        if name == "cmd":
            return ["cmd", "/d", "/s", "/c", command]
        return None
    if name == "sh":
        return ["sh", "-c", command]
    if name == "bash":
        return ["bash", "-c", command]
    return None


def shell_choices() -> list[str]:
    """当前平台可选的 shell 取值，首项是空串，表示按参数列表解析。"""
    if os.name == "nt":
        return ["", "cmd", "pwsh"]
    return ["", "sh", "bash"]


def quotes_paired(text: str) -> bool:
    """Windows 引号自检：双引号须成对，段内单引号按普通字符处理。"""
    in_double = False
    escaped = False
    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_double = not in_double
    return not in_double


def strip_wrapping_quotes(token: str) -> str:
    """剥掉成对的包裹引号。"""
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        return token[1:-1]
    return token


def display_argv(argv: list[str]) -> str:
    """把 argv 还原成一行展示文本。"""
    items = [str(item) for item in (argv or [])]
    if not items:
        return ""
    if os.name == "nt":
        return subprocess.list2cmdline(items)
    return shlex.join(items)


def run(argv: list[str], cwd: str = "", timeout_s: float = 30,
        env: dict | None = None, max_capture_bytes: int = 131072,
        on_output=None, on_start=None) -> RunResult:
    """执行一条命令，捕获两路输出；超时终止整个进程树。

    on_output(stream, text) 按整行回传，text 一定以换行结尾，界面不必再拼行；
    on_start(pid) 在子进程创建后立即回传，供编排层在退出时终止孤儿进程。
    """
    started = time.monotonic()
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                                   | _NO_WINDOW)
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            list(argv), cwd=cwd or None, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, **kwargs,
        )
    except OSError as exc:
        return RunResult(duration_ms=_elapsed_ms(started),
                         error=f"无法启动命令：{exc}", code="bad_command")
    if on_start is not None:
        try:
            on_start(proc.pid)
        except Exception:
            logger.exception("[TerminalCore] 进程登记失败")

    captured = {"stdout": bytearray(), "stderr": bytearray()}
    truncated_streams = {"stdout": False, "stderr": False}
    lock = threading.Lock()

    def _emit(stream: str, text: str) -> None:
        if not text or on_output is None:
            return
        try:
            on_output(stream, text)
        except Exception:
            logger.exception("[TerminalCore] 输出回传失败")

    def _reader(stream, key: str) -> None:
        pending = bytearray()
        try:
            while True:
                chunk = stream.read(_READ_CHUNK)
                if not chunk:
                    break
                with lock:
                    buf = captured[key]
                    room = max_capture_bytes - len(buf)
                    if room > 0:
                        buf.extend(chunk[:room])
                    if len(chunk) > room:
                        truncated_streams[key] = True
                if on_output is None:
                    continue
                # 按整行回传
                pending.extend(chunk)
                cut = pending.rfind(b"\n")
                if cut >= 0:
                    _emit(key, decode_bytes(bytes(pending[:cut + 1])))
                    del pending[:cut + 1]
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass
            if pending:
                # 末尾没有换行的内容也补成整行，界面层只做 splitlines
                _emit(key, decode_bytes(bytes(pending)) + "\n")

    readers = [
        threading.Thread(target=_reader, args=(proc.stdout, "stdout"), daemon=True),
        threading.Thread(target=_reader, args=(proc.stderr, "stderr"), daemon=True),
    ]
    for reader in readers:
        reader.start()

    code = ""
    error = ""
    try:
        proc.wait(timeout=max(1.0, float(timeout_s)))
    except subprocess.TimeoutExpired:
        ok, message = terminate_tree(proc.pid)
        if ok:
            code, error = "command_timeout", f"命令超过 {timeout_s:g} 秒被终止"
        else:
            code, error = "kill_failed", message
        try:
            proc.wait(KILL_WAIT_S)
        except subprocess.TimeoutExpired:
            logger.warning("[TerminalCore] 进程树终止后主进程仍未退出：pid=%s", proc.pid)

    for reader in readers:
        reader.join(OUTPUT_JOIN_S)
    reader_stuck = any(reader.is_alive() for reader in readers)
    if reader_stuck:
        logger.warning("[TerminalCore] 读取线程未在 %ss 内退出：pid=%s", OUTPUT_JOIN_S, proc.pid)

    exit_code = proc.returncode if proc.returncode is not None else -1
    stderr = decode_capture(bytes(captured["stderr"]), truncated_streams["stderr"])
    if reader_stuck:
        # 标注加在解码之后：写进字节缓冲会挡住截断处的残缺 UTF-8 尾部
        stderr += "\n[读取线程未退出]"
    return RunResult(
        exit_code=exit_code,
        stdout=decode_capture(bytes(captured["stdout"]), truncated_streams["stdout"]),
        stderr=stderr,
        duration_ms=_elapsed_ms(started),
        truncated=any(truncated_streams.values()),
        error=error,
        code=code,
    )


def terminate_tree(pid: int) -> tuple[bool, str]:
    """终止整个进程树，返回 (是否成功, 错误消息)。"""
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, text=True, creationflags=_NO_WINDOW,
            )
        except OSError as exc:
            return False, f"终止进程树失败：{exc}；进程 {pid} 可能仍在运行"
        if completed.returncode != 0:
            return False, f"taskkill 返回 {completed.returncode}；进程 {pid} 可能仍在运行"
        return True, ""
    try:
        os.killpg(os.getpgid(pid), getattr(signal, "SIGKILL", signal.SIGTERM))
    except ProcessLookupError:
        return True, ""
    except OSError as exc:
        return False, f"终止进程树失败：{exc}；进程 {pid} 可能仍在运行"
    return True, ""


def decode_bytes(raw: bytes) -> str:
    """三级解码：UTF-8 → 系统首选编码 → UTF-8 替换非法字节。"""
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode(locale.getpreferredencoding(False))
    except (UnicodeDecodeError, LookupError):
        return raw.decode("utf-8", errors="replace")


def decode_capture(raw: bytes, truncated: bool = False) -> str:
    """解码捕获输出；截断切开 UTF-8 字符时丢掉残缺尾部再解码，避免整段跌到 GBK。"""
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if truncated:
        for cut in range(1, 4):
            if len(raw) <= cut:
                break
            try:
                return raw[:-cut].decode("utf-8")
            except UnicodeDecodeError:
                continue
    return decode_bytes(raw)


def truncate_streams(stdout: str, stderr: str, max_chars: int) -> tuple[str, bool]:
    """按两流合计上限截断，返回 (body, 是否截断)。

    每流配额 max_chars // 2，配额内头占 3/4、尾占 1/4，结余不转移给另一流。
    """
    quota = max(1, int(max_chars) // 2)
    out_text, out_truncated = _truncate_one(stdout, quota)
    err_text, err_truncated = _truncate_one(stderr, quota)
    body = out_text
    if err_text.strip():
        body = f"{out_text}\n{STDERR_HEADER}\n{err_text}" if out_text else err_text
    return body, out_truncated or err_truncated


def _truncate_one(text: str, quota: int) -> tuple[str, bool]:
    text = text or ""
    if len(text) <= quota:
        return text, False
    if quota < 4:
        return text[:quota], True
    head = quota * 3 // 4
    tail = quota - head
    omitted = len(text) - head - tail
    return f"{text[:head]}{TRUNCATE_NOTICE.format(count=omitted)}{text[-tail:]}", True


def count_lines(*texts: str) -> int:
    """非空行数，用于返回值的 headline。"""
    total = 0
    for text in texts:
        total += sum(1 for line in (text or "").splitlines() if line.strip())
    return total


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
