"""TerminalRunner：模型命令与用户终端共用的执行编排"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import time
import uuid
from dataclasses import dataclass

from pet.config import config
from pet.terminal import core, policy
from pet.terminal.confirm import ConfirmRequest
from pet.terminal.events import CommandSource, Phase, TerminalEvent

logger = logging.getLogger(__name__)

# 程序安装情况的探测缓存时长
_WHICH_TTL_S = 60
TRUNCATED_SUFFIX = "，输出已截断"

_HINTS = {
    "terminal_disabled": "不要重试，工具已被用户关闭",
    "denied_by_user": "不要重复提交同一条命令；需要时先说明想做什么，等用户同意",
    "bad_command": "重新给出一条完整命令；换目录用 cwd 参数而不是 cd；不确定本机有哪些程序时先调用 terminal__list",
    "untrusted_program": "用 terminal__list 查看可信程序，不要换路径绕过",
    "cwd_denied": "换用工作目录根及其子目录内的路径",
    "command_timeout": "命令已终止，不要原样重试",
    "busy": "前面的命令还在跑，不要立即重试",
    "kill_failed": "进程可能仍在运行，不要重试",
    "shell_mode_disabled": "去掉 shell 参数",
    "internal_error": "不要重试，告知用户命令未执行",
}

_MESSAGES = {
    "terminal_disabled": "终端工具已关闭",
    "denied_by_user": "用户拒绝了这条命令",
    "bad_command": "命令无法解析或程序不存在",
    "untrusted_program": "程序来自可写目录，不可信",
    "cwd_denied": "工作目录超出允许范围",
    "command_timeout": "命令超时被终止",
    "busy": "前面的命令还在执行",
    "kill_failed": "终止进程树失败",
    "shell_mode_disabled": "未开启整条命令交给 shell",
    "internal_error": "命令执行出现未预期错误",
}


def failure(code: str, message: str = "") -> dict:
    """业务失败的返回结构，由 executor 转入失败通道。"""
    return {
        "error": message or _MESSAGES.get(code, code),
        "code": code,
        "hint": _HINTS.get(code, ""),
    }


@dataclass(frozen=True)
class Prepared:
    """prepare() 的唯一产物：判定、执行参数与展示信息在这里定稿。"""

    run_id: str
    source: CommandSource
    command: str
    argv: tuple[str, ...]
    cwd: str
    timeout_s: float
    verdict: policy.Verdict


class TerminalRunner:

    def __init__(self):
        self.lock = threading.Lock()
        self._proc_lock = threading.Lock()
        self._active_pid: int | None = None
        self._installed_cache: dict[str, tuple[float, bool]] = {}
        self._terminal_sink = None
        self._confirm = None
        self._cwd_source = None

    @property
    def method_timeout(self) -> float:
        """工具方法的超时预算，覆盖确认、排队、执行、终止与收尾。"""
        return float(
            int(config.TERMINAL_CONFIRM_TIMEOUT_S)
            + int(config.TERMINAL_MAX_COMMAND_TIMEOUT_S)
            + int(config.TERMINAL_QUEUE_WAIT_S)
            + core.KILL_WAIT_S + core.OUTPUT_JOIN_S + 3
        )

    def set_terminal_sink(self, sink) -> None:
        """注册终端窗口的回显回调；传 None 注销。"""
        self._terminal_sink = sink

    def set_confirm(self, confirm_fn) -> None:
        """注册确认函数（形如 TOOL_CTX.confirm）；未注册时模型命令一律拒绝。"""
        self._confirm = confirm_fn

    def set_cwd_source(self, provider) -> None:
        """注册共享工作目录来源（终端窗口当前目录）；传 None 注销。"""
        self._cwd_source = provider

    def shutdown(self) -> int:
        """应用退出：终止仍在运行的子进程并注销全部回调。

        返回被终止的进程数。子进程只在超时路径自动终止，正常退出时若不处理
        会随应用一起变成孤儿进程。
        """
        with self._proc_lock:
            pid, self._active_pid = self._active_pid, None
        terminated = 0
        if pid:
            ok, message = core.terminate_tree(pid)
            terminated = int(ok)
            if not ok:
                logger.warning("[TerminalRunner] 终止活跃子进程失败：%s", message)
        self.set_terminal_sink(None)
        self.set_cwd_source(None)
        self.set_confirm(None)
        return terminated

    def shared_cwd(self) -> str:
        """终端窗口的当前目录；没有窗口时返回空串。"""
        provider = self._cwd_source
        if provider is None:
            return ""
        try:
            return str(provider() or "")
        except Exception:
            logger.warning("[TerminalRunner] 读取共享目录失败", exc_info=True)
            return ""

    def run(self, command: str, cwd: str = "", timeout: int = 0,
            shell: str = "", **_ignored) -> dict:
        """模型入口：source 固定为 model，未声明参数忽略。"""
        return self._dispatch("model", command, cwd, timeout, shell)

    def run_user(self, command: str, cwd: str = "", timeout: int = 0,
                 shell: str = "") -> dict:
        """终端入口：用户输入的命令跳过程序解析与沙箱限制。"""
        return self._dispatch("user", command, cwd, timeout, shell)

    def _dispatch(self, source: str, command: str, cwd: str, timeout, shell: str) -> dict:
        try:
            return self._run(source, command, cwd, timeout, shell)
        except Exception as exc:
            logger.exception("[TerminalRunner] 未预期异常")
            return failure("internal_error", f"命令执行失败：{exc}")

    def snapshot(self) -> dict:
        """纯数据策略快照：不含任何面向 LLM 或终端的排版字段。"""
        entries = policy.allowed_entries()
        root = policy.effective_root(self.shared_cwd())
        allowed = [
            {"command": " ".join([program] + list(pattern)),
             "installed": self._installed(program)}
            for program, pattern in entries
        ]
        return {
            "platform": platform_name(),
            "sandbox_root": root,
            "allowed": allowed,
            "interpreters": list(policy.INTERPRETERS),
            "deny_categories": policy.deny_categories(),
            "shell_mode": bool(config.TERMINAL_ALLOW_SHELL),
            "mode": (config.TERMINAL_MODE or "ask").strip().lower(),
        }

    def list(self) -> dict:
        """terminal__list 的 LLM 投影：headline / body + 原始快照字段。"""
        data = self.snapshot()
        chips = [item["command"] for item in data["allowed"]]
        lines = [
            "免确认命令：" + (" | ".join(chips) if chips else "无"),
            "解释器一律询问：" + "、".join(policy.INTERPRETERS),
            f"工作目录根：{data['sandbox_root']}",
        ]
        return {
            "headline": f"当前档位 {data['mode']}，免确认命令 {len(chips)} 条",
            "body": "\n".join(lines),
            **data,
        }

    def prepare(self, run_id: str, source: CommandSource, command: str,
                cwd: str, timeout, shell: str) -> Prepared | dict:
        """校验、策略、确认与执行参数定稿；失败时已发出终态事件并返回 failure。"""
        text = str(command or "").strip()
        if source is not CommandSource.USER:
            if not config.TERMINAL_ENABLED:
                return self._reject(source, text, "terminal_disabled", run_id=run_id)
            if shell and not config.TERMINAL_ALLOW_SHELL:
                return self._reject(source, text, "shell_mode_disabled", run_id=run_id)

        parsed, err = core.parse_command(text, shell)
        if err:
                return self._reject(source, text, err, parsed.argv, run_id=run_id)

        default_s = (config.TERMINAL_MAX_COMMAND_TIMEOUT_S
                     if source is CommandSource.USER
                     else config.TERMINAL_COMMAND_TIMEOUT_S)
        timeout_s = policy.clamp_timeout(timeout, default_s)

        shared = self.shared_cwd() if source is not CommandSource.USER else ""
        verdict = policy.check(source.value, parsed.argv, parsed.builtin, text,
                               cwd=cwd, shell=shell, root=shared)
        if verdict.decision == "deny":
            return self._reject(source, text, verdict.reason, parsed.argv,
                                run_id=run_id)

        # 非内建、非 shell 模式执行判定解析出的绝对路径
        exec_argv = list(parsed.routed or parsed.argv)
        if not parsed.builtin and not shell and verdict.resolved:
            exec_argv = [verdict.resolved] + list(parsed.argv[1:])

        if verdict.decision == "ask":
            request = ConfirmRequest(
                command=text,
                argv=list(parsed.argv),
                builtin=parsed.builtin,
                routed=list(exec_argv),
                cwd=verdict.abs_cwd,
                shell_mode=shell,
                timeout_s=float(config.TERMINAL_CONFIRM_TIMEOUT_S),
                source=source.value,
                matched_patterns=list(verdict.matched),
                untrusted=verdict.untrusted,
            )
            confirm = self._confirm
            allowed = bool(confirm(request)) if confirm is not None else False
            if not allowed:
                self._notify_terminal(TerminalEvent.finished(
                    run_id, source, text, exec_argv, verdict.abs_cwd,
                    rejected=True, error=_MESSAGES["denied_by_user"]))
                return failure("denied_by_user")

        return Prepared(run_id=run_id, source=source, command=text,
                        argv=tuple(exec_argv), cwd=verdict.abs_cwd,
                        timeout_s=timeout_s, verdict=verdict)

    def execute(self, prepared: Prepared) -> core.RunResult:
        """只负责调用执行核心，并登记活跃 pid 供 shutdown() 收尾。"""
        return core.run(
            list(prepared.argv), prepared.cwd, prepared.timeout_s,
            policy.build_env(), int(config.TERMINAL_MAX_CAPTURE_BYTES),
            on_output=self._output_relay(prepared.source, prepared.run_id),
            on_start=self._track_pid,
        )

    def report(self, prepared: Prepared,
               result: core.RunResult) -> tuple[TerminalEvent, dict]:
        """把执行结果投影成终态事件与工具返回值；不发送、不碰闸门。"""
        base = (prepared.run_id, prepared.source, prepared.command,
                prepared.argv, prepared.cwd)
        end_event = TerminalEvent.finished(
            *base, rejected=False, exit_code=result.exit_code,
            stdout=result.stdout, stderr=result.stderr,
            duration_ms=result.duration_ms,
            error=result.error if result.code else "")
        if result.code:
            return end_event, failure(result.code, result.error)
        body, char_truncated = core.truncate_streams(
            result.stdout, result.stderr, int(config.TERMINAL_MAX_OUTPUT_CHARS))
        truncated = bool(char_truncated or result.truncated)
        headline = f"退出码 {result.exit_code}，输出 {core.count_lines(body)} 行"
        if truncated:
            headline += TRUNCATED_SUFFIX
        return end_event, {
            "headline": headline,
            "body": body,
            "exit_code": int(result.exit_code),
            "hint": "",
        }

    def _run(self, source: str, command: str, cwd: str, timeout, shell: str) -> dict:
        src = CommandSource(source)
        run_id = uuid.uuid4().hex
        prepared = self.prepare(run_id, src, command, cwd, timeout, shell)
        if isinstance(prepared, dict):
            return prepared

        self._notify_terminal(
            TerminalEvent.phase_event(run_id, src, Phase.WAITING))
        wait_s = max(0.0, float(config.TERMINAL_QUEUE_WAIT_S))
        if not self.lock.acquire(timeout=wait_s):
            # 模型命令没有窗口侧 _on_run_finished 兜底，busy 必须自己复位状态点
            self._notify_terminal(
                TerminalEvent.phase_event(run_id, src, Phase.IDLE))
            return self._reject(src, prepared.command, "busy", prepared.argv,
                                run_id=run_id)

        # 终态事件一律在闸门内发出，避免下一条命令的 start 抢在本条 end 之前；
        # 无论成功、业务失败还是异常，都恰好发一个 end 和一个 idle。
        end_event = TerminalEvent.finished(
            run_id, src, prepared.command, prepared.argv, prepared.cwd,
            rejected=True, error=_MESSAGES["internal_error"])
        outcome = failure("internal_error")
        try:
            self._notify_terminal(
                TerminalEvent.phase_event(run_id, src, Phase.RUNNING))
            self._notify_terminal(TerminalEvent.started(
                run_id, src, prepared.command, prepared.argv, prepared.cwd))
            end_event, outcome = self.report(prepared, self.execute(prepared))
        except Exception as exc:
            logger.exception("[TerminalRunner] 执行阶段异常")
            message = f"命令执行失败：{exc}"
            end_event = TerminalEvent.finished(
                run_id, src, prepared.command, prepared.argv, prepared.cwd,
                rejected=True, error=message)
            outcome = failure("internal_error", message)
        finally:
            self._notify_terminal(end_event)
            self._notify_terminal(
                TerminalEvent.phase_event(run_id, src, Phase.IDLE))
            self._clear_pid()
            self.lock.release()
        return outcome

    def _reject(self, source: CommandSource, command: str, code: str,
                argv=(), *, run_id: str) -> dict:
        logger.info("[TerminalRunner] %s 拒绝：%s", source, code)
        self._notify_terminal(TerminalEvent.finished(
            run_id, source, command, argv or (command,), "", rejected=True,
            error=_MESSAGES.get(code, code)))
        return failure(code)

    def _output_relay(self, source: CommandSource, run_id: str):
        """运行中按行回传给终端；没有终端窗口时不回传。"""
        if self._terminal_sink is None:
            return None

        def relay(stream: str, text: str) -> None:
            self._notify_terminal(
                TerminalEvent.chunk(run_id, source, stream, text))

        return relay

    def _track_pid(self, pid: int) -> None:
        with self._proc_lock:
            self._active_pid = int(pid)

    def _clear_pid(self) -> None:
        with self._proc_lock:
            self._active_pid = None

    def _notify_terminal(self, event: TerminalEvent) -> None:
        sink = self._terminal_sink
        if sink is None:
            return
        try:
            sink(event)
        except Exception:
            logger.exception("[TerminalRunner] 终端回显失败")

    def _installed(self, program: str) -> bool:
        now = time.monotonic()
        cached = self._installed_cache.get(program)
        if cached is not None and now - cached[0] < _WHICH_TTL_S:
            return cached[1]
        builtin = os.name == "nt" and program in core.BUILTINS
        installed = builtin or shutil.which(program) is not None
        self._installed_cache[program] = (now, installed)
        return installed


def platform_name() -> str:
    if os.name == "nt":
        return "Windows"
    if sys.platform == "darwin":
        return "macOS"
    return "Linux"


_runner: "TerminalRunner | None" = None
_runner_lock = threading.Lock()


def get_runner() -> TerminalRunner:
    """两个入口共用的实例：模型工具与终端窗口共用同一把执行闸门。"""
    global _runner
    if _runner is not None:
        return _runner
    with _runner_lock:
        if _runner is None:
            _runner = TerminalRunner()
    return _runner
