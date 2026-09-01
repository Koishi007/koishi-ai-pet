"""工具注册表 — 自动发现、注册、描述可用工具。"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Any

from pet.config import config

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
    meta: bool = False  # 元工具：系统内置，不可禁用、不出现在工具管理列表


class ToolRegistry:

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}
        self._disabled: set[str] = set()
        self._register_default_tools()

    def register(self, tool_name: str, description: str, group: str = None,
                 meta: bool = False) -> "ToolDef":
        tool = ToolDef(name=tool_name, description=description, meta=meta)
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
        self.register("tool_search", "工具发现：浏览和搜索可用的工具",
                      group="default", meta=True)
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
        # 觅食工具
        if config.FOOD_ENABLED:
            self._register_food_tools()
        # 游戏管理工具
        self._register_game_tools()
        # 记忆检索元工具
        self._register_recall_tools()

    def _register_food_tools(self):
        """注册觅食工具（FOOD_ENABLED=True 时调用）。"""
        self.register("food", "觅食：在桌面上生成食物，或查询食物状态与距离",
                      group="default", meta=True)
        self.add_method(
            tool_name="food",
            method_name="spawn",
            description=(
                "在肚子饿了的时候调用，在桌面随机位置生成食物，返回坐标与偏移 dx/dy/bounce_height；"
                "用 walk/drive 接近、bounce 跳起来吃，走到附近自动开吃；已有食物时不重复生成。"
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
                "查询当前食物状态：实时偏移 dx/dy/bounce_height、是否到达、剩余过期时间；"
                "无食物则确认是否需要生成。"
            ),
            handler=self._food_status,
        )

    def _food_spawn(self, food_type: str = None) -> dict:
        """food__spawn 工具实现（懒 import，避免注册表依赖行为层）。"""
        from pet.food.food import FOOD
        return FOOD.spawn(food_type)

    def _food_status(self) -> dict:
        """food__status 工具实现（懒 import，避免注册表依赖行为层）。"""
        from pet.food.food import FOOD
        return FOOD.status()

    def _register_game_tools(self):
        """注册游戏元工具：回合制游戏，模型通过 game__play 连续调用游玩。"""
        self.register("game", "游戏：玩回合制小游戏，如猜数字",
                      group="default", meta=True)
        self.add_method(
            tool_name="game",
            method_name="list",
            description=(
                "列出当前可玩的游戏及规则。想玩游戏时先调用此方法了解可选游戏。"
            ),
            handler=self._game_list,
        )
        self.add_method(
            tool_name="game",
            method_name="init",
            description=(
                "开始（或重新开始）一局游戏：传 game_name 创建新对局。"
                "所有游戏必须先调用 game__init 开启，再通过 game__play 推进；"
                "游戏结束（ended=True）或返回『未开始/已结束』后，需要再次调用 game__init 才能开新局。"
            ),
            handler=self._game_init,
            args={
                "game_name": {
                    "type": "str",
                    "required": True,
                    "description": "要初始化/重新初始化的游戏名",
                },
            },
        )
        self.add_method(
            tool_name="game",
            method_name="play",
            description=(
                "玩一回合游戏：需先调用 game__init 开启对局，再传 game_name 和该游戏需要的动作参数"
                "（参数因游戏而异，见 game_name 枚举及参数说明），返回本回合结果。"
                "游戏未结束（ended=False）前必须继续调用 game__play 推进游戏，不要提前输出最终答复；"
                "结束（ended=True）时会自动结算，也可以主动调 game__stop 结束不想玩的游戏。"
                "你可以在一次输出里连续调用多次 game__play，根据每次返回结果继续，直到某次返回 ended=True。"
            ),
            handler=self._game_play,
        )
        self.add_method(
            tool_name="game",
            method_name="stop",
            description=(
                "主动结束某个进行中的游戏。游戏自然结束后无需调用。"
            ),
            handler=self._game_stop,
            args={
                "game_name": {
                    "type": "str",
                    "required": True,
                    "description": "要结束的游戏名",
                },
            },
        )

    def _register_recall_tools(self):
        """注册记忆检索元工具（只读）：search 语义回忆，browse 分页翻阅。"""
        self.register(
            "recall",
            "回忆：主动检索你关于用户的长期记忆。"
            "系统每轮自动注入的记忆条数有限，当你感觉记忆不完整、"
            "或用户提到过去的事而注入段中没有相关内容时，用此工具补齐。",
            group="default", meta=True,
        )
        self.add_method(
            tool_name="recall",
            method_name="search",
            description=(
                "按语义或关键词回忆与查询相关的记忆。"
                "自动注入的记忆不足、想不起细节、或用户问『你还记得…吗』『我之前说过什么』时调用；"
                "检索到的结果直接使用即可，同一话题无需重复检索。"
            ),
            handler=self._recall_search,
            args={
                "query": {
                    "type": "str",
                    "required": True,
                    "desc": "回忆的线索：人名、事件、话题关键词等",
                },
                "limit": {
                    "type": "int",
                    "required": False,
                    "default": 5,
                    "desc": "返回条数(1~10)",
                },
            },
            timeout=30.0,
        )
        self.add_method(
            tool_name="recall",
            method_name="browse",
            description=(
                "分页翻阅你关于用户的全部记忆，可按创建日期范围/关键词筛选。"
                "日期均为 YYYY-MM-DD 且含首尾两天，可只填一端表示不限。"
                "想不起来具体线索、或想整体看看自己记住了什么时调用。"
            ),
            handler=self._recall_browse,
            args={
                "start_date": {
                    "type": "str",
                    "required": False,
                    "default": "",
                    "desc": "开始日期(YYYY-MM-DD)，含当天；不填=不限",
                },
                "end_date": {
                    "type": "str",
                    "required": False,
                    "default": "",
                    "desc": "结束日期(YYYY-MM-DD)，含当天；不填=不限",
                },
                "keyword": {
                    "type": "str",
                    "required": False,
                    "default": "",
                    "desc": "内容/关键词模糊匹配",
                },
                "page": {
                    "type": "int",
                    "required": False,
                    "default": 1,
                    "desc": "页码(从1开始)",
                },
            },
        )

    @staticmethod
    def _empty_recall(summary: str) -> dict:
        """recall__browse 的空结果（含参数错误提示）。"""
        return {"summary": summary, "memories": [], "total": 0,
                "page": 1, "total_pages": 0}

    @staticmethod
    def _normalize_date(value: str, name: str) -> tuple[str, str]:
        """把日期参数规范化为 YYYY-MM-DD，返回 (值, 错误信息)。

        strptime 会接受 '2025-8-1' 这类非补零写法，但 SQL 端要求定长格式，
        不回写为标准形态会出现「校验通过却查不到任何记录」的静默失败。
        """
        value = (value or "").strip()
        if not value:
            return "", ""
        try:
            return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d"), ""
        except ValueError:
            return "", f"{name} 格式无效，请使用 YYYY-MM-DD，例如 2025-08-01"

    @staticmethod
    def _format_recall_memory(row: dict) -> dict:
        """将记忆行精简为 LLM 可读字段。"""
        from pet.brain.memory import _MemoryRetriever
        return {
            "content": row.get("content", ""),
            "level": row.get("level", "L2"),
            "importance": row.get("importance", 3),
            "time": _MemoryRetriever._format_memory_time(row.get("created_at", "")),
        }

    def _recall_search(self, query: str, limit: int = 5) -> dict:
        """recall__search 工具实现。"""
        from pet.tools.context import TOOL_CTX
        from pet.brain.memory import get_memory_store
        TOOL_CTX.speech_random(["让我想想…", "回忆一下…", "翻翻记忆…"])
        query = (query or "").strip()
        if not query:
            return {"summary": "回忆线索为空，请提供人名、事件或话题关键词",
                    "memories": [], "count": 0}
        try:
            limit = max(1, min(int(limit), 10))
        except (TypeError, ValueError):
            limit = 5
        results = get_memory_store().search_memories(query, limit=limit)
        if not results:
            return {"summary": "没有想起相关记忆", "memories": [], "count": 0}
        memories = [self._format_recall_memory(r) for r in results]
        return {
            "summary": f"想起 {len(memories)} 条与「{query}」相关的记忆",
            "memories": memories,
            "count": len(memories),
        }

    def _recall_browse(self, start_date: str = "", end_date: str = "",
                       keyword: str = "", page: int = 1, **_) -> dict:
        """recall__browse 工具实现。

        **_ 吞掉模型多传的未声明参数：executor 会把 schema 外的键原样透传给
        handler，缺少它时一个多余参数就会让整次调用 TypeError 失败。
        """
        from pet.tools.context import TOOL_CTX
        from pet.brain.memory import get_memory_store
        TOOL_CTX.speech_random(["翻翻记忆…", "看看都记了些什么…", "回忆一下…"])
        start_date, err = self._normalize_date(start_date, "start_date")
        if err:
            return self._empty_recall(err)
        end_date, err = self._normalize_date(end_date, "end_date")
        if err:
            return self._empty_recall(err)
        if start_date and end_date and start_date > end_date:
            return self._empty_recall(
                f"日期范围无效：开始日期 {start_date} 晚于结束日期 {end_date}"
            )
        keyword = (keyword or "").strip()
        try:
            page = max(1, int(page or 1))
        except (TypeError, ValueError):
            page = 1
        page_size = 10
        # list_memories 的 page 从 0 开始，工具入参从 1 开始
        rows, total = get_memory_store().list_memories(
            search=keyword, start_date=start_date, end_date=end_date,
            page=page - 1, page_size=page_size,
        )
        filters = [f for f in (
            f"{start_date} 起" if start_date else "",
            f"{end_date} 止" if end_date else "",
            f"关键词「{keyword}」" if keyword else "",
        ) if f]
        filter_str = f"（筛选：{' / '.join(filters)}）" if filters else ""
        total_pages = (total + page_size - 1) // page_size
        if not rows:
            if total == 0:
                return {"summary": f"没有符合条件的记忆{filter_str}",
                        "memories": [], "total": 0, "page": page, "total_pages": 0}
            # 页码越界：明确告知范围，避免模型误判为「没有记忆」
            return {"summary": f"第 {page} 页超出范围：共 {total} 条记忆，仅 {total_pages} 页{filter_str}",
                    "memories": [], "total": total, "page": page,
                    "total_pages": total_pages}
        memories = [self._format_recall_memory(r) for r in rows]
        return {
            "summary": f"共 {total} 条记忆，第 {page}/{total_pages} 页{filter_str}",
            "memories": memories,
            "total": total,
            "page": page,
            "total_pages": total_pages,
        }

    def _game_list(self) -> dict:
        """game__list 工具实现。"""
        from pet.game.gamebase import GAME
        return GAME.list_games()

    def _game_play(self, game_name: str, **params) -> dict:
        """game__play 工具实现。"""
        from pet.game.gamebase import GAME
        return GAME.play(game_name, **params)

    def _game_init(self, game_name: str) -> dict:
        """game__init 工具实现。"""
        from pet.game.gamebase import GAME
        return GAME.init(game_name)

    def _game_stop(self, game_name: str) -> dict:
        """game__stop 工具实现。"""
        from pet.game.gamebase import GAME
        return GAME.stop(game_name)

    def _refresh_game_play_args(self):
        """每次生成工具列表前，用当前已注册游戏动态刷新 game__play/init 的参数 schema。"""
        tool = self._tools.get("game")
        if tool is None:
            return
        from pet.game.gamebase import GAME
        play = tool.methods.get("play")
        if play is not None:
            play.args = GAME.play_args_schema()
        init = tool.methods.get("init")
        if init is not None:
            init.args = {
                "game_name": {
                    "type": "str",
                    "required": True,
                    "description": "要初始化/重新初始化的游戏名",
                    "enum": GAME.names(),
                },
            }

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
        self._refresh_game_play_args()
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
                # 通用可选参数：模型调用工具时可带 aside，作为自言自语播出（不传给 handler）
                properties["aside"] = {
                    "type": "string",
                    "description": "（可选）调用此工具时的自言自语，按你的人格口吻说一句，只让用户理解你正在做什么，不会作为对用户的正式回复；不想说可不填",
                }
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
