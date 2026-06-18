"""浏览器操作核心逻辑。"""

import base64
import logging
import webbrowser

logger = logging.getLogger(__name__)


class BrowserTool:
    def open_url(self, url: str) -> dict:
        """打开指定网址。"""
        try:
            webbrowser.open(url)
        except webbrowser.Error as e:
            logger.error(f"[BrowserTool] open_url failed: {e}")
            return {"error": f"无法打开浏览器: {e}", "url": url}
        return {"status": "opened", "url": url}

    def search(self, query: str) -> dict:
        """用默认浏览器搜索。"""
        url = f"https://www.bing.com/search?q={query}"
        try:
            webbrowser.open(url)
        except webbrowser.Error as e:
            logger.error(f"[BrowserTool] search failed: {e}")
            return {"error": f"无法打开浏览器: {e}", "query": query}
        return {"status": "searching", "query": query, "url": url}

    def screenshot_url(self, url: str, width: int = 1280, height: int = 800,
                       wait_seconds: float = 3.0) -> dict:
        """用无头浏览器打开 URL 并截图，返回 base64 图片。

        通过 __image__ 旁路将截图传给 LLM 多模态上下文，
        __image_mime__ 指定 MIME 类型。
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return {"error": "playwright 未安装，请运行: pip install playwright && playwright install chromium"}

        browser = None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": width, "height": height})
                page.goto(url, timeout=20000, wait_until="domcontentloaded")
                page.wait_for_timeout(int(wait_seconds * 1000))
                screenshot_bytes = page.screenshot(full_page=False, type="jpeg", quality=80)
                browser.close()
                browser = None

                img_b64 = base64.b64encode(screenshot_bytes).decode("ascii")
                logger.info(f"[BrowserTool] screenshot_url: {url} → {len(screenshot_bytes)} bytes JPEG")
                return {
                    "status": "captured",
                    "url": url,
                    "size": f"{width}x{height}",
                    "__image__": img_b64,
                    "__image_mime__": "image/jpeg",
                }
        except Exception as e:
            logger.error(f"[BrowserTool] screenshot_url failed: {e}")
            return {"error": f"截图失败: {e}", "url": url}
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
