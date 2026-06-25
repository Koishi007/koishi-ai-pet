"""工具注册表 — 自动发现、注册、描述可用工具。"""

import logging
from dataclasses import dataclass, field
from typing import Callable, Any

logger = logging.getLogger(__name__)


@dataclass
class ToolMethod:
    name: str
    description: str
    args: dict = field(default_factory=dict)
    handler: Callable = None
    timeout: float = 30.0


@dataclass
class ToolDef:
    name: str
    description: str
    methods: dict[str, ToolMethod] = field(default_factory=dict)
    menu_items: list[dict] = field(default_factory=list)


class ToolRegistry:

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}
        self._disabled: set[str] = set()

    def register(self, tool_name: str, description: str) -> "ToolDef":
        tool = ToolDef(name=tool_name, description=description)
        self._tools[tool_name] = tool
        return tool

    def add_method(self, tool_name: str, method_name: str,
                   description: str, handler: Callable, args: dict = None,
                   timeout: float = None):
        tool = self._tools[tool_name]
        tool.methods[method_name] = ToolMethod(
            name=method_name, description=description,
            args=args or {}, handler=handler,
            timeout=timeout if timeout is not None else 30.0,
        )

    def add_menu_action(self, tool_name: str, label: str,
                        handler: Callable):
        """注册一个工具右键子菜单项。handler 在点击时调用。"""
        tool = self._tools[tool_name]
        tool.menu_items.append({"label": label, "handler": handler})
        logger.info(f"[ToolRegistry] menu item added: {tool_name} > {label}")

    def get_method(self, full_name: str) -> ToolMethod | None:
        """通过 'tool_name.method_name' 获取方法对象。"""
        parts = full_name.split(".", 1)
        if len(parts) != 2:
            return None
        tool_name, method_name = parts
        if not self.is_enabled(tool_name):
            return None
        tool = self._tools.get(tool_name)
        if not tool:
            return None
        return tool.methods.get(method_name)

    def get_handler(self, full_name: str) -> Callable | None:
        method = self.get_method(full_name)
        return method.handler if method else None

    @property
    def enabled_tools(self) -> list["ToolDef"]:
        return [t for t in self._tools.values() if self.is_enabled(t.name)]

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def is_enabled(self, tool_name: str) -> bool:
        return tool_name not in self._disabled

    def set_enabled(self, tool_name: str, enabled: bool):
        if enabled:
            self._disabled.discard(tool_name)
            logger.info(f"[Tool] enabled: {tool_name}")
        else:
            self._disabled.add(tool_name)
            logger.info(f"[Tool] disabled: {tool_name}")

    @property
    def disabled_set(self) -> set[str]:
        return set(self._disabled)

    _TYPE_TO_JSON_SCHEMA = {
        "str": "string",
        "int": "integer",
        "float": "number",
        "bool": "boolean",
        "list": "array",
        "dict": "object",
        "any": "string",
    }

    def to_openai_tools(self) -> list[dict]:
        """将已注册工具转换为 OpenAI function calling 格式。"""
        tools = []
        for tool in self.enabled_tools:
            for method_name, method in tool.methods.items():
                properties = {}
                required = []
                for arg_name, spec in method.args.items():
                    py_type = spec.get("type", "str")
                    json_type = self._TYPE_TO_JSON_SCHEMA.get(py_type, "string")
                    prop = {
                        "type": json_type,
                        "description": spec.get("desc", spec.get("description", "")),
                    }
                    if "default" in spec:
                        prop["default"] = spec["default"]
                    if "enum" in spec:
                        prop["enum"] = spec["enum"]
                    properties[arg_name] = prop
                    if spec.get("required"):
                        required.append(arg_name)
                tools.append({
                    "type": "function",
                    "function": {
                        "name": f"{tool.name}.{method_name}",
                        "description": method.description,
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        },
                    },
                })
        return tools

    def to_prompt_summary(self) -> str:
        """生成简短的工具能力概览，供注入 system prompt。

        与 to_openai_tools() 互补：
        - to_openai_tools() 提供完整 function schema 给 API 参数
        - to_prompt_summary() 提供简短文字概览给 prompt，增强 LLM 主动调用意识
        """
        tools = self.enabled_tools
        if not tools:
            return ""
        lines = [f"- {t.description}" for t in tools]
        return (
            "=== 可用工具 ===\n"
            "你拥有以下工具能力，自主决策时可主动使用：\n"
            + "\n".join(lines)
            + "\n需要时直接发起 function call。"
        )


TOOL_REGISTRY = ToolRegistry()
