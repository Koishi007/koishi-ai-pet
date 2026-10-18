"""支持 SearXNG（自建）、DeepSeek 搜索 API、Tavily 和 Bing Web Search API 四种后端。"""

import ipaddress
import json
import logging
import socket
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

_TOOL_DIR = Path(__file__).parent
_CONFIG_FILE = _TOOL_DIR / "config.json"

_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_BING_ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"
_DEEPSEEK_SEARCH_ENDPOINT = "https://api.deepseek.com/v1/tools/search"
_DEEPSEEK_FETCH_ENDPOINT = "https://api.deepseek.com/v1/tools/fetch"
_TAVILY_SEARCH_ENDPOINT = "https://api.tavily.com/search"
_TAVILY_EXTRACT_ENDPOINT = "https://api.tavily.com/extract"
# Tavily 的 language 只做排序加权，接受 ISO 639-1
_TAVILY_LANG = {"zh-CN": "zh-cn", "en-US": "en", "ja-JP": "ja"}


def _load_config() -> dict:
    if _CONFIG_FILE.is_file():
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[web_search] Failed to read config.json: {e}")
    return {}


class _PinnedHTTPSAdapter(HTTPAdapter):
    """HTTPS 连接固定到已校验的 IP，SNI 与证书校验保留原主机名。"""

    def __init__(self, server_hostname: str, **kwargs):
        self._server_hostname = server_hostname
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["server_hostname"] = self._server_hostname
        super().init_poolmanager(connections, maxsize, block, **pool_kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        proxy_kwargs["server_hostname"] = self._server_hostname
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def _is_public_ip(ip) -> bool:
    """全局可路由地址返回 True；6to4 地址按内嵌的 IPv4 再判一次。"""
    if not ip.is_global or ip.is_reserved or ip.is_multicast:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.sixtofour is not None:
        return _is_public_ip(ip.sixtofour)
    return True


def _resolve_public_ips(host: str, port: int) -> list[str]:
    """主机解析出的地址全部为公网时返回地址列表，否则返回空列表。"""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        return []
    ips = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return []
        if not _is_public_ip(ip):
            return []
        ips.append(str(ip))
    return ips


def _pin_target(url: str) -> tuple[str, str]:
    """把 URL 的主机换成解析出的公网 IP，返回 (固定后的 URL, 原主机)；不可抓取返回 ("", "")。"""
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return "", ""
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https") or not host:
        return "", ""
    ips = _resolve_public_ips(host, port)
    if not ips:
        return "", ""
    ip = f"[{ips[0]}]" if ":" in ips[0] else ips[0]
    pinned = urlunsplit((parts.scheme, f"{ip}:{port}", parts.path or "/", parts.query, ""))
    return pinned, host


def _url_reject_reason(url: str) -> str:
    """URL 不可抓取时返回原因，可抓取返回空字符串。"""
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return "url 格式无效"
    if parts.scheme not in ("http", "https"):
        return "url 必须是以 http:// 或 https:// 开头的地址"
    if not parts.hostname:
        return "url 缺少主机名"
    if not _resolve_public_ips(parts.hostname, port):
        return "url 指向内网或本机地址，已拒绝"
    return ""


def _download_html(url: str, timeout: int = 20) -> str:
    """下载网页 HTML：连接固定为校验过的公网 IP，重定向逐跳校验。"""
    for _ in range(4):
        pinned, host = _pin_target(url)
        if not pinned:
            logger.warning(f"[web_search] url rejected: {url}")
            return ""
        session = requests.Session()
        try:
            if pinned.startswith("https://"):
                session.mount("https://", _PinnedHTTPSAdapter(host))
            resp = session.get(
                pinned,
                timeout=timeout,
                allow_redirects=False,
                headers={"User-Agent": _USER_AGENT, "Host": host},
            )
        except requests.RequestException as e:
            logger.debug(f"[web_search] download failed for {url}: {e}")
            return ""
        finally:
            session.close()
        if resp.is_redirect:
            location = resp.headers.get("Location", "")
            if not location:
                return ""
            url = urljoin(url, location)
            continue
        if resp.status_code != 200:
            return ""
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding
        return resp.text
    return ""


def _extract_text(url: str, max_chars: int = 2000) -> str:
    """从 URL 抓取正文文本（使用 trafilatura 解析），失败返回空字符串。"""
    try:
        import trafilatura
    except ImportError:
        logger.debug("[web_search] trafilatura not installed, skip text extraction")
        return ""
    # 首次用到时才调低它自己的日志级别
    logging.getLogger("trafilatura").setLevel(logging.ERROR)

    html = _download_html(url)
    if not html:
        return ""
    try:
        result = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )
    except Exception as e:
        logger.debug(f"[web_search] trafilatura extract failed for {url}: {e}")
        return ""
    if not result:
        return ""
    if len(result) > max_chars:
        result = result[:max_chars] + "…"
    return result



def _search_searxng(query: str, count: int, language: str, cfg: dict, page: int = 1) -> dict:
    base_url = cfg.get("searxng_url", "").rstrip("/").removesuffix("/search")
    if not base_url:
        return {"summary": "搜索失败：未配置 searxng_url，请在 web_search/config.json 中设置"}

    params = {
        "q": query,
        "format": "json",
        "categories": "general",
        "language": language,
        "pageno": page,
    }

    # SearXNG 实例可能配置了 API key（settings.yml 中的 secret_key）
    api_key = cfg.get("searxng_key", "")
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        resp = requests.get(
            f"{base_url}/search",
            params=params,
            headers=headers,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.warning(f"[web_search] SearXNG error: {e}")
        return {"summary": f"搜索失败：SearXNG 请求异常 ({type(e).__name__})"}

    raw_results = data.get("results", [])
    if not raw_results:
        return {"summary": f"「{query}」未找到相关结果"}

    lines = [f"「{query}」搜索结果（{min(len(raw_results), count)}条）："]
    for i, item in enumerate(raw_results[:count], 1):
        title = item.get("title", "")
        url = item.get("url", "")
        snippet = item.get("content", "")
        date = item.get("publishedDate", "")
        engine = item.get("engine", "")
        date_tag = f" [{date}]" if date else ""
        engine_tag = f" [{engine}]" if engine else ""
        lines.append(f"  {i}. {title}{date_tag}{engine_tag}\n     {snippet}\n     {url}")

    return {"summary": "\n".join(lines)}



def _search_bing(query: str, count: int, market: str, cfg: dict, page: int = 1) -> dict:
    api_key = cfg.get("bing_search_key", "")
    if not api_key:
        return {"summary": "搜索失败：未配置 bing_search_key，请在 web_search/config.json 中设置"}

    count = max(1, min(count, 10))
    offset = (page - 1) * count
    headers = {"Ocp-Apim-Subscription-Key": api_key}
    params = {
        "q": query,
        "count": count,
        "offset": offset,
        "mkt": market,
        "responseFilter": "Webpages",
        "textDecorations": True,
        "safeSearch": "Moderate",
    }

    try:
        resp = requests.get(
            _BING_ENDPOINT,
            headers=headers,
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.warning(f"[web_search] Bing API error: {e}")
        return {"summary": f"搜索失败：网络请求异常 ({type(e).__name__})"}

    web_pages = data.get("webPages", {}).get("value", [])
    if not web_pages:
        return {"summary": f"「{query}」未找到相关结果"}

    lines = [f"「{query}」搜索结果（{len(web_pages)}条）："]
    for i, page in enumerate(web_pages, 1):
        title = page.get("name", "")
        url = page.get("url", "")
        snippet = page.get("snippet", "")
        date = page.get("dateLastPublished", "")
        date_tag = f" [{date}]" if date else ""
        lines.append(f"  {i}. {title}{date_tag}\n     {snippet}\n     {url}")

    return {"summary": "\n".join(lines)}


def _search_deepseek(query: str, count: int, language: str, cfg: dict, page: int = 1) -> dict:
    """DeepSeek 搜索 API：POST /v1/tools/search。

    接口不带页码与语言参数，page / language 不下发；需要限定语言时在 query 里写 lang: 操作符。
    """
    api_key = cfg.get("deepseek_search_key", "")
    if not api_key:
        return {"summary": "搜索失败：未配置 deepseek_search_key，请在 web_search/config.json 中设置"}

    count = max(1, min(count, 50))
    payload = {"query": query, "count": count, "need_content": False}

    try:
        resp = requests.post(
            _DEEPSEEK_SEARCH_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.warning(f"[web_search] DeepSeek API error: {e}")
        return {"summary": f"搜索失败：DeepSeek 请求异常 ({type(e).__name__})"}

    results = data.get("results", [])
    if not results:
        return {"summary": f"「{query}」未找到相关结果"}

    lines = [f"「{query}」搜索结果（{min(len(results), count)}条）："]
    for i, item in enumerate(results[:count], 1):
        title = item.get("title", "")
        url = item.get("url", "")
        # summary 面向模型阅读，缺失时退回 snippet
        snippet = item.get("summary") or item.get("snippet") or ""
        # date 为 RFC 3339，只取日期部分
        date = (item.get("date") or "")[:10]
        site = item.get("site_name") or ""
        date_tag = f" [{date}]" if date else ""
        site_tag = f" [{site}]" if site else ""
        lines.append(f"  {i}. {title}{date_tag}{site_tag}\n     {snippet}\n     {url}")

    return {"summary": "\n".join(lines)}


def _search_tavily(query: str, count: int, language: str, cfg: dict, page: int = 1) -> dict:
    """Tavily 搜索 API：POST /search。

    接口用 max_results 控制条数、没有页码，page 不下发；language 只做排序加权。
    """
    api_key = cfg.get("tavily_api_key", "")
    if not api_key:
        return {"summary": "搜索失败：未配置 tavily_api_key，请在 web_search/config.json 中设置"}

    payload = {
        "query": query,
        "max_results": max(1, min(count, 20)),
        "search_depth": "basic",
        "include_published_date": True,
    }
    lang = _TAVILY_LANG.get(language)
    if lang:
        payload["language"] = lang

    try:
        resp = requests.post(
            _TAVILY_SEARCH_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.warning(f"[web_search] Tavily API error: {e}")
        return {"summary": f"搜索失败：Tavily 请求异常 ({type(e).__name__})"}

    results = data.get("results", [])
    if not results:
        return {"summary": f"「{query}」未找到相关结果"}

    lines = [f"「{query}」搜索结果（{len(results)}条）："]
    for i, item in enumerate(results, 1):
        title = item.get("title", "")
        url = item.get("url", "")
        snippet = item.get("content", "")
        date = item.get("published_date") or ""
        date_tag = f" [{date}]" if date else ""
        lines.append(f"  {i}. {title}{date_tag}\n     {snippet}\n     {url}")

    return {"summary": "\n".join(lines)}


def check_connectivity() -> bool:
    """检测已配置的后端连通性，至少一个可用返回 True。

    配置了多个后端时全部探测；探测按调用优先级进行，DeepSeek 与 Tavily 会消耗额度。
    """
    cfg = _load_config()
    any_ok = False

    # 检测 SearXNG
    searxng_url = cfg.get("searxng_url", "").rstrip("/").removesuffix("/search")
    if searxng_url:
        api_key = cfg.get("searxng_key", "")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            resp = requests.get(
                f"{searxng_url}/search",
                params={"q": "test", "format": "json", "pageno": 1},
                headers=headers,
                timeout=20,
            )
            if resp.status_code == 200:
                data = resp.json()
                n = len(data.get("results", []))
                unresponsive = data.get("unresponsive_engines", [])
                if n > 0:
                    logger.info(f"[web_search] ✓ SearXNG 连通正常 → {searxng_url} ({n} 条)")
                    any_ok = True
                else:
                    engines = ", ".join(e[0] for e in unresponsive[:5]) if unresponsive else "未知"
                    logger.warning(
                        f"[web_search] ✗ SearXNG 可达但无结果 → {searxng_url} "
                        f"(所有引擎不可用: {engines})"
                    )
            else:
                logger.warning(
                    f"[web_search] ✗ SearXNG 响应异常 → {searxng_url} "
                    f"(HTTP {resp.status_code})"
                )
        except requests.RequestException as e:
            logger.warning(
                f"[web_search] ✗ SearXNG 无法连接 → {searxng_url} ({e})"
            )

    # 检测 Tavily
    tavily_key = cfg.get("tavily_api_key", "")
    if tavily_key:
        try:
            resp = requests.post(
                _TAVILY_SEARCH_ENDPOINT,
                headers={"Authorization": f"Bearer {tavily_key}"},
                json={"query": "test", "max_results": 1, "search_depth": "basic"},
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info("[web_search] ✓ Tavily 搜索 API 连通正常")
                any_ok = True
            else:
                logger.warning(
                    f"[web_search] ✗ Tavily 搜索 API 响应异常 (HTTP {resp.status_code})"
                )
        except requests.RequestException as e:
            logger.warning(f"[web_search] ✗ Tavily 搜索 API 无法连接 ({e})")

    # 检测 DeepSeek
    deepseek_key = cfg.get("deepseek_search_key", "")
    if deepseek_key:
        try:
            resp = requests.post(
                _DEEPSEEK_SEARCH_ENDPOINT,
                headers={"Authorization": f"Bearer {deepseek_key}"},
                json={"query": "test", "count": 1},
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info("[web_search] ✓ DeepSeek 搜索 API 连通正常")
                any_ok = True
            else:
                logger.warning(
                    f"[web_search] ✗ DeepSeek 搜索 API 响应异常 (HTTP {resp.status_code})"
                )
        except requests.RequestException as e:
            logger.warning(f"[web_search] ✗ DeepSeek 搜索 API 无法连接 ({e})")

    # 检测 Bing
    bing_key = cfg.get("bing_search_key", "")
    if bing_key:
        try:
            resp = requests.get(
                _BING_ENDPOINT,
                headers={"Ocp-Apim-Subscription-Key": bing_key},
                params={"q": "test", "count": 1, "mkt": "en-US"},
                timeout=5,
            )
            if resp.status_code == 200:
                logger.info("[web_search] ✓ Bing API 连通正常")
                any_ok = True
            else:
                logger.warning(
                    f"[web_search] ✗ Bing API 响应异常 (HTTP {resp.status_code})"
                )
        except requests.RequestException as e:
            logger.warning(f"[web_search] ✗ Bing API 无法连接 ({e})")

    if not any_ok:
        if not searxng_url and not bing_key and not deepseek_key and not tavily_key:
            logger.warning("[web_search] ⚠ 未配置任何搜索后端，搜索功能不可用")
        else:
            logger.warning("[web_search] ⚠ 所有搜索后端均不可达")

    return any_ok



def search(query: str, count: int = 5, language: str = "zh-CN", page: int = 1) -> dict:
    """网络搜索 — 智能路由：优先使用 SearXNG，未配置/失败则依次回退 DeepSeek、Bing。

    Args:
        query:    搜索关键词
        count:    返回结果数量（1-10）
        language: 语言/区域代码，如 zh-CN / en-US / ja-JP（SearXNG 用 language，Bing 用 market，DeepSeek 忽略）
        page:     页码，从1开始（第2页获取更多结果；DeepSeek 后端不支持翻页）
    """
    cfg = _load_config()
    backend = cfg.get("backend", "auto")

    if backend in ("searxng", "auto"):
        searxng_url = cfg.get("searxng_url", "")
        if searxng_url:
            result = _search_searxng(query, count, language, cfg, page)
            if "搜索失败" not in str(result.get("summary", "")):
                result["page"] = page
                result["has_next"] = len(result.get("summary", "").split("\n")) > 1
                result["__context__"] = f"搜索「{query}」（第{page}页）"
                return result
            logger.info("[web_search] SearXNG failed, falling back to Tavily")

    tavily_key = cfg.get("tavily_api_key", "")
    if backend in ("tavily", "auto") and tavily_key:
        result = _search_tavily(query, count, language, cfg, page)
        if "搜索失败" not in str(result.get("summary", "")):
            result["page"] = page
            result["has_next"] = False
            result["__context__"] = f"搜索「{query}」"
            return result
        logger.info("[web_search] Tavily failed, falling back to DeepSeek")

    deepseek_key = cfg.get("deepseek_search_key", "")
    if backend in ("deepseek", "auto") and deepseek_key:
        result = _search_deepseek(query, count, language, cfg, page)
        if "搜索失败" not in str(result.get("summary", "")):
            result["page"] = page
            result["has_next"] = False
            result["__context__"] = f"搜索「{query}」"
            return result
        logger.info("[web_search] DeepSeek failed, falling back to Bing")

    bing_key = cfg.get("bing_search_key", "")
    if backend in ("bing", "auto") and bing_key:
        result = _search_bing(query, count, language, cfg, page)
        result["page"] = page
        result["has_next"] = len(result.get("summary", "").split("\n")) > 1
        result["__context__"] = f"搜索「{query}」（第{page}页）"
        return result

    searxng_ok = bool(cfg.get("searxng_url", ""))
    return {
        "summary": (
            "搜索失败：所有后端均不可用。"
            + ("SearXNG 已配置但请求失败。" if searxng_ok else "")
            + " 请在 web_search/config.json 中配置 searxng_url、tavily_api_key、deepseek_search_key 或 bing_search_key"
        ),
    }


def _fetch_deepseek(url: str, cfg: dict) -> dict:
    """DeepSeek 抓取 API：POST /v1/tools/fetch，返回标题与 Markdown 正文。"""
    api_key = cfg.get("deepseek_search_key", "")
    if not api_key:
        return {}
    try:
        resp = requests.post(
            _DEEPSEEK_FETCH_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"url": url},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.info(f"[web_search] DeepSeek fetch failed for {url}: {e}")
        return {}
    content = data.get("content", "")
    if not content:
        return {}
    return {"title": data.get("title", ""), "content": content}


def _fetch_tavily(url: str, cfg: dict) -> dict:
    """Tavily 抓取 API：POST /extract，返回 Markdown 正文。"""
    api_key = cfg.get("tavily_api_key", "")
    if not api_key:
        return {}
    try:
        resp = requests.post(
            _TAVILY_EXTRACT_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"urls": [url], "extract_depth": "basic", "format": "markdown"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.info(f"[web_search] Tavily extract failed for {url}: {e}")
        return {}
    for item in data.get("results", []):
        content = item.get("raw_content")
        if content:
            return {"content": content}
    return {}


def fetch(url: str, max_chars: int = 10000) -> dict:
    """抓取网页正文 — 按 backend 依次尝试 DeepSeek Fetch、Tavily Extract，失败回退 trafilatura。

    Args:
        url:       网页地址（含 http:// 或 https://）
        max_chars: 正文最大字符数
    """
    reason = _url_reject_reason(url)
    if reason:
        return {"summary": f"抓取失败：{reason}"}

    cfg = _load_config()
    backend = cfg.get("backend", "auto")

    providers = []
    if backend in ("tavily", "auto") and cfg.get("tavily_api_key"):
        providers.append(_fetch_tavily)
    if backend in ("deepseek", "auto") and cfg.get("deepseek_search_key"):
        providers.append(_fetch_deepseek)

    for provider in providers:
        page = provider(url, cfg)
        if page:
            text = page["content"]
            if len(text) > max_chars:
                text = text[:max_chars] + "…"
            title = page.get("title", "")
            head = f"{title}\n{url}" if title else url
            return {"summary": f"{head}\n{text}",
                    "__context__": f"抓取网页「{title or url}」"}

    text = _extract_text(url, max_chars)
    if not text:
        return {"summary": f"抓取失败：未能从 {url} 提取正文"}
    return {"summary": f"{url}\n{text}", "__context__": f"抓取网页 {url}"}