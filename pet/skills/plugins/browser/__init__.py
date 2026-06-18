import logging
import sys

from pet.skills.plugins.browser.core import BrowserTool

logger = logging.getLogger(__name__)

SKILL_NAME = "browser"
SKILL_DESCRIPTION = "浏览器操作（打开网页、搜索、读取网页文本、截图）"

_instance = BrowserTool()


def _has_playwright_browsers() -> bool:
    """快速检测 Playwright 浏览器二进制是否已安装（不启动浏览器）。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            executable = p.chromium.executable_path
            from pathlib import Path
            return Path(executable).exists()
    except Exception:
        return False


def register(registry):
    has_pw = _has_playwright_browsers()

    skill = registry.register(SKILL_NAME, SKILL_DESCRIPTION)
    skill.when = "用户让你打开网站、搜索、或需要查看网页内容时；read_url 读取完整文本（含滚动内容），screenshot_url 截图看外观"

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

    if has_pw:
        registry.add_method(
            SKILL_NAME, "read_url",
            "用无头浏览器打开URL并提取页面正文文本，能获取完整内容（包括需要滚动才能看到的部分）",
            handler=_instance.read_url,
            when="需要阅读网页的完整文字内容时，特别是页面较长需要滚动、截图看不到关键信息的情况",
            args={
                "url": {"type": "str", "required": True, "desc": "要读取的网页地址（包含 http/https）"},
                "max_chars": {"type": "int", "required": False, "desc": "最大返回字符数", "default": 8000},
                "wait_seconds": {"type": "float", "required": False, "desc": "页面加载等待时间(秒)", "default": 3.0},
            },
        )
        registry.add_method(
            SKILL_NAME, "screenshot_url",
            "用无头浏览器打开URL并截图，可以\"看到\"网页外观",
            handler=_instance.screenshot_url,
            when="需要查看网页的外观、布局、图片等视觉内容时",
            args={
                "url": {"type": "str", "required": True, "desc": "要截图的网页地址（包含 http/https）"},
                "width": {"type": "int", "required": False, "desc": "视口宽度(px)", "default": 1280},
                "height": {"type": "int", "required": False, "desc": "视口高度(px)", "default": 800},
                "wait_seconds": {"type": "float", "required": False, "desc": "页面加载等待时间(秒)", "default": 3.0},
            },
        )
    else:
        logger.warning(
            "[BrowserPlugin] Playwright browsers not installed. "
            "Run: pip install playwright && playwright install chromium"
        )
