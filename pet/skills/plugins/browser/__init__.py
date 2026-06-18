"""浏览器操作插件"""

from pet.skills.plugins.browser.core import BrowserTool

SKILL_NAME = "browser"
SKILL_DESCRIPTION = "浏览器操作（打开网页、搜索、截图查看网页内容）"

_instance = BrowserTool()


def register(registry):
    """插件接口 — 由 SkillLoader 调用。"""
    skill = registry.register(SKILL_NAME, SKILL_DESCRIPTION)
    skill.when = "用户让你打开某个网站、帮他用浏览器搜索、或需要查看网页内容时；如果其他技能的摘要结果不够详细，可用 screenshot_url 截图查看网页详情"

    registry.add_method(
        SKILL_NAME, "open_url",
        "用默认浏览器打开指定URL（仅帮用户在浏览器中打开，不会读取页面内容）",
        handler=_instance.open_url,
        when="用户明确要求\"打开xxx网站\"\"帮我在浏览器打开这个链接\"时",
        args={
            "url": {"type": "str", "required": True, "desc": "要打开的网址（包含 http/https）"},
        },
    )
    registry.add_method(
        SKILL_NAME, "search",
        "用默认浏览器打开搜索页面（仅帮用户打开浏览器搜索，不会获取搜索结果）",
        handler=_instance.search,
        when="用户明确要求\"用浏览器搜一下\"\"打开浏览器搜xxx\"时",
        args={
            "query": {"type": "str", "required": True, "desc": "搜索关键词"},
        },
    )
    registry.add_method(
        SKILL_NAME, "screenshot_url",
        "用无头浏览器打开URL并截图，可以\"看到\"网页内容并分析",
        handler=_instance.screenshot_url,
        when="需要查看某个网页的具体内容（如读取搜索结果页面的详情、查看文章等）时",
        args={
            "url": {"type": "str", "required": True, "desc": "要截图的网页地址（包含 http/https）"},
            "width": {"type": "int", "required": False, "desc": "视口宽度(px)", "default": 1280},
            "height": {"type": "int", "required": False, "desc": "视口高度(px)", "default": 800},
            "wait_seconds": {"type": "float", "required": False, "desc": "页面加载等待时间(秒)", "default": 3.0},
        },
    )
