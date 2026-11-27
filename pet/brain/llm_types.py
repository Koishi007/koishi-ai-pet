"""供应商边界的中性类型：工具调用、用量、补全结果与流式增量。"""

from dataclasses import dataclass
from typing import Optional

# 结束原因收敛为四个值，各 provider 负责把供应商取值映射到这里。
# tool_calls 只在供应商原生返回该值时产出：Gemini 在返回函数调用时仍报 STOP，
# 因此该值只描述「结束原因」，消费方判工具调用看 tool_calls 字段而不是这里。
FINISH_STOP = "stop"
FINISH_LENGTH = "length"
FINISH_CONTENT_FILTER = "content_filter"
FINISH_TOOL_CALLS = "tool_calls"


@dataclass(frozen=True)
class ToolCall:
    """一次完整工具调用；arguments 是 JSON 字符串。

    id 由上游返回，或由不返回 id 的 provider 按序号生成。生成方只保证同一段
    历史内不重复；跨轮次重复时由编码层取最近一次同名调用消歧。
    """

    id: str
    name: str
    arguments: str = "{}"


@dataclass(frozen=True)
class ToolCallDelta:
    """流式工具调用增量；complete 为真时消费方替换累计参数而不是追加。"""

    index: int
    id: str = ""
    name: str = ""
    arguments: str = ""
    complete: bool = False


@dataclass(frozen=True)
class Usage:
    """一次请求的 token 用量。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class Completion:
    """非流式补全结果。"""

    content: str = ""
    tool_calls: tuple = ()
    finish_reason: str = FINISH_STOP
    usage: Optional[Usage] = None


@dataclass(frozen=True)
class StreamEvent:
    """流式增量；一个事件只承载一类事实。"""

    content: str = ""
    reasoning: str = ""
    tool_call: Optional[ToolCallDelta] = None
    finish_reason: Optional[str] = None
    usage: Optional[Usage] = None
