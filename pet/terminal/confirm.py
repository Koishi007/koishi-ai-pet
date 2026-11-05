"""确认请求的数据结构：执行层与界面层共用，不依赖 Qt。"""

import threading
from dataclasses import dataclass, field


@dataclass
class ConfirmRequest:
    """一条待确认命令：Runner 填充，主线程只写 result 并 set 事件。"""

    command: str = ""
    argv: list[str] = field(default_factory=list)
    builtin: str = ""
    routed: list[str] = field(default_factory=list)
    cwd: str = ""
    shell_mode: str = ""
    timeout_s: float = 0.0
    source: str = "model"
    matched_patterns: list[str] = field(default_factory=list)
    untrusted: bool = False
    sequence: int = 0
    total: int = 0
    event: threading.Event = field(default_factory=threading.Event)
    result: bool = False
