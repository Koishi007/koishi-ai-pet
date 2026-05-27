"""工具注册表 — 自动发现、注册、描述可用工具。"""

from dataclasses import dataclass, field
from typing import Callable, Any


@dataclass
class ToolMethod:
    name: str
    description: str
    args: dict = field(default_factory=dict)
    handler: Callable = None


@dataclass
class ToolDef:
    name: str
    description: str
    methods: dict[str, ToolMethod] = field(default_factory=dict)


class ToolRegistry:
    """全局工具注册表。"""

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool_name: str, description: str) -> "ToolDef":
        tool = ToolDef(name=tool_name, description=description)
        self._tools[tool_name] = tool
        return tool

    def add_method(self, tool_name: str, method_name: str,
                   description: str, handler: Callable, args: dict = None):
        tool = self._tools[tool_name]
        tool.methods[method_name] = ToolMethod(
            name=method_name, description=description,
            args=args or {}, handler=handler,
        )

    def get_handler(self, full_name: str) -> Callable | None:
        parts = full_name.split(".", 1)
        if len(parts) != 2:
            return None
        tool_name, method_name = parts
        tool = self._tools.get(tool_name)
        if not tool:
            return None
        method = tool.methods.get(method_name)
        return method.handler if method else None

    def generate_prompt_section(self) -> str:
        """生成注入 LLM prompt 的工具描述段。"""
        if not self._tools:
            return ""
        lines = ["=== 可用工具 ===",
                 "当你需要调用工具。输出格式：",
                 '  Tool: {"name": "tool.method", "args": {}}',
                 "",
                 "可用工具列表："]
        for tool in self._tools.values():
            lines.append(f"\n【{tool.name}】{tool.description}")
            for m in tool.methods.values():
                args_desc = ", ".join(f"{k}: {v}" for k, v in m.args.items())
                args_part = f"  参数: {{{args_desc}}}" if args_desc else "  无参数"
                lines.append(f"  - {tool.name}.{m.name}: {m.description}")
                lines.append(f"    {args_part}")
        return "\n".join(lines)

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())


# 全局单例
TOOL_REGISTRY = ToolRegistry()
