"""工具注册表 — 自动发现、注册、描述可用工具。"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Any, Optional

from pet.config import config
from pet.tools.context import TOOL_CTX
from pet.tools.output import PAGINATION_SCHEMA, ToolOutputDefinition

logger = logging.getLogger(__name__)


@dataclass
class ToolMethod:
    name: str
    description: str
    output: "ToolOutputDefinition"  # 输出契约，注册时必须声明
    args: dict = field(default_factory=dict)
    handler: Callable = None
    timeout: float = 30.0


@dataclass
class ToolDef:
    name: str
    description: str
    methods: dict[str, ToolMethod] = field(default_factory=dict)
    menu_items: list[dict] = field(default_factory=list)
    file_actions: list[dict] = field(default_factory=list)
    group: str = "default"
    meta: bool = False  # 元工具：系统内置，不可禁用、不出现在工具管理列表
    side_effect: bool = False  # 是否对外产生副作用（点赞、写文件等），用于崩溃窗口判定


_SEARCH_SPLIT = re.compile(r"[\s,，、;；/|+&]+")

# 单次搜索最多返回的工具数：与文件/浏览/知识库的分页同一个思路，避免一次吐出全部定义
_MAX_SEARCH_RESULTS = 5

_HEADLINE = {"type": "string", "x-role": "headline",
             "description": "一句话结论，进历史记录"}

FOOD_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": _HEADLINE,
        "data": {"type": "object", "x-role": "data",
                 "description": "食物位置与相对宠物的偏移"},
    },
}

GAME_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": _HEADLINE,
        "hint": {"type": "string", "x-role": "hint",
                       "description": "下一步该传什么参数"},
        "ended": {"type": "boolean", "x-role": "data",
                  "description": "本局是否结束"},
        "won": {"x-role": "data",
                "description": "胜负：true 获胜，false 失败，null 平局或未分胜负"},
        "data": {"type": "object", "x-role": "data",
                 "description": "对局状态与游戏专属字段"},
    },
}

SEARCH_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": _HEADLINE,
        "body": {"type": "string", "x-role": "body", "description": "命中的工具与方法清单"},
        "hint": {"type": "string", "x-role": "hint",
                       "description": "下一步该直接调用哪个函数"},
        "groups": {"type": "array", "x-role": "suppress",
                   "description": "命中的工具分组，供行为层激活"},
    },
}

GROUPS_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": _HEADLINE,
        "body": {"type": "string", "x-role": "body", "description": "按分组列出的工具清单"},
    },
}

RECALL_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": _HEADLINE,
        "body": {"type": "string", "x-role": "body", "description": "每条记忆一行"},
        "pagination": {**PAGINATION_SCHEMA},
    },
}


def _search_normalize(text: str) -> str:
    """搜索归一化：小写并去掉全部空白。"""
    return "".join((text or "").lower().split())


def _search_terms(keyword: str) -> list[str]:
    """按空白与常见分隔符把关键词拆成词，归一化后丢弃空串。"""
    return [t for t in (_search_normalize(part) for part in _SEARCH_SPLIT.split(keyword or "")) if t]


def _tool_search_text(tool: "ToolDef") -> str:
    """工具的可搜索文本：名称、分组、描述，以及每个方法的短名与全名。"""
    parts = [tool.name, tool.group, tool.description]
    for method in tool.methods.values():
        parts.append(method.name)
        # 全名也进索引
        parts.append(f"{tool.name}__{method.name}")
        parts.append(method.description)
    return _search_normalize(" ".join(parts))


class ToolRegistry:

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}
        self._disabled: set[str] = set()
        self._register_default_tools()

    def register(self, tool_name: str, description: str, group: str = None,
                 meta: bool = False, side_effect: bool = False) -> "ToolDef":
        tool = ToolDef(name=tool_name, description=description, meta=meta,
                       side_effect=side_effect)
        if group:
            tool.group = group
        self._tools[tool_name] = tool
        return tool

    def is_side_effect(self, full_name: str) -> bool:
        """工具是否有对外副作用；未注册的工具按有副作用处理。"""
        tool = self._tools.get(full_name.split("__", 1)[0])
        return True if tool is None else tool.side_effect

    def add_method(self, tool_name: str, method_name: str,
                   description: str, handler: Callable, args: dict = None,
                   timeout: float = None, *,
                   output: "ToolOutputDefinition"):
        """注册方法；output 必填，缺失直接报错。"""
        if output is None:
            raise ValueError(f"{tool_name}__{method_name} 必须声明 output")
        tool = self._tools[tool_name]
        tool.methods[method_name] = ToolMethod(
            name=method_name, description=description, output=output,
            args=args or {}, handler=handler,
            timeout=timeout if timeout is not None else 30.0,
        )

    def add_menu_action(self, tool_name: str, label: str,
                        handler: Callable):
        """注册一个工具右键子菜单项。handler 在点击时调用。"""
        tool = self._tools[tool_name]
        tool.menu_items.append({"label": label, "handler": handler})
        logger.info(f"[ToolRegistry] menu item added: {tool_name} > {label}")

    def add_file_action(self, tool_name: str, action_id: str, label: str,
                        handler: Callable, accepts: str = "any",
                        needs_content: bool = True):
        """注册一个文件动作：拖入文件后由文件气泡渲染成按钮。

        accepts 取值：text（需要可读文本）、image（需要可解码图片）、any（只要有元信息）。
        needs_content 为真时该动作跟随内容读取开关隐藏，只需文件名与路径的动作传 False。
        handler 签名 (files: list[FileRef]) -> {"ok": bool, "summary": str}，一次调用收到全部文件。
        """
        tool = self._tools[tool_name]
        tool.file_actions.append({"tool": tool_name, "id": action_id, "label": label,
                                  "handler": handler, "accepts": accepts,
                                  "needs_content": bool(needs_content)})
        logger.info(f"[ToolRegistry] file action added: {tool_name} > {label}")

    def file_actions(self) -> list[dict]:
        """返回已注册且已启用的工具声明的文件动作。"""
        actions: list[dict] = []
        for tool in self._tools.values():
            if tool.file_actions and self.is_enabled(tool.name):
                actions.extend(tool.file_actions)
        return actions

    def get_tool(self, tool_name: str) -> "ToolDef | None":
        """按名称取工具定义，未注册返回 None。"""
        return self._tools.get(tool_name)

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
            output=ToolOutputDefinition(schema=GROUPS_OUTPUT),
        )
        self.add_method(
            tool_name="tool_search",
            method_name="search",
            description=(
                "按关键词搜索工具，匹配工具名、方法名、描述与分组名。"
                "多个关键词用空格分开，返回命中全部关键词的工具；都不满足时退回命中任一关键词的工具。"
                "搜索返回的分组会在后续请求中自动变为可用。"
                "命中后必须直接调用返回的函数，不要为同一件事重复搜索。"
                "单次最多返回 5 个工具；无匹配时返回全部分组清单，请换个词再搜。"
                "提示：要浏览全部工具用 list_groups。"
            ),
            handler=self._tool_search_search,
            args={
                "keyword": {
                    "type": "str",
                    "description": "一个或多个关键词，用空格分隔，如 '文件'、'天气 提醒'",
                }
            },
            output=ToolOutputDefinition(schema=SEARCH_OUTPUT),
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
                      group="default", meta=True, side_effect=True)
        self.add_method(
            tool_name="food",
            method_name="spawn",
            description=(
                "在肚子饿了的时候调用，在桌面随机位置生成食物，"
                "返回需要移动的水平距离 dx、方向 direction 和建议跳高 bounce_height；"
                "用 walk/drive 接近、bounce 原地跳起来吃，走到附近自动开吃；已有食物时不重复生成。"
            ),
            handler=self._food_spawn,
            output=ToolOutputDefinition(schema=FOOD_OUTPUT),
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
            output=ToolOutputDefinition(schema=FOOD_OUTPUT),
        )

    def _food_spawn(self, food_type: str = None) -> dict:
        """food__spawn 工具实现（懒 import，避免注册表依赖行为层）。"""
        from pet.food.food import FOOD
        return FOOD.spawn(food_type)

    def _food_status(self) -> dict:
        """food__status 工具实现。"""
        from pet.food.food import FOOD
        return FOOD.status()

    def _register_game_tools(self):
        """注册游戏元工具：回合制游戏，模型通过 game__play 连续调用游玩。"""
        self.register("game", "游戏：玩回合制小游戏，如猜数字",
                      group="default", meta=True, side_effect=True)
        self.add_method(
            tool_name="game",
            method_name="list",
            description=(
                "列出当前可玩的游戏及规则。想玩游戏时先调用此方法了解可选游戏。"
            ),
            handler=self._game_list,
            output=ToolOutputDefinition(schema=GAME_OUTPUT),
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
            output=ToolOutputDefinition(schema=GAME_OUTPUT),
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
            output=ToolOutputDefinition(schema=GAME_OUTPUT),
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
            output=ToolOutputDefinition(schema=GAME_OUTPUT),
        )

    def _register_recall_tools(self):
        """注册记忆检索元工具（只读）：search 语义回忆，browse 分页翻阅。"""
        self.register(
            "recall",
            "回忆：主动检索你关于用户的长期记忆。"
            "系统每轮自动注入的记忆条数有限；当你感觉记忆不完整、想不起细节，"
            "或看到/听到的线索（画面里的人、物、地点、照片、应用，对话中的人名、事件、话题）"
            "与用户有关、而注入段中没有时，主动用此工具补齐。",
            group="default", meta=True,
        )
        self.add_method(
            tool_name="recall",
            method_name="search",
            description=(
                "按语义或关键词回忆与线索相关的记忆。以下情况应主动调用："
                "① 线索似曾相识——画面里的人/物/地点/照片/应用，或窗口标题里的应用名、文档名、项目名——"
                "想确认是否与用户有关；"
                "② 用户提到过去的人/事/地点，而注入的记忆段中没有；"
                "③ 想不起细节，或用户问『你还记得…吗』『我之前说过什么』。"
                "结果直接使用即可，同一话题无需重复检索；想起来的记忆本来就在库里，自然说出来就行，"
                "不必再输出 Memory 行。"
                "没检索到就是你没有这段记忆，如实说想不起来，不要顺着话题编。"
            ),
            handler=self._recall_search,
            args={
                "query": {
                    "type": "str",
                    "required": True,
                    "desc": "回忆的线索：画面或对话中的关键词，如人名、地点、事件、话题、应用名",
                },
                "limit": {
                    "type": "int",
                    "required": False,
                    "default": 5,
                    "desc": "返回条数(1~10)",
                },
            },
            timeout=30.0,
            output=ToolOutputDefinition(schema=RECALL_OUTPUT),
        )
        self.add_method(
            tool_name="recall",
            method_name="browse",
            description=(
                "分页翻阅你关于用户的全部记忆，可按创建日期范围/关键词筛选。"
                "日期均为 YYYY-MM-DD 且含首尾两天，可只填一端表示不限。"
                "想回忆某一天或某段时间发生了什么、或想按时间顺序把记过的东西过一遍时用它；"
                "按具体线索（人名/地点/话题）查记忆用 recall__search。"
                "没翻阅到就是你没有那段记忆，如实说想不起来，不要编。"
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
            output=ToolOutputDefinition(schema=RECALL_OUTPUT),
        )

    @staticmethod
    def _empty_recall(msg: str) -> dict:
        """recall__browse 的空结果（含参数错误提示）。"""
        return {"error": msg}

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
    def _format_recall_memory(row: dict) -> str:
        """将记忆行渲染成一行可读文本。"""
        # 模块级导入会经 pet.brain.__init__ → behavior 回指本模块成环，保持函数内导入
        from pet.brain.memory import get_memory_store
        time_str = get_memory_store().format_memory_time(row.get("created_at", ""))
        level = row.get("level", "L2")
        return f"[{time_str}]（{level}）{row.get('content', '')}"

    def _recall_search(self, query: str, limit: int = 5) -> dict:
        """recall__search 工具实现。"""
        from pet.brain.memory import get_memory_store
        TOOL_CTX.speech_random(["让我想想…", "回忆一下…", "翻翻记忆…"])
        query = (query or "").strip()
        if not query:
            return {"error": "回忆线索为空，请提供人名、事件或话题关键词"}
        try:
            limit = max(1, min(int(limit), 10))
        except (TypeError, ValueError):
            limit = 5
        results = get_memory_store().search_memories(query, limit=limit)
        if not results:
            return {"headline": f"没有想起与「{query}」相关的记忆"}
        return {
            "headline": f"想起 {len(results)} 条与「{query}」相关的记忆",
            "body": "\n".join(self._format_recall_memory(r) for r in results),
        }

    def _recall_browse(self, start_date: str = "", end_date: str = "",
                       keyword: str = "", page: int = 1, **_) -> dict:
        """recall__browse 工具实现。

        **_ 吞掉模型多传的未声明参数：executor 会把 schema 外的键原样透传给
        handler，缺少它时一个多余参数就会让整次调用 TypeError 失败。
        """
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
                return {"headline": f"没有符合条件的记忆{filter_str}"}
            # 页码越界：明确告知范围，避免模型误判为「没有记忆」
            return {
                "headline": (f"第 {page} 页超出范围：共 {total} 条记忆，"
                             f"仅 {total_pages} 页{filter_str}"),
            }
        memories = [self._format_recall_memory(r) for r in rows]
        return {
            "headline": f"共 {total} 条记忆，第 {page}/{total_pages} 页{filter_str}",
            "body": "\n".join(memories),
            "pagination": {"page": page, "page_size": page_size,
                           "total_items": total, "has_next": page < total_pages},
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
        return {
            "headline": f"共 {len(groups)} 个工具分组，{sum(g['tool_count'] for g in groups)} 个工具",
            "body": self._format_catalog(groups),
        }

    @staticmethod
    def _tool_search_entry(tool: "ToolDef") -> dict:
        """搜索结果里单个工具的展示结构。"""
        return {
            "name": tool.name,
            "group": tool.group,
            "description": tool.description,
            "methods": [
                {"name": f"{tool.name}__{method.name}", "description": method.description}
                for method in tool.methods.values()
            ],
        }

    def _tool_search_search(self, keyword: str = "") -> dict:
        kw = (keyword or "").strip()
        phrase = _search_normalize(kw)
        terms = _search_terms(kw)
        candidates = [tool for tool in self._tools.values() if tool.group != "default"]
        tools = [tool for tool in candidates if self.is_enabled(tool.name)]
        texts = {tool.name: _tool_search_text(tool) for tool in candidates}

        def _hits(tool) -> bool:
            return bool(phrase and phrase in texts[tool.name])

        def _partial(tool) -> bool:
            return any(term in texts[tool.name] for term in terms)

        matched = [
            tool for tool in tools
            if not kw or _hits(tool) or (terms and all(term in texts[tool.name] for term in terms))
        ]
        if kw and not matched:
            # 未命中全部关键词时退回命中任一关键词
            matched = [tool for tool in tools if _partial(tool)]

        hint = None
        brief = None
        catalog = None
        if kw and not matched:
            closed = [tool for tool in candidates
                      if not self.is_enabled(tool.name) and (_hits(tool) or _partial(tool))]
            if closed:
                names = "、".join(tool.name for tool in closed[:3])
                return {
                    "headline": f"「{keyword}」匹配到已关闭的工具：{names}",
                    "body": self._format_entries(
                        [self._tool_search_entry(tool) for tool in closed]),
                    "hint": (f"「{keyword}」对应的工具 {names} 当前被关闭，"
                                   f"需要提示用户开启。"),
                    "groups": [],
                }
            # 未命中：回全量分组清单（只有名称与描述），让模型换个词再搜
            catalog = self._group_catalog(tools)
            hint = (f"没有工具匹配「{keyword}」。下面按分组列出全部工具，"
                    f"请用其中的工具名或描述里的词重新搜索；"
                    f"这些工具需要先搜索命中才会出现在你的工具列表里。")
            brief = f"未匹配「{keyword}」，已列出全部 {len(tools)} 个工具的分组清单"
        elif kw:
            if len(matched) > _MAX_SEARCH_RESULTS:
                hint = (f"命中 {len(matched)} 个，已列出前 {_MAX_SEARCH_RESULTS} 个；"
                        f"需要更多请换更具体的关键词。")
                matched = matched[:_MAX_SEARCH_RESULTS]
            names = [f"{t.name}__{m.name}" for t in matched[:2] for m in t.methods.values()]
            hint = (hint + " " if hint else "") + (
                "已命中：" + "、".join(names[:4])
                + "。下一轮直接调用这些函数，不要再调用 tool_search。")
            brief = f"搜到 {len(matched)} 个：" + "、".join(t.name for t in matched[:3])

        return {
            "headline": brief or f"列出全部 {len(matched)} 个可用工具",
            "body": (self._format_catalog(catalog) if catalog
                     else self._format_entries(
                         [self._tool_search_entry(tool) for tool in matched])),
            "hint": hint or "",
            "groups": sorted({tool.group for tool in matched if tool.group != "default"}),
        }

    @staticmethod
    def _group_catalog(tools: list) -> list[dict]:
        """全量工具的分组清单：只给名称与描述，不含方法与参数。"""
        groups: dict[str, list] = {}
        for tool in tools:
            groups.setdefault(tool.group, []).append(
                {"name": tool.name, "description": tool.description})
        return [{"group": name, "tools": items} for name, items in groups.items()]

    @staticmethod
    def _format_entries(entries: list[dict]) -> str:
        """命中结果的可读文本：工具一行，方法缩进列出。"""
        lines = []
        for entry in entries:
            lines.append(f"{entry['name']}（{entry['group']}）：{entry['description']}")
            for method in entry.get("methods") or []:
                lines.append(f"  - {method['name']}：{method['description']}")
        return "\n".join(lines)

    @staticmethod
    def _format_catalog(catalog: list[dict]) -> str:
        lines = []
        for group in catalog:
            lines.append(f"[{group['group']}]")
            for tool in group["tools"]:
                lines.append(f"  - {tool['name']}：{tool['description']}")
        return "\n".join(lines)

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


def food_tool_available() -> bool:
    """觅食工具是否已注册且启用；贴边时该工具被禁用。"""
    return TOOL_REGISTRY.get_tool("food") is not None and TOOL_REGISTRY.is_enabled("food")
