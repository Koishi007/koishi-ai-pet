"""网络搜索插件 — 支持 SearXNG（自建）和 Bing Web Search API。"""

from pet.skills.plugins.web_search.core import search, check_connectivity

SKILL_NAME = "web_search"
SKILL_DESCRIPTION = "网络搜索，查询时事新闻、实时信息、百科知识（支持 SearXNG 自建实例或 Bing API）"


def register(registry):
    """插件接口 — 由 SkillLoader（后台线程）调用。"""
    # 连通性检测（同步，但在 loader 后台线程中，不阻塞主线程）
    if not check_connectivity():
        raise RuntimeError("web_search: 所有搜索后端均不可达，跳过加载")

    registry.register(SKILL_NAME, SKILL_DESCRIPTION)

    registry.add_method(
        SKILL_NAME, "search",
        "搜索时事新闻、实时信息、百科知识",
        handler=search,
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
            },
        },
    )