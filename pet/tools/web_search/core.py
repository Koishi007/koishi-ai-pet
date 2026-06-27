"""支持 SearXNG（自建）和 Bing Web Search API 两种后端。"""

import json
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

logging.getLogger("trafilatura").setLevel(logging.ERROR)

_TOOL_DIR = Path(__file__).parent
_CONFIG_FILE = _TOOL_DIR / "config.json"

_BING_ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"


def _load_config() -> dict:
    if _CONFIG_FILE.is_file():
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[web_search] Failed to read config.json: {e}")
    return {}


def _extract_text(url: str, max_chars: int = 2000) -> str:
    """从 URL 抓取正文文本（使用 trafilatura），失败返回空字符串。"""
    try:
        import trafilatura
    except ImportError:
        logger.debug("[web_search] trafilatura not installed, skip text extraction")
        return ""

    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return ""
        result = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )
        if not result:
            return ""
        if len(result) > max_chars:
            result = result[:max_chars] + "…"
        return result
    except Exception as e:
        logger.debug(f"[web_search] trafilatura extract failed for {url}: {e}")
        return ""



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


def check_connectivity() -> bool:
    """检测后端连通性，至少一个可用返回 True。"""
    cfg = _load_config()
    backend = cfg.get("backend", "auto")
    any_ok = False

    # 检测 SearXNG
    searxng_url = cfg.get("searxng_url", "").rstrip("/").removesuffix("/search")
    if searxng_url and backend in ("searxng", "auto"):
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
        if not searxng_url and not bing_key:
            logger.warning("[web_search] ⚠ 未配置任何搜索后端，搜索功能不可用")
        else:
            logger.warning("[web_search] ⚠ 所有搜索后端均不可达")

    return any_ok



def search(query: str, count: int = 5, language: str = "zh-CN", page: int = 1) -> dict:
    """网络搜索 — 智能路由：优先使用 SearXNG，未配置/失败则 fallback 到 Bing。

    Args:
        query:    搜索关键词
        count:    返回结果数量（1-10）
        language: 语言/区域代码，如 zh-CN / en-US / ja-JP（SearXNG 用 language，Bing 用 market）
        page:     页码，从1开始（第2页获取更多结果）
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
            logger.info("[web_search] SearXNG failed, falling back to Bing")

    bing_key = cfg.get("bing_search_key", "")
    if bing_key:
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
            + " 请在 web_search/config.json 中配置 searxng_url 或 bing_search_key"
        ),
    }


def deep_search(query: str, count: int = 5, language: str = "zh-CN",
                extract_top: int = 2, max_chars: int = 2000, page: int = 1) -> dict:
    """深度搜索 — 先搜索，再抓取前 N 条结果的正文摘要。

    相比 search() 只返回标题+snippet，deep_search 会访问搜索结果页面，
    提取正文文本，信息密度更高，适合需要具体细节的查询。

    Args:
        query:       搜索关键词
        count:       搜索结果数量（1-10）
        language:    语言/区域代码
        extract_top: 抓取正文的结果条数（1-3，越多越慢）
        max_chars:   每条正文最大字符数
        page:        页码，从1开始
    """
    base_result = search(query, count, language, page)
    summary = base_result.get("summary", "")
    if "搜索失败" in summary:
        return base_result

    urls = []
    for line in summary.split("\n"):
        line = line.strip()
        if line.startswith("http://") or line.startswith("https://"):
            urls.append(line)

    if not urls:
        return base_result

    extract_top = max(1, min(extract_top, 3, len(urls)))
    extra_lines = ["\n--- 深度搜索：页面正文 ---"]
    extracted = 0
    for url in urls:
        if extracted >= extract_top:
            break
        text = _extract_text(url, max_chars)
        if text:
            extra_lines.append(f"[{url}]\n{text}")
            extracted += 1

    if extracted == 0:
        extra_lines.append("(未能提取任何页面正文，请参考搜索摘要)")

    return {"summary": summary + "\n".join(extra_lines),
            "__context__": f"深度搜索「{query}」（提取{extracted}条正文）"}