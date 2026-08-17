"""崩溃信息收集与持久化"""

from __future__ import annotations

import datetime as _dt
import faulthandler
import json
import logging
import os
import platform
import shutil
import signal
import sys
import threading
import time
import traceback

logger = logging.getLogger(__name__)

_CRASH_DIR_NAME = "crash"
_MARKER_NAME = "startup.state"
_MAX_REPORTS = 10
_SCHEMA = 1

_SENSITIVE_FRAGMENTS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "APPID")
_ENV_MAX_VALUE = 500

_NATIVE_CRASH_PATTERNS = (
    "Windows fatal exception",
    "Fatal Python error",
    "Segmentation fault",
    "Bus error",
    "Aborted",
    "Illegal instruction",
    "Stack overflow",
    "Current thread",
)


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _log_dir() -> str:
    return os.path.join(_project_root(), "logs")


class CrashReporter:
    """崩溃信息收集器"""

    def __init__(self):
        self.crash_dir = os.path.join(_log_dir(), _CRASH_DIR_NAME)
        self.marker_path = os.path.join(_log_dir(), _MARKER_NAME)
        self._enabled = True
        self._installed = False
        self._started_at: float | None = None
        self._old_excepthook = None
        self._old_thread_excepthook = None
        self._faulthandler_file = None
        self._recording = False

    def set_enabled(self, enabled: bool) -> None:
        """按用户配置开关（app 读取 config 后调用）。"""
        self._enabled = bool(enabled)
        if not self._enabled:
            # 关闭时也清理残留标记，避免下次启动误报
            self.clear_marker()

    def install(self) -> "CrashReporter":
        """安装崩溃钩子并登记本次启动。幂等。"""
        if self._installed:
            return self
        self._installed = True
        try:
            os.makedirs(self.crash_dir, exist_ok=True)
        except OSError:
            pass

        self._check_previous_session()
        self._write_marker("starting")
        self._started_at = time.time()

        # Python 未处理异常钩子（主线程 / 子线程）
        self._old_excepthook = sys.excepthook
        sys.excepthook = self._on_exception
        self._old_thread_excepthook = threading.excepthook
        threading.excepthook = self._on_thread_exception

        try:
            dump_path = os.path.join(self.crash_dir, "faulthandler.log")
            self._faulthandler_file = open(dump_path, "a", encoding="utf-8", errors="replace")
            faulthandler.enable(file=self._faulthandler_file, all_threads=True)
            if hasattr(faulthandler, "register"):
                for _name in ("SIGSEGV", "SIGFPE", "SIGABRT", "SIGBUS", "SIGILL"):
                    _sig = getattr(signal, _name, None)
                    if _sig is None:
                        continue
                    try:
                        faulthandler.register(_sig, file=self._faulthandler_file, all_threads=True)
                    except (ValueError, OSError, RuntimeError):
                        pass
        except Exception as e:
            logger.warning("[CrashReport] faulthandler 安装失败: %s", e)
        return self

    def mark_started(self) -> None:
        """初始化完成（进入事件循环前）调用，标记置为 running。"""
        if self._installed:
            self._write_marker("running")

    def clear_marker(self) -> None:
        """正常退出时清除启动标记。幂等。"""
        try:
            if os.path.exists(self.marker_path):
                os.remove(self.marker_path)
        except OSError:
            pass

    def record(self, reason: str, report_type: str = "crash",
               exc_info=None, thread_name: str | None = None,
               uptime: float | None = None,
               native_crash: str | None = None) -> str | None:
        """收集并持久化一份报告，返回报告文件路径（失败返回 None）。"""
        if not self._enabled or self._recording:
            return None
        self._recording = True
        try:
            report = self._collect(reason, report_type, exc_info, thread_name, uptime, native_crash)
            return self._save(report)
        except Exception as e:  # 收集过程出错也不能影响主流程
            try:
                logger.error("[CrashReport] 收集崩溃信息失败: %s", e)
            except Exception:
                pass
            return None
        finally:
            self._recording = False

    def _on_exception(self, exc_type, exc_value, exc_tb) -> None:
        """主线程未处理异常。"""
        try:
            self.record(
                reason=f"未处理异常: {getattr(exc_type, '__name__', exc_type)}: {exc_value}",
                report_type="crash",
                exc_info=(exc_type, exc_value, exc_tb),
            )
            # 已写报告则清除标记，避免下次启动重复上报
            self.clear_marker()
        except Exception:
            pass
        if self._old_excepthook is not None:
            try:
                self._old_excepthook(exc_type, exc_value, exc_tb)
            except Exception:
                pass

    def _on_thread_exception(self, args) -> None:
        """子线程未处理异常"""
        _thread = getattr(args, "thread", None)
        _thread_name = getattr(_thread, "name", None) or getattr(args, "thread_name", None)
        try:
            self.record(
                reason=f"线程未处理异常 ({_thread_name or '?'}): "
                       f"{getattr(args.exc_type, '__name__', args.exc_type)}: {args.exc_value}",
                report_type="crash",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
                thread_name=_thread_name,
            )
        except Exception:
            pass
        if self._old_thread_excepthook is not None:
            try:
                self._old_thread_excepthook(args)
            except Exception:
                pass

    def _read_marker(self) -> dict | None:
        try:
            with open(self.marker_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _write_marker(self, status: str) -> None:
        data = {
            "pid": os.getpid(),
            "status": status,
            "started_at": _dt.datetime.now().isoformat(timespec="seconds"),
        }
        try:
            os.makedirs(_log_dir(), exist_ok=True)
            _atomic_write_json(self.marker_path, data)
        except OSError as e:
            logger.warning("[CrashReport] 写入启动标记失败: %s", e)

    def _check_previous_session(self) -> None:
        """启动时检查上次会话是否异常退出；是则补写 abnormal_exit 报告。"""
        marker = self._read_marker()
        if not marker:
            return
        if _pid_alive(marker.get("pid")):
            logger.info("[CrashReport] 检测到其他实例仍在运行，跳过上次会话检查")
            return
        _uptime = _parse_uptime(marker.get("started_at"))
        _extra = f"，已运行约 {_uptime:g} 秒" if _uptime is not None else ""
        _native = self._consume_faulthandler_log() or None
        self.record(
            reason=f"上次会话未正常退出（pid={marker.get('pid')}, "
                   f"status={marker.get('status')!r}, 启动于 {marker.get('started_at')}{_extra}）",
            report_type="abnormal_exit",
            uptime=_uptime,
            native_crash=_native,
        )

    def _consume_faulthandler_log(self) -> str:
        path = os.path.join(self.crash_dir, "faulthandler.log")
        try:
            if not os.path.exists(path) or os.path.getsize(path) == 0:
                return ""
            # 该钩子在本流程（install 开头）尚未打开，此时读写是安全的
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read().strip()
            if content:
                with open(path, "w", encoding="utf-8") as f:
                    f.truncate(0)
            return content
        except OSError as e:
            logger.warning("[CrashReport] 读取 faulthandler.log 失败: %s", e)
            return ""

    def _collect(self, reason: str, report_type: str, exc_info,
                 thread_name: str | None, uptime: float | None,
                 native_crash: str | None = None) -> dict:
        report = {
            "schema": _SCHEMA,
            "type": report_type,
            "reason": reason,
            "timestamp": _dt.datetime.now().isoformat(timespec="seconds"),
            "app_version": _app_version(),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "frozen": bool(getattr(sys, "frozen", False)),
            "pid": os.getpid(),
            "executable": sys.executable,
            "command_line": list(sys.argv),
            "cwd": _safe_getcwd(),
            "uptime_seconds": round(
                uptime if uptime is not None
                else (time.time() - self._started_at) if self._started_at else 0.0,
                1,
            ),
            "thread": thread_name or threading.current_thread().name,
        }

        if exc_info and exc_info[0] is not None:
            _et, _ev, _tb = exc_info
            report["exception"] = {
                "type": getattr(_et, "__name__", str(_et)),
                "message": str(_ev or ""),
                "traceback": "".join(traceback.format_exception(_et, _ev, _tb)),
            }

        _threads = _collect_thread_stacks()
        if _threads:
            report["threads"] = _threads

        if native_crash:
            report["native_crash"] = {
                "detected": _detect_native_crash(native_crash),
                "faulthandler_log": native_crash,
            }
        report["config"] = _safe_config()
        report["environment"] = _safe_environment()
        try:
            report["disk_free_bytes"] = shutil.disk_usage(_log_dir()).free
        except OSError:
            pass
        return report

    def _save(self, report: dict) -> str | None:
        try:
            os.makedirs(self.crash_dir, exist_ok=True)
            ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            base = os.path.join(self.crash_dir, f"crash_{ts}_{report['type']}")
            _atomic_write_json(base + ".json", report)
            _atomic_write_text(base + ".txt", _format_text(report))
            self._prune()
            logger.error("[CrashReport] 崩溃报告已保存: %s.json", base)
            return base + ".json"
        except Exception as e:
            try:
                logger.error("[CrashReport] 保存崩溃报告失败: %s", e)
            except Exception:
                pass
            return None

    def _prune(self) -> None:
        """只保留最近 _MAX_REPORTS 份报告"""
        try:
            json_files = sorted(
                f for f in os.listdir(self.crash_dir)
                if f.startswith("crash_") and f.endswith(".json")
            )
            for old in json_files[:-_MAX_REPORTS]:
                base = os.path.splitext(old)[0]
                for suffix in (".json", ".txt"):
                    p = os.path.join(self.crash_dir, base + suffix)
                    try:
                        if os.path.exists(p):
                            os.remove(p)
                    except OSError:
                        pass
        except OSError:
            pass

_guard: CrashReporter | None = None


def get_guard() -> CrashReporter:
    global _guard
    if _guard is None:
        _guard = CrashReporter()
    return _guard


def install() -> CrashReporter:
    """安装全局崩溃钩子（幂等）"""
    return get_guard().install()


def mark_started() -> None:
    get_guard().mark_started()


def clear_marker() -> None:
    get_guard().clear_marker()

def _pid_alive(pid) -> bool:
    """尽力判断 pid 是否仍存活（psutil 优先，跨平台）。"""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        import psutil  # 项目已有依赖
        return psutil.pid_exists(pid)
    except Exception:
        pass
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _parse_uptime(started_at) -> float | None:
    """解析启动标记中的启动时间，返回距现在的秒数。"""
    if not isinstance(started_at, str):
        return None
    try:
        start = _dt.datetime.fromisoformat(started_at)
        return max(0.0, (_dt.datetime.now() - start).total_seconds())
    except Exception:
        return None


def _collect_thread_stacks() -> dict:
    """收集所有存活线程的当前调用栈"""
    result = {}
    try:
        frames = sys._current_frames()
    except Exception:
        return result
    for t in threading.enumerate():
        frame = frames.get(t.ident)
        if frame is None:
            continue
        try:
            stack = "".join(traceback.format_stack(frame))
        except Exception:
            continue
        result[t.name] = stack.rstrip()
    return result


def _safe_getcwd() -> str:
    try:
        return os.getcwd()
    except OSError:
        return ""


def _app_version() -> str:
    try:
        import tomllib
        with open(os.path.join(_project_root(), "pyproject.toml"), "rb") as f:
            return str(tomllib.load(f).get("project", {}).get("version", ""))
    except Exception:
        pass
    try:
        from importlib.metadata import version as _pv
        return _pv("koishi-ai-pet")
    except Exception:
        return ""


def _is_sensitive(key: str) -> bool:
    k = key.upper()
    return any(s in k for s in _SENSITIVE_FRAGMENTS)


def _detect_native_crash(content: str) -> bool:
    if not content:
        return False
    lower = content.lower()
    return any(p.lower() in lower for p in _NATIVE_CRASH_PATTERNS)


def _settings_path() -> str | None:
    try:
        if sys.platform == "win32":
            base = os.environ.get("APPDATA", os.path.expanduser("~/AppData/Roaming"))
        elif sys.platform == "darwin":
            base = os.path.expanduser("~/Library/Application Support")
        else:
            base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        return os.path.join(base, "KoishiAI", "settings.json")
    except Exception:
        return None


def _safe_config() -> dict:
    path = _settings_path()
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if not _is_sensitive(k)}
    except Exception:
        return {}


def _safe_environment() -> dict:
    result = {}
    for k, v in os.environ.items():
        if _is_sensitive(k):
            continue
        if isinstance(v, str) and len(v) > _ENV_MAX_VALUE:
            v = v[:_ENV_MAX_VALUE] + f"...(truncated, {len(v)} chars)"
        result[k] = v
    return result


def _atomic_write_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _atomic_write_text(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _format_text(report: dict) -> str:
    """报告格式化"""
    lines = [
        f"类型     : {report.get('type')}",
        f"原因     : {report.get('reason', '')}",
        f"时间     : {report.get('timestamp')}",
        f"版本     : {report.get('app_version') or 'unknown'}",
        f"Python   : {report.get('python_version')}",
        f"平台     : {report.get('platform')}",
        f"PID      : {report.get('pid')}",
        f"运行时长 : {report.get('uptime_seconds')}s",
        f"目录     : {report.get('cwd')}",
        f"磁盘剩余 : {report.get('disk_free_bytes')} bytes",
        "",
    ]
    exc = report.get("exception")
    if exc:
        lines += [
            "=" * 60,
            f"异常类型: {exc.get('type')}",
            f"异常信息: {exc.get('message')}",
            "Traceback:",
            exc.get("traceback", "").rstrip(),
            "",
        ]
    threads = report.get("threads")
    if threads:
        lines += ["=" * 60, "各线程当前调用栈:"]
        for name, stack in threads.items():
            lines += [f"--- 线程: {name} ---", stack, ""]
    native = report.get("native_crash")
    if native:
        lines += [
            "=" * 60,
            f"是否原生崩溃: {'是' if native.get('detected') else '疑似'}",
            native.get("faulthandler_log", ""),
            "",
        ]
    lines += [
        "=" * 60,
        "配置摘要:",
        json.dumps(report.get("config", {}), ensure_ascii=False, indent=2),
    ]
    return "\n".join(lines)
