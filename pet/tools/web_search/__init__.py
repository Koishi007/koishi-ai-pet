"""支持 SearXNG（自建）和 Bing Web Search API。"""

from pet.tools.web_search.core import search, deep_search, check_connectivity
from pet.tools.context import TOOL_CTX

TOOL_NAME = "web_search"
TOOL_DESCRIPTION = "网络搜索，获取实时信息、新闻、百科知识"


def _search(**kw):
    TOOL_CTX.speech_random(["搜搜看…", "查查看…", "找找看…", "搜一下…"])
    return search(**kw)


def _deep_search(**kw):
    TOOL_CTX.speech_random(["搜搜看…", "查查看…", "找找看…", "搜一下…"])
    return deep_search(**kw)


def register(registry):
    if not check_connectivity():
        raise RuntimeError("web_search: 所有搜索后端均不可达，跳过加载")

    tool = registry.register(TOOL_NAME, TOOL_DESCRIPTION)

    registry.add_method(
        TOOL_NAME, "search",
        "搜索网络获取最新信息（返回标题和摘要，速度快）",
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
        },
    )
    registry.add_method(
        TOOL_NAME, "deep_search",
        "深度搜索：搜索后自动抓取前几条结果的页面正文，信息更完整详细（比 search 慢）",
        handler=_deep_search,
        timeout=60.0,
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
                "desc": "搜索结果数量（1-10）",
            },
            "language": {
                "type": "str",
                "required": False,
                "default": "zh-CN",
                "desc": "语言/区域代码",
                "enum": ["zh-CN", "en-US", "ja-JP"],
            },
            "extract_top": {
                "type": "int",
                "required": False,
                "default": 2,
                "desc": "抓取正文的结果条数（1-3，越多越慢）",
            },
        },
    )