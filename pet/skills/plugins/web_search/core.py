"""网络搜索核心逻辑 — 支持 SearXNG（自建）和 Bing Web Search API 两种后端。"""

import json
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_PLUGIN_DIR = Path(__file__).parent
_CONFIG_FILE = _PLUGIN_DIR / "config.json"

_BING_ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"


def _load_config() -> dict:
    """读取插件本地 config.json 配置。"""
    if _CONFIG_FILE.is_file():
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[web_search] Failed to read config.json: {e}")
    return {}


# ── SearXNG 后端 ──────────────────────────────────────────────

def _search_searxng(query: str, count: int, language: str, cfg: dict) -> dict:
    """通过自建 SearXNG 实例搜索。"""
    base_url = cfg.get("searxng_url", "").rstrip("/")
    if not base_url:
        return {"summary": "搜索失败：未配置 searxng_url，请在 web_search/config.json 中设置"}

    params = {
        "q": query,
        "format": "json",
        "categories": "general",
        "language": language,
        "pageno": 1,
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
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.warning(f"[web_search] SearXNG error: {e}")
        return {"summary": f"搜索失败：SearXNG 请求异常 ({type(e).__name__})"}

    raw_results = data.get("results", [])
    if not raw_results:
        return {"summary": f"「{query}」未找到相关结果", "results": [], "query": query}

    results = []
    for item in raw_results[:count]:
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
            "engine": item.get("engine", ""),
            "publishedDate": item.get("publishedDate", ""),
        })

    lines = [f"「{query}」搜索结果（{len(results)}条）："]
    for i, r in enumerate(results, 1):
        date = r["publishedDate"]
        date_tag = f" [{date}]" if date else ""
        engine_tag = f" [{r['engine']}]" if r["engine"] else ""
        lines.append(f"  {i}. {r['title']}{date_tag}{engine_tag}\n     {r['snippet']}")

    return {
        "summary": "\n".join(lines),
        "results": results,
        "query": query,
    }


# ── Bing 后端 ────────────────────────────────────────────────

def _search_bing(query: str, count: int, market: str, cfg: dict) -> dict:
    """通过 Bing Web Search API 搜索。"""
    api_key = cfg.get("bing_search_key", "")
    if not api_key:
        return {"summary": "搜索失败：未配置 bing_search_key，请在 web_search/config.json 中设置"}

    count = max(1, min(count, 10))
    headers = {"Ocp-Apim-Subscription-Key": api_key}
    params = {
        "q": query,
        "count": count,
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
        return {"summary": f"「{query}」未找到相关结果", "results": [], "query": query}

    results = []
    for page in web_pages:
        results.append({
            "title": page.get("name", ""),
            "url": page.get("url", ""),
            "snippet": page.get("snippet", ""),
            "dateLastPublished": page.get("dateLastPublished", ""),
        })

    lines = [f"「{query}」搜索结果（{len(results)}条）："]
    for i, r in enumerate(results, 1):
        date = r["dateLastPublished"]
        date_tag = f" [{date}]" if date else ""
        lines.append(f"  {i}. {r['title']}{date_tag}\n     {r['snippet']}")

    return {
        "summary": "\n".join(lines),
        "results": results,
        "query": query,
        "total_matches": data.get("webPages", {}).get("totalEstimatedMatches", 0),
    }


# ── 统一入口 ──────────────────────────────────────────────────

def search(query: str, count: int = 5, language: str = "zh-CN") -> dict:
    """网络搜索 — 智能路由：优先使用 SearXNG，未配置/失败则 fallback 到 Bing。

    Args:
        query:    搜索关键词
        count:    返回结果数量（1-10）
        language: 语言/区域代码，如 zh-CN / en-US / ja-JP（SearXNG 用 language，Bing 用 market）
    """
    cfg = _load_config()
    backend = cfg.get("backend", "auto")

    # SearXNG 优先（backend=searxng 或 backend=auto）
    if backend in ("searxng", "auto"):
        searxng_url = cfg.get("searxng_url", "")
        if searxng_url:
            result = _search_searxng(query, count, language, cfg)
            if "搜索失败" not in str(result.get("summary", "")):
                return result
            logger.info("[web_search] SearXNG failed, falling back to Bing")

    # Bing fallback
    bing_key = cfg.get("bing_search_key", "")
    if bing_key:
        return _search_bing(query, count, language, cfg)

    # 两个后端都不可用
    searxng_ok = bool(cfg.get("searxng_url", ""))
    return {
        "summary": (
            "搜索失败：所有后端均不可用。"
            + ("SearXNG 已配置但请求失败。" if searxng_ok else "")
            + " 请在 web_search/config.json 中配置 searxng_url 或 bing_search_key"
        ),
        "results": [],
        "query": query,
    }