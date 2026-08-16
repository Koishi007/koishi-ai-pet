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
    group: str = "default"


class ToolRegistry:

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}
        self._disabled: set[str] = set()
        self._register_default_tools()

    def register(self, tool_name: str, description: str, group: str = None) -> "ToolDef":
        tool = ToolDef(name=tool_name, description=description)
        if group:
            tool.group = group
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
        parts = full_name.split("__", 1)
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

    def get_groups(self) -> list[str]:
        """返回所有已注册的分组名，去重排序。"""
        return sorted(set(t.group for t in self._tools.values()))

    def get_tools_by_group(self, group: str) -> list["ToolDef"]:
        """返回指定分组下的所有工具。"""
        return [t for t in self._tools.values() if t.group == group]

    def get_tool_group(self, tool_name: str) -> str:
        """返回工具所属分组名。"""
        t = self._tools.get(tool_name)
        return t.group if t else "default"

    def _register_default_tools(self):
        """注册内置元工具 tool_search（不可插拔，始终可用）。"""
        self.register("tool_search", "工具发现：浏览和搜索可用的工具", group="default")
        self.add_method(
            tool_name="tool_search",
            method_name="list_groups",
            description=(
                "【优先使用】列出所有可用的工具分组，含每组下的工具名称和功能描述。"
                "先调用此方法了解全局有哪些工具可用，再决定用哪个。"
                "仅需1次调用，无需参数，始终返回完整列表。"
            ),
            handler=self._tool_search_list_groups,
        )
        self.add_method(
            tool_name="tool_search",
            method_name="search",
            description=(
                "按关键词精确搜索工具。匹配工具名、描述或分组名。"
                "搜索返回的分组会在后续请求中自动变为可用。"
                "如无匹配结果会自动返回全部工具列表。"
                "提示：不确定关键词时优先用 list_groups 浏览全部。"
            ),
            handler=self._tool_search_search,
            args={
                "keyword": {
                    "type": "str",
                    "description": "搜索关键词，如 '文件'、'浏览器'、'提醒'、'天气'",
                }
            },
        )
        # 觅食游戏元工具：与 tool_search 同级常驻（不受 TOOLS_ENABLED 控制，
        # 计入 behavior._META_TOOL_NAMES 免轮次配额）。handler 懒 import
        # FoodGameManager，注册表本体不依赖游戏层。
        self.register("food", "觅食：在桌面上生成食物，或查询食物状态与距离", group="default")
        self.add_method(
            tool_name="food",
            method_name="spawn",
            description=(
                "在桌面上生成一份食物，返回坐标和相对你当前位置的水平偏移 dx 与方向。"
                "生成后用 Action: walk left/right <dx> 走过去；走到食物附近会自动开吃。"
                "桌面上已有食物时不会重复生成，会返回已有食物的位置。"
            ),
            handler=self._food_spawn,
            args={
                "food_type": {
                    "type": "str",
                    "required": False,
                    "description": "想要的食物类型；不填则随机",
                    "enum": ["蛋糕", "饭团", "苹果", "拉面", "鸡腿", "甜甜圈", "披萨", "草莓", "饺子", "寿司"],
                }
            },
        )
        self.add_method(
            tool_name="food",
            method_name="status",
            description=(
                "查询桌面上食物的状态：是否存在、位置、距离你多远（实时 dx）、"
                "是否已经到达、剩余过期时间。没有食物时可用来确认是否需要生成。"
            ),
            handler=self._food_status,
        )

    def _food_spawn(self, food_type: str = None) -> dict:
        """food__spawn 工具实现（懒 import，避免注册表依赖游戏层）。"""
        from pet.game.food_game import FOOD_GAME
        return FOOD_GAME.spawn(food_type)

    def _food_status(self) -> dict:
        """food__status 工具实现（懒 import，避免注册表依赖游戏层）。"""
        from pet.game.food_game import FOOD_GAME
        return FOOD_GAME.status()

    def _tool_search_list_groups(self) -> dict:
        groups = []
        for grp in self.get_groups():
            tools = self.get_tools_by_group(grp)
            enabled = [t for t in tools if self.is_enabled(t.name)]
            groups.append({
                "group": grp,
                "tool_count": len(enabled),
                "tools": [{"name": t.name, "description": t.description} for t in enabled],
            })
        return {"groups": groups}

    def _tool_search_search(self, keyword: str = "") -> dict:
        kw = keyword.strip().lower() if keyword else ""
        results = []
        for tool in self.enabled_tools:
            if tool.group == "default":
                continue
            name_low = tool.name.lower()
            desc_low = tool.description.lower()
            if not kw or kw in name_low or kw in desc_low or kw in tool.group.lower():
                methods_list = [
                    {"name": f"{tool.name}__{m.name}", "description": m.description}
                    for m in tool.methods.values()
                ]
                results.append({
                    "name": tool.name,
                    "group": tool.group,
                    "description": tool.description,
                    "methods": methods_list,
                })

        hint = None
        if kw and not results:
            # 无匹配：fallback 返回全部工具，引导用 list_groups
            for tool in self.enabled_tools:
                if tool.group == "default":
                    continue
                methods_list = [
                    {"name": f"{tool.name}__{m.name}", "description": m.description}
                    for m in tool.methods.values()
                ]
                results.append({
                    "name": tool.name,
                    "group": tool.group,
                    "description": tool.description,
                    "methods": methods_list,
                })
            hint = (f"未找到与「{keyword}」匹配的工具，已返回全部可用工具。")

        return {"keyword": keyword, "matches": results, "hint": hint}

    _TYPE_TO_JSON_SCHEMA = {
        "str": "string",
        "int": "integer",
        "float": "number",
        "bool": "boolean",
        "list": "array",
        "dict": "object",
        "any": "string",
    }

    def to_openai_tools(self, groups: set[str] | None = None) -> list[dict]:
        """将已注册工具转换为 OpenAI function calling 格式。
        
        Args:
            groups: 指定分组集合，None 表示返回全部已启用工具。
        """
        tools = []
        for tool in self.enabled_tools:
            if groups is not None and tool.group not in groups:
                continue
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
                        "name": f"{tool.name}__{method_name}",
                        "description": method.description,
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        },
                    },
                })
        return tools

TOOL_REGISTRY = ToolRegistry()
