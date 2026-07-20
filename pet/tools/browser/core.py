import base64
import json
import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

logger = logging.getLogger(__name__)

_TOOL_DIR = Path(__file__).parent
_CONFIG_FILE = _TOOL_DIR / "config.json"

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def _load_config() -> dict:
    if _CONFIG_FILE.is_file():
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[BrowserTool] Failed to read config.json: {e}")
    return {}


_cfg = _load_config()

# 反检测注入脚本：隐藏 webdriver、伪造 chrome.runtime、拟真 plugins/permissions
_STEALTH_INIT_JS = r"""
(() => {
  try {
    Object.defineProperty(Navigator.prototype, 'webdriver', {
      get: () => false, configurable: true,
    });
  } catch (e) {}
  if (!window.chrome) {
    window.chrome = {};
  }
  if (!window.chrome.runtime) {
    const noop = () => {};
    const fakeEvent = { addListener: noop, removeListener: noop, hasListener: () => false };
    window.chrome.runtime = {
      onConnect: fakeEvent,
      onMessage: fakeEvent,
      connect: noop,
      sendMessage: noop,
      PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' },
      PlatformArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
      PlatformNaclArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
      RequestUpdateCheckStatus: { THROTTLED: 'throttled', NO_UPDATE: 'no_update', UPDATE_AVAILABLE: 'update_available' },
    };
  }
  const fakePluginArray = (plugins) => {
    const arr = plugins.slice();
    arr.length = plugins.length;
    arr.item = (i) => plugins[i] || null;
    arr.namedItem = (name) => plugins.find(p => p.name === name) || null;
    arr.refresh = () => {};
    return arr;
  };
  const makePlugin = (name, filename, desc) => {
    const p = { name, filename, description: desc, length: 1 };
    p[0] = { type: 'application/pdf', suffixes: 'pdf', description: desc };
    p.item = (i) => p[i] || null;
    p.namedItem = (n) => p[n] || null;
    return p;
  };
  const plugins = [
    makePlugin('PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
    makePlugin('Chrome PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
    makePlugin('Chromium PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
    makePlugin('Microsoft Edge PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
    makePlugin('WebKit built-in PDF', 'internal-pdf-viewer', 'Portable Document Format'),
  ];
  try {
    Object.defineProperty(navigator, 'plugins', {
      get: () => fakePluginArray(plugins), configurable: true,
    });
  } catch (e) {}
  try {
    Object.defineProperty(navigator, 'languages', {
      get: () => ['zh-CN', 'zh', 'en-US', 'en'], configurable: true,
    });
  } catch (e) {}
  if (navigator.permissions && navigator.permissions.query) {
    const origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (params) =>
      params && params.name === 'notifications'
        ? Promise.resolve({ state: 'prompt', onchange: null })
        : origQuery(params);
  }
})();
"""

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
    """浏览器工具：每次调用冷启动 Chromium，用完即关。
    串行化（_lock）保证 Playwright 不被并发访问。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._pw = None        # 实例级，供 close() / atexit 兜底
        self._browser = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # ─── 配置属性 ───

    @staticmethod
    def _get_playwright_sync():
        try:
            from playwright.sync_api import sync_playwright as _sync_pw
            return _sync_pw
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

    @property
    def _ignore_cert(self) -> bool:
        return _cfg.get("ignore_https_errors", False)

    @property
    def _no_sandbox(self) -> bool:
        return _cfg.get("no_sandbox", False)

    def _validate_url(self, url: str, label: str = "URL") -> str | None:
        if not url:
            return f"{label}为空"
        parsed = urlparse(url)
        if parsed.scheme.lower() not in ("http", "https"):
            return f"{label}只允许 http/https 协议: {url}"
        return None

    # ─── 生命周期 ───

    def _acquire(self):
        """启动 Playwright + Chromium，存入实例属性。
        调用方须持有 _lock。"""
        sync_pw = self._get_playwright_sync()
        if not sync_pw:
            raise RuntimeError(
                "playwright 未安装，请运行: pip install playwright && playwright install chromium"
            )
        self._pw = sync_pw().start()

        launch_args = ["--disable-blink-features=AutomationControlled"]
        if self._no_sandbox:
            launch_args.extend(["--no-sandbox", "--disable-dev-shm-usage"])

        self._browser = self._pw.chromium.launch(
            headless=self._headless, args=launch_args
        )
        logger.info(f"[BrowserTool] browser launched (headless={self._headless})")

    def _release(self):
        """关闭 Playwright + Chromium，清理实例属性。
        调用方须持有 _lock。"""
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None

    @contextmanager
    def _session(self):
        """冷启动 → 执行 → 关闭。锁串行化，finally 保证清理。"""
        with self._lock:
            try:
                self._acquire()
                yield self._browser
            finally:
                self._release()

    def close(self):
        """强制关闭（atexit / 应用退出兜底）。
        非阻塞：若浏览器正忙（通常是一次调用未结束），跳过以免 atexit 卡死。"""
        if not self._lock.acquire(timeout=2):
            logger.warning("[BrowserTool] close skipped: browser busy")
            return
        try:
            self._release()
        finally:
            self._lock.release()

    def _new_context(self, browser, viewport: dict | None = None):
        ctx = browser.new_context(
            user_agent=self._user_agent,
            locale="zh-CN",
            viewport=viewport or {"width": 1366, "height": 900},
            ignore_https_errors=self._ignore_cert,
        )
        ctx.add_init_script(_STEALTH_INIT_JS)
        return ctx

    # ─── 搜索 ───

    def _wait_results(self, page, engine, timeout_ms=10000):
        wait_items = engine.get("wait_items_selector")
        if wait_items:
            try:
                page.wait_for_selector(wait_items, timeout=timeout_ms)
                return
            except Exception:
                pass
        w = engine.get("wait_selector")
        if w:
            try:
                page.wait_for_selector(w, timeout=timeout_ms)
                return
            except Exception:
                pass
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass

    def _search_once(self, engine, query, count, browser):
        """单次搜索，browser 由 _session 注入。"""
        url = engine["url"].format(query=quote(query))
        goto_timeout = engine["timeout"]
        extract_js = engine["extract_js"]

        ctx = self._new_context(browser)
        page = None
        try:
            page = ctx.new_page()

            if engine.get("simulate_human"):
                page.goto(engine["homepage"], timeout=goto_timeout, wait_until="domcontentloaded")
                sel = engine["input_selector"]
                page.fill(sel, query)
                page.press(sel, "Enter")
            else:
                page.goto(url, timeout=goto_timeout, wait_until="domcontentloaded")

            self._wait_results(page, engine)
            results = page.evaluate(extract_js)
            results = (results or [])[:count]

            if self._engine == "duckduckgo":
                for r in results:
                    u = r.get("url", "")
                    if "uddg=" in u or "duckduckgo.com/l/" in u:
                        try:
                            qs = parse_qs(urlparse(u).query)
                            if "uddg" in qs:
                                r["url"] = qs["uddg"][0]
                        except Exception:
                            pass

            logger.info(f"[BrowserTool] search({self._engine}): '{query}' → {len(results)} results")
            return results
        finally:
            if page:
                try:
                    page.close()
                except Exception:
                    pass
            try:
                ctx.close()
            except Exception:
                pass

    def search(self, query: str, count: int = 10, max_retries: int = 1) -> dict:
        engine = _SEARCH_ENGINES.get(self._engine)
        if not engine:
            return {"error": f"未知搜索引擎: {self._engine}，可选: {', '.join(_SEARCH_ENGINES)}"}

        last_err = None
        for attempt in range(max_retries):
            try:
                with self._session() as browser:
                    results = self._search_once(engine, query, count, browser)
            except Exception as e:
                last_err = e
                if attempt < max_retries - 1:
                    logger.warning(f"[BrowserTool] search retry {attempt+1}/{max_retries}: {e}")
                    continue
                logger.exception(f"[BrowserTool] search failed after {max_retries} attempts")
                return {"error": f"搜索失败: {e}", "query": query}

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

        raise RuntimeError("unreachable")

    # ─── 读取网页 ───

    def read_url(self, url: str, max_chars: int = 10000,
                 wait_seconds: float = 3.0, page: int = 1,
                 page_size: int = 3000) -> dict:
        err = self._validate_url(url)
        if err:
            return {"error": err}

        try:
            with self._session() as browser:
                ctx = self._new_context(browser)
                pg = None
                title = ""
                raw_text = ""
                try:
                    pg = ctx.new_page()
                    pg.goto(url, timeout=15000, wait_until="domcontentloaded")

                    # 触发懒加载
                    try:
                        pg.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                        pg.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass
                    pg.wait_for_timeout(int(wait_seconds * 1000))

                    title = pg.title()
                    raw_text = pg.evaluate("""() => {
                        const sel = document.querySelector(
                            'article, main, [role="main"], .article, .content, #article, #content'
                        );
                        const root = sel || document.body;
                        const clone = root.cloneNode(true);
                        clone.querySelectorAll(
                            'script,style,nav,footer,header,aside,form,iframe,' +
                            '[class*="sidebar"],[class*="comment"],[class*="ad-"],[role="complementary"]'
                        ).forEach(e => e.remove());
                        return clone.innerText;
                    }""")
                finally:
                    if pg:
                        try:
                            pg.close()
                        except Exception:
                            pass
                    try:
                        ctx.close()
                    except Exception:
                        pass
        except Exception as e:
            logger.exception(f"[BrowserTool] read_url failed: {e}")
            return {"error": f"读取失败: {e}", "url": url}

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

    # ─── 截图 ───

    def screenshot_url(self, url: str, width: int = 1280, height: int = 800,
                       wait_seconds: float = 3.0, full_page: bool = False) -> dict:
        err = self._validate_url(url)
        if err:
            return {"error": err}

        screenshot_bytes = None
        try:
            with self._session() as browser:
                ctx = self._new_context(browser, viewport={"width": width, "height": height})
                pg = None
                try:
                    pg = ctx.new_page()
                    pg.goto(url, timeout=15000, wait_until="domcontentloaded")

                    if full_page:
                        try:
                            pg.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                            pg.wait_for_load_state("networkidle", timeout=5000)
                        except Exception:
                            pass
                    pg.wait_for_timeout(int(wait_seconds * 1000))

                    screenshot_bytes = pg.screenshot(full_page=full_page, type="jpeg", quality=80)
                finally:
                    if pg:
                        try:
                            pg.close()
                        except Exception:
                            pass
                    try:
                        ctx.close()
                    except Exception:
                        pass
        except Exception as e:
            logger.exception(f"[BrowserTool] screenshot_url failed: {e}")
            return {"error": f"截图失败: {e}", "url": url}

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
