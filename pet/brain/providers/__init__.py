"""供应商边界：对话协议、能力声明与连接参数。

嵌入与模型列表是可选的通道能力，由 Capabilities 声明，调用方先看声明再调方法；
协议只保留所有通道都必须实现的对话方法。
"""

from dataclasses import dataclass
from typing import Iterator, Optional, Protocol, runtime_checkable

from pet.brain.llm_errors import ProviderError
from pet.brain.llm_types import Completion, StreamEvent


@dataclass(frozen=True)
class Capabilities:
    """通道能力声明；真源在 provider，不来自配置。"""

    vision: bool = False
    native_tools: bool = False
    thinking: bool = False
    embedding: bool = False
    model_list: bool = False


@dataclass(frozen=True)
class LLMConnection:
    """一套连接参数；provider 留空表示沿用首选方案的通道。"""

    provider: str
    key: str = ""
    url: str = ""
    model: str = ""


@runtime_checkable
class ChatProvider(Protocol):
    """对话通道；stream() 调用即建连并返回带 close() 的迭代器。"""

    name: str
    model: str
    capabilities: Capabilities

    def complete(self, messages: list, *, max_tokens: int, tools: Optional[list] = None,
                 thinking: Optional[bool] = None,
                 temperature: Optional[float] = None) -> Completion: ...

    def stream(self, messages: list, *, max_tokens: int, tools: Optional[list] = None,
               thinking: Optional[bool] = None,
               temperature: Optional[float] = None) -> Iterator[StreamEvent]: ...

    def thinking_rejected(self, exc: ProviderError) -> bool: ...

    def reasoning_effort_rejected(self) -> bool:
        """该实例是否已因端点拒绝而停发推理强度；不提供分档的通道返回假。"""
        ...

    def close(self) -> None: ...
