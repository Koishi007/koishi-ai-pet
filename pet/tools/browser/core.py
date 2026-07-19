import base64
import json
import logging
import re
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)

_TOOL_DIR = Path(__file__).parent
_CONFIG_FILE = _TOOL_DIR / "config.json"

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# 安全参数（仅本地/受控环境推荐；对外暴露服务时可通过 config 关闭）
_BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]

try:
    from playwright_stealth import Stealth
    _HAS_STEALTH = True
except ImportError:
    _HAS_STEALTH = False
    Stealth = None


def _load_config() -> dict:
    if _CONFIG_FILE.is_file():
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[BrowserTool] Failed to read config.json: {e}")
    return {}


_cfg = _load_config()

_SEARCH_ENGINES = {
    "duckduckgo": {
        "url": "https://html.duckduckgo.com/html/?q={query}",
        "timeout": 30000,
        "extract_js": """() => {
            const items = [];
            document.querySelectorAll('.result').forEach(node => {
                const titleEl = node.querySelector('.result__a');
                const snippetEl = node.querySelector('.result__snippet');
                if (!titleEl) return;
                const href = titleEl.href || '';
                if (!href) return;
                items.push({
                    title: titleEl.innerText.trim(),
                    url: href,
                    snippet: snippetEl ? snippetEl.innerText.trim() : '',
                });
            });
            return items;
        }""",
    },
    "bing": {
        "url": "https://www.bing.com/search?q={query}",
        "simulate_human": True,
        "homepage": "https://www.bing.com/",
        "input_selector": "#sb_form_q",
        "timeout": 30000,
        "wait_selector": "#b_results",
        "wait_items_selector": "#b_results .b_algo",
        "extract_js": """() => {
            const items = [];
            document.querySelectorAll('#b_results .b_algo').forEach(node => {
                const titleEl = node.querySelector('h2 a');
                if (!titleEl) return;
                const href = titleEl.href || '';
                if (!href) return;
                const snippetEls = node.querySelectorAll('.b_caption p, .b_lineclamp2');
                const snippet = Array.from(snippetEls).map(e => e.innerText.trim()).filter(t => t).join(' ');
                items.push({ title: titleEl.innerText.trim(), url: href, snippet: snippet });
            });
            return items;
        }""",
    },
    "baidu": {
        "url": "https://www.baidu.com/s?wd={query}",
        "simulate_human": True,
        "homepage": "https://www.baidu.com/",
        "input_selector": "#kw",
        "timeout": 30000,
        "wait_selector": "#content_left",
        "wait_items_selector": "#content_left .result h3 a, #content_left .c-container h3 a",
        "extract_js": """() => {
            const items = [];
            const results = document.querySelectorAll('#content_left .result, #content_left .c-container');
            results.forEach(node => {
                const titleEl = node.querySelector('h3 a');
                if (!titleEl) return;
                const href = titleEl.href || '';
                if (!href || href === '#') return;
                const snippetEl = node.querySelector('.c-abstract, .c-span-last, .content-right_8Zs40');
                items.push({
                    title: titleEl.innerText.trim(),
                    url: href,
                    snippet: snippetEl ? snippetEl.innerText.trim() : '',
                });
            });
            return items;
        }""",
    },
    "sogou": {
        "url": "https://www.sogou.com/web?query={query}",
        "simulate_human": True,
        "homepage": "https://www.sogou.com/",
        "input_selector": "#query",
        "timeout": 30000,
        "wait_selector": ".results",
        "wait_items_selector": ".results .rb h3 a, .results .vrwrap h3 a",
        "extract_js": """() => {
            const items = [];
            document.querySelectorAll('.rb, .vrwrap, .result').forEach(node => {
                const titleEl = node.querySelector('h3 a');
                if (!titleEl) return;
                const href = titleEl.href || '';
                if (!href || href === '#') return;
                const snippetEl = node.querySelector('.str-text, .str_info, .star-wiki, .fb');
                items.push({
                    title: titleEl.innerText.trim(),
                    url: href,
                    snippet: snippetEl ? snippetEl.innerText.trim() : '',
                });
            });
            return items;
        }""",
    },
}


class BrowserTool:

    @staticmethod
    def _get_playwright_sync():
        try:
            from playwright.sync_api import sync_playwright
            return sync_playwright
        except ImportError:
            return None

    @property
    def _headless(self) -> bool:
        return _cfg.get("headless", True)

    @property
    def _user_agent(self) -> str:
        return _cfg.get("user_agent", _DEFAULT_UA)

    @property
    def _engine(self) -> str:
        return _cfg.get("search_engine", "bing")

    @staticmethod
    def _validate_url(url: str, label: str = "URL") -> str | None:
        if not url:
            return f"{label}为空"
        if not _URL_RE.match(url):
            return f"{label}只允许 http/https 协议: {url}"
        return None


    def _make_context(self, pw, viewport: dict | None = None):
        """启动浏览器 + 创建隔离 Context + stealth 反爬注入。"""
        browser = pw.chromium.launch(headless=self._headless, args=_BROWSER_ARGS)
        ctx = browser.new_context(
            user_agent=self._user_agent,
            locale="zh-CN",
            viewport=viewport or {"width": 1366, "height": 900},
            ignore_https_errors=True,
        )
        self._stealth_context(ctx)
        return browser, ctx

    @staticmethod
    def _stealth_context(ctx):
        """对 Context 注入 stealth 补丁（覆盖 webdriver/plugins/WebGL/media codecs 等）。"""
        if _HAS_STEALTH:
            Stealth().hook_playwright_context(ctx)
        else:
            logger.warning("[BrowserTool] playwright-stealth 未安装，反爬能力受限")


    def search(self, query: str, count: int = 10) -> dict:
        engine = _SEARCH_ENGINES.get(self._engine)
        if not engine:
            return {"error": f"未知搜索引擎: {self._engine}，可选: {', '.join(_SEARCH_ENGINES)}"}

        sync_pw = self._get_playwright_sync()
        if not sync_pw:
            return {"error": "playwright 未安装，请运行: pip install playwright && playwright install chromium"}

        url = engine["url"].format(query=quote(query))
        wait_sel = engine.get("wait_selector")
        wait_items = engine.get("wait_items_selector")
        extract_js = engine["extract_js"]
        goto_timeout = engine["timeout"]

        try:
            with sync_pw() as pw:
                _browser, ctx = self._make_context(pw)
                page = ctx.new_page()

                if engine.get("simulate_human"):
                    logger.debug(f"[BrowserTool] simulate_human: goto {engine['homepage']}")
                    page.goto(engine["homepage"], timeout=goto_timeout, wait_until="domcontentloaded")
                    sel = engine["input_selector"]
                    logger.debug(f"[BrowserTool] simulate_human: fill '{sel}'")
                    page.fill(sel, query)
                    page.press(sel, "Enter")
                else:
                    page.goto(url, timeout=goto_timeout, wait_until="domcontentloaded")

                if wait_sel:
                    try:
                        page.wait_for_selector(wait_sel, timeout=15000)
                    except Exception:
                        pass
                else:
                    page.wait_for_timeout(2000)

                page.wait_for_timeout(1500)

                if wait_items:
                    try:
                        page.wait_for_selector(wait_items, timeout=10000)
                    except Exception:
                        pass

                page.wait_for_timeout(500)
                results = page.evaluate(extract_js)

            results = (results or [])[:count]
            logger.info(f"[BrowserTool] search({self._engine}): '{query}' → {len(results)} results")

            summary_parts = []
            for i, r in enumerate(results, 1):
                summary_parts.append(f"{i}. {r['title']}\n   {r['url']}\n   {r.get('snippet','')}")

            return {
                "status": "success",
                "query": query,
                "engine": self._engine,
                "results": results,
                "summary": "\n\n".join(summary_parts),
                "__context__": f"搜索「{query}」获得 {len(results)} 条结果",
            }
        except Exception as e:
            logger.error(f"[BrowserTool] search failed: {e}")
            return {"error": f"搜索失败: {e}", "query": query}

    def read_url(self, url: str, max_chars: int = 10000,
                 wait_seconds: float = 3.0, page: int = 1,
                 page_size: int = 3000) -> dict:
        err = self._validate_url(url)
        if err:
            return {"error": err}

        sync_pw = self._get_playwright_sync()
        if not sync_pw:
            return {"error": "playwright 未安装，请运行: pip install playwright && playwright install chromium"}

        try:
            with sync_pw() as pw:
                _browser, ctx = self._make_context(pw)
                pg = ctx.new_page()
                pg.goto(url, timeout=20000, wait_until="domcontentloaded")
                pg.wait_for_timeout(int(wait_seconds * 1000))
                title = pg.title()
                raw_text = pg.evaluate("""() => {
                    const sel = document.querySelector('article, main, [role="main"]');
                    const root = sel || document.body;
                    const clone = root.cloneNode(true);
                    clone.querySelectorAll('script,style,nav,footer,header,aside,form,iframe').forEach(e => e.remove());
                    return clone.innerText;
                }""")

            raw_text = (raw_text or "").strip()
            truncated = len(raw_text) > max_chars
            full_text = raw_text[:max_chars]
            total_chars = len(full_text)

            if page < 1:
                page = 1
            start = (page - 1) * page_size
            end = start + page_size
            text = full_text[start:end] if start < total_chars else ""
            has_next = end < total_chars
            total_pages = (total_chars + page_size - 1) // page_size if total_chars > 0 else 1

            logger.info(f"[BrowserTool] read_url: {url} → page {page}/{total_pages} ({len(text)} chars)")
            return {
                "status": "success",
                "url": url,
                "title": title,
                "text": text,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "total_chars": total_chars,
                "truncated": truncated,
                "has_next": has_next,
                "__context__": f"读取网页 {url}「{title}」第{page}/{total_pages}页（{len(text)}字符）",
            }
        except Exception as e:
            logger.error(f"[BrowserTool] read_url failed: {e}")
            return {"error": f"读取失败: {e}", "url": url}

    def screenshot_url(self, url: str, width: int = 1280, height: int = 800,
                       wait_seconds: float = 3.0, full_page: bool = False) -> dict:
        err = self._validate_url(url)
        if err:
            return {"error": err}

        sync_pw = self._get_playwright_sync()
        if not sync_pw:
            return {"error": "playwright 未安装，请运行: pip install playwright && playwright install chromium"}

        try:
            with sync_pw() as pw:
                _browser, ctx = self._make_context(pw, viewport={"width": width, "height": height})
                pg = ctx.new_page()
                pg.goto(url, timeout=20000, wait_until="domcontentloaded")
                pg.wait_for_timeout(int(wait_seconds * 1000))
                screenshot_bytes = pg.screenshot(full_page=full_page, type="jpeg", quality=80)

            img_b64 = base64.b64encode(screenshot_bytes).decode("ascii")
            logger.info(f"[BrowserTool] screenshot_url: {url} → {len(screenshot_bytes)} bytes JPEG")
            return {
                "status": "success",
                "url": url,
                "size": f"{width}x{height}",
                "full_page": full_page,
                "__image__": img_b64,
                "__image_mime__": "image/jpeg",
                "__context__": f"截图网页 {url}",
            }
        except Exception as e:
            logger.error(f"[BrowserTool] screenshot_url failed: {e}")
            return {"error": f"截图失败: {e}", "url": url}
