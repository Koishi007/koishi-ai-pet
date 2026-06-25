import logging
import sys

from pet.tools.browser.core import BrowserTool
from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)

TOOL_NAME = "browser"
TOOL_DESCRIPTION = "浏览器操作（打开网页、搜索、读取网页文本、截图）"

_instance = BrowserTool()


def _open_url(**kw):
    TOOL_CTX.speech_random(["打开看看…", "开一下…", "瞧瞧这个…", "打开…"])
    return _instance.open_url(**kw)


def _search(**kw):
    TOOL_CTX.speech_random(["搜一下…", "搜搜看…", "查查看…", "找找…"])
    return _instance.search(**kw)


def _read_url(**kw):
    TOOL_CTX.speech_random(["读一读网页…", "看看写了什么…", "读读看…", "瞄一眼…"])
    return _instance.read_url(**kw)


def _screenshot_url(**kw):
    TOOL_CTX.speech_random(["看看网页…", "瞄一眼…", "瞧瞧…", "看看…"])
    return _instance.screenshot_url(**kw)


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

    tool = registry.register(TOOL_NAME, TOOL_DESCRIPTION)

    registry.add_method(
        TOOL_NAME, "open_url",
        "用默认浏览器打开指定URL（仅帮用户在浏览器中打开，不会读取页面内容）",
        handler=_open_url,
        args={
            "url": {"type": "str", "required": True, "desc": "要打开的网址（包含 http/https）"},
        },
    )
    registry.add_method(
        TOOL_NAME, "search",
        "用默认浏览器打开搜索页面（仅帮用户打开浏览器搜索，不会获取搜索结果）",
        handler=_search,
        args={
            "query": {"type": "str", "required": True, "desc": "搜索关键词"},
        },
    )

    if has_pw:
        registry.add_method(
            TOOL_NAME, "read_url",
            "用无头浏览器打开URL并提取页面正文文本，能获取完整内容（包括需要滚动才能看到的部分）",
            handler=_read_url,
            timeout=30.0,
            args={
                "url": {"type": "str", "required": True, "desc": "要读取的网页地址（包含 http/https）"},
                "max_chars": {"type": "int", "required": False, "desc": "最大返回字符数", "default": 8000},
                "wait_seconds": {"type": "float", "required": False, "desc": "页面加载等待时间(秒)", "default": 3.0},
            },
        )
        registry.add_method(
            TOOL_NAME, "screenshot_url",
            "用无头浏览器打开URL并截图，可以\"看到\"网页外观",
            handler=_screenshot_url,
            timeout=30.0,
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
