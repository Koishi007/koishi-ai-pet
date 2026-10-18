"""支持 SearXNG（自建）、DeepSeek 搜索 API、Tavily 和 Bing Web Search API。"""

from pet.tools.web_search.core import search, fetch, check_connectivity
from pet.tools.context import TOOL_CTX

TOOL_NAME = "web_search"
TOOL_DESCRIPTION = "网络搜索与网页抓取 API（SearXNG / DeepSeek / Tavily / Bing）：搜索直连服务商，抓取网页转纯文本"
TOOL_GROUP = "web"


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
    )
    registry.add_method(
        TOOL_NAME, "fetch",
        "抓取指定 URL 的网页正文并转为纯文本（优先 DeepSeek Fetch，失败回退本地提取），不支持 JS 渲染页面",
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
    )