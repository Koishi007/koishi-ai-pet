"""浏览器操作工具 — 打开URL、搜索。"""

import logging
import webbrowser

logger = logging.getLogger(__name__)

SKILL_NAME = "browser"
SKILL_DESCRIPTION = "浏览器操作（打开网页、搜索）"


class BrowserTool:
    def open_url(self, url: str) -> dict:
        """打开指定网址。"""
        webbrowser.open(url)
        return {"status": "opened", "url": url}

    def search(self, query: str) -> dict:
        """用默认浏览器搜索。"""
        url = f"https://www.bing.com/search?q={query}"
        webbrowser.open(url)
        return {"status": "searching", "query": query, "url": url}


_instance = BrowserTool()


def register(registry):
    """插件接口 — 由 SkillLoader 调用。"""
    registry.register(SKILL_NAME, SKILL_DESCRIPTION)
    registry.add_method(
        SKILL_NAME, "open_url",
        "用默认浏览器打开指定URL",
        handler=_instance.open_url,
        args={"url": "要打开的网址"},
    )
    registry.add_method(
        SKILL_NAME, "search",
        "用默认浏览器搜索关键词",
        handler=_instance.search,
        args={"query": "搜索关键词"},
    )
