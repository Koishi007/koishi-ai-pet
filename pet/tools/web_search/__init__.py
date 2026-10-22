"""支持 SearXNG（自建）、DeepSeek 搜索 API、Tavily 和 Bing Web Search API。"""

from pet.tools.web_search.core import search, fetch, check_connectivity
from pet.tools.context import TOOL_CTX
from pet.tools.output import PAGINATION_SCHEMA, ToolOutputDefinition

TOOL_NAME = "web_search"
TOOL_DESCRIPTION = "网络搜索与网页抓取：搜索直接请求搜索服务返回标题与摘要，抓取网页正文转为纯文本"
TOOL_GROUP = "web"

SEARCH_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "x-role": "headline",
                     "description": "搜索词与结果条数"},
        "body": {"type": "string", "x-role": "body",
                 "description": "每条三行：标题、摘要、网址"},
        "pagination": {**PAGINATION_SCHEMA},
    },
}

FETCH_OUTPUT = {
    "type": "object",
    "required": ["headline"],
    "properties": {
        "headline": {"type": "string", "x-role": "headline",
                     "description": "网页标题与字符数"},
        "body": {"type": "string", "x-role": "body", "description": "正文纯文本"},
    },
}


def _search(**kw):
    TOOL_CTX.speech_random(["搜搜看…", "查查看…", "找找看…", "搜一下…"])
    return search(**kw)


def _fetch(**kw):
    TOOL_CTX.speech_random(["读一读网页…", "看看写了什么…", "读读看…", "瞄一眼…"])
    return fetch(**kw)


def register(registry):
    if not check_connectivity():
        raise RuntimeError("web_search: 所有搜索后端均不可达，跳过加载")

    tool = registry.register(TOOL_NAME, TOOL_DESCRIPTION)

    registry.add_method(
        TOOL_NAME, "search",
        "搜索网络获取最新信息（返回标题和摘要），直接请求搜索 API，速度快",
        handler=_search,
        timeout=15.0,
        args={
            "query": {
                "type": "str",
                "required": True,
                "desc": "搜索关键词",
            },
            "count": {
                "type": "int",
                "required": False,
                "default": 5,
                "desc": "返回结果数量（1-10）",
            },
            "language": {
                "type": "str",
                "required": False,
                "default": "zh-CN",
                "desc": "语言/区域代码（zh-CN / en-US / ja-JP 等）",
                "enum": ["zh-CN", "en-US", "ja-JP"],
            },
            "page": {
                "type": "int",
                "required": False,
                "default": 1,
                "desc": "页码，从1开始（结果不够时翻页获取更多）",
            },
        },
        output=ToolOutputDefinition(schema=SEARCH_OUTPUT),
    )
    registry.add_method(
        TOOL_NAME, "fetch",
        "抓取指定 URL 的网页正文并转为纯文本，不支持 JS 渲染页面",
        handler=_fetch,
        timeout=30.0,
        args={
            "url": {
                "type": "str",
                "required": True,
                "desc": "要抓取的网页地址（含 http:// 或 https://）",
            },
            "max_chars": {
                "type": "int",
                "required": False,
                "default": 10000,
                "desc": "正文最大字符数",
            },
        },
        output=ToolOutputDefinition(schema=FETCH_OUTPUT),
    )