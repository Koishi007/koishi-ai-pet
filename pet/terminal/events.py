"""终端编排层 → 界面层的事件协议"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class _ValueEnum(str, Enum):
    """字符串枚举：f-string 与字典键都按值处理，避免打印出 Decision.ASK。"""

    def __str__(self) -> str:
        return self.value


class EventKind(_ValueEnum):
    """事件种类；界面据此分派，不再有「没有 kind 就整块渲染」的兜底。"""

    PHASE = "phase"
    START = "start"
    CHUNK = "chunk"
    END = "end"


class Phase(_ValueEnum):
    """状态点四态。"""

    WAITING = "waiting"
    RUNNING = "running"
    ERROR = "error"
    IDLE = "idle"


class CommandSource(_ValueEnum):
    """命令发起方。"""

    MODEL = "model"
    USER = "user"


class Decision(_ValueEnum):
    """策略判定结论。"""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class TerminalEvent:
    """一条终端事件。kind 决定哪些字段有意义，请一律用下面的构造器。"""

    kind: EventKind
    run_id: str = ""
    source: CommandSource = CommandSource.MODEL
    phase: Phase | None = None
    command: str = ""
    argv: tuple[str, ...] = ()
    cwd: str = ""
    stream: str = ""
    text: str = ""
    rejected: bool = False
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    error: str = ""

    @classmethod
    def phase_event(cls, run_id: str, source: CommandSource,
                    phase: Phase) -> "TerminalEvent":
        return cls(kind=EventKind.PHASE, run_id=run_id, source=source, phase=phase)

    @classmethod
    def started(cls, run_id: str, source: CommandSource, command: str,
                argv, cwd: str) -> "TerminalEvent":
        return cls(kind=EventKind.START, run_id=run_id, source=source,
                   command=command, argv=tuple(argv), cwd=cwd)

    @classmethod
    def chunk(cls, run_id: str, source: CommandSource, stream: str,
              text: str) -> "TerminalEvent":
        return cls(kind=EventKind.CHUNK, run_id=run_id, source=source,
                   stream=stream if stream in ("stdout", "stderr") else "stdout",
                   text=text)

    @classmethod
    def finished(cls, run_id: str, source: CommandSource, command: str, argv,
                 cwd: str, *, rejected: bool = False, exit_code: int | None = None,
                 stdout: str = "", stderr: str = "", duration_ms: int = 0,
                 error: str = "") -> "TerminalEvent":
        return cls(kind=EventKind.END, run_id=run_id, source=source,
                   command=command, argv=tuple(argv), cwd=cwd,
                   rejected=rejected, exit_code=exit_code, stdout=stdout,
                   stderr=stderr, duration_ms=duration_ms, error=error)
