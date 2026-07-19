import logging

from pet.tools.browser.core import BrowserTool
from pet.tools.context import TOOL_CTX

logger = logging.getLogger(__name__)

TOOL_NAME = "browser"
TOOL_DESCRIPTION = "浏览器操作（搜索网页、读取网页正文、截图）"
TOOL_GROUP = "web"

_instance = BrowserTool()


def _search(**kw):
    TOOL_CTX.speech_random(["搜一下…", "搜搜看…", "查查看…", "找找…"])
    return _instance.search(**kw)


def _read_url(**kw):
    TOOL_CTX.speech_random(["读一读网页…", "看看写了什么…", "读读看…", "瞄一眼…"])
    return _instance.read_url(**kw)


def _screenshot_url(**kw):
    TOOL_CTX.speech_random(["看看网页…", "瞄一眼…", "瞧瞧…", "看看…"])
    return _instance.screenshot_url(**kw)


def _close():
    return _instance.close()


def _check_playwright() -> None:
    """检查 Playwright 及浏览器二进制是否安装，不满足则抛出异常导致工具加载失败。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "playwright 未安装，请运行: pip install playwright && playwright install chromium"
        )

    try:
        with sync_playwright() as p:
            executable = p.chromium.executable_path
            from pathlib import Path
            if not Path(executable).exists():
                raise RuntimeError(
                    f"Playwright Chromium 浏览器未安装（{executable}），请运行: playwright install chromium"
                )
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(
            f"Playwright 浏览器检测失败: {e}，请运行: playwright install chromium"
        )


def register(registry):
    _check_playwright()

    tool = registry.register(TOOL_NAME, TOOL_DESCRIPTION)

    registry.add_method(
        TOOL_NAME, "search",
        "搜索关键词，返回结构化搜索结果（标题、URL、摘要），最多10条",
        handler=_search,
        timeout=30.0,
        args={
            "query": {"type": "str", "required": True, "desc": "搜索关键词"},
            "count": {"type": "int", "required": False, "desc": "返回结果数量(1-10)", "default": 10},
        },
    )
    registry.add_method(
        TOOL_NAME, "read_url",
        "用无头浏览器打开URL并提取页面正文文本，支持分页（默认每页3000字），返回当前页内容及总页数",
        handler=_read_url,
        timeout=30.0,
        args={
            "url": {"type": "str", "required": True, "desc": "要读取的网页地址（包含 http/https）"},
            "max_chars": {"type": "int", "required": False, "desc": "最大提取字符数", "default": 10000},
            "wait_seconds": {"type": "float", "required": False, "desc": "页面加载等待时间(秒)", "default": 3.0},
            "page": {"type": "int", "required": False, "desc": "分页页码，从1开始", "default": 1},
            "page_size": {"type": "int", "required": False, "desc": "每页字符数", "default": 3000},
        },
    )
    registry.add_method(
        TOOL_NAME, "screenshot_url",
        "用无头浏览器打开URL并截图，可以'看到'网页外观",
        handler=_screenshot_url,
        timeout=30.0,
        args={
            "url": {"type": "str", "required": True, "desc": "要截图的网页地址（包含 http/https）"},
            "width": {"type": "int", "required": False, "desc": "视口宽度(px)", "default": 1280},
            "height": {"type": "int", "required": False, "desc": "视口高度(px)", "default": 800},
            "wait_seconds": {"type": "float", "required": False, "desc": "页面加载等待时间(秒)", "default": 3.0},
            "full_page": {"type": "bool", "required": False, "desc": "是否截取整页（默认仅可视区域）", "default": False},
        },
    )
    registry.add_method(
        TOOL_NAME, "close",
        "关闭浏览器，释放内存（所有网页操作完成后调用）",
        handler=_close,
    )
