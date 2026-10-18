"""web_search 后端：DeepSeek 搜索与 auto 回退顺序。"""

import requests
import pytest

from pet.tools.web_search import core


class _Resp:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


def _deepseek_payload() -> dict:
    return {
        "results": [
            {
                "title": "DeepSeek V4 发布",
                "url": "https://example.com/v4",
                "snippet": "短摘要",
                "summary": "长摘要，面向模型阅读",
                "date": "2026-06-01T00:00:00+00:00",
                "site_name": "Example",
            }
        ]
    }


@pytest.fixture
def config(monkeypatch) -> dict:
    cfg = {"backend": "auto", "deepseek_search_key": "test-key", "bing_search_key": ""}
    monkeypatch.setattr(core, "_load_config", lambda: cfg)
    return cfg


def test_deepseek_result_format(config, monkeypatch):
    monkeypatch.setattr(core.requests, "post",
                        lambda *a, **kw: _Resp(200, _deepseek_payload()))
    summary = core.search("测试")["summary"]
    assert "DeepSeek V4 发布" in summary
    assert "长摘要，面向模型阅读" in summary
    assert "https://example.com/v4" in summary
    assert "[2026-06-01]" in summary
    assert "[Example]" in summary


def test_auto_uses_deepseek_when_searxng_absent(config, monkeypatch):
    monkeypatch.setattr(core.requests, "post",
                        lambda *a, **kw: _Resp(200, _deepseek_payload()))

    def _unexpected(*a, **kw):
        raise AssertionError("未配置 Bing 时不应请求 Bing")

    monkeypatch.setattr(core.requests, "get", _unexpected)
    result = core.search("测试", page=2)
    assert "https://example.com/v4" in result["summary"]
    assert result["has_next"] is False


def test_deepseek_failure_falls_back_to_bing(config, monkeypatch):
    config["bing_search_key"] = "bing-key"

    def _post(*a, **kw):
        raise requests.ConnectionError("boom")

    def _get(*a, **kw):
        return _Resp(200, {"webPages": {"value": [
            {"name": "Bing 结果", "url": "https://bing.example", "snippet": "摘要"},
        ]}})

    monkeypatch.setattr(core.requests, "post", _post)
    monkeypatch.setattr(core.requests, "get", _get)
    assert "https://bing.example" in core.search("测试")["summary"]


def test_deepseek_without_key_reports_failure(monkeypatch):
    monkeypatch.setattr(core, "_load_config",
                        lambda: {"backend": "deepseek", "deepseek_search_key": ""})
    assert "搜索失败" in core.search("测试")["summary"]


def test_deepseek_empty_results(config, monkeypatch):
    monkeypatch.setattr(core.requests, "post",
                        lambda *a, **kw: _Resp(200, {"results": []}))
    assert "未找到相关结果" in core.search("测试")["summary"]


def test_connectivity_detects_deepseek(monkeypatch):
    monkeypatch.setattr(core, "_load_config",
                        lambda: {"backend": "deepseek", "deepseek_search_key": "k"})
    monkeypatch.setattr(core.requests, "post", lambda *a, **kw: _Resp(200, {}))
    assert core.check_connectivity() is True


def test_fetch_uses_deepseek(config, monkeypatch):
    monkeypatch.setattr(core.requests, "post",
                        lambda *a, **kw: _Resp(200, {"title": "页面标题", "content": "正文内容"}))
    summary = core.fetch("https://example.com")["summary"]
    assert "页面标题" in summary
    assert "https://example.com" in summary
    assert "正文内容" in summary


def test_fetch_falls_back_to_local_extract(config, monkeypatch):
    def _post(*a, **kw):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(core.requests, "post", _post)
    monkeypatch.setattr(core, "_extract_text", lambda url, max_chars: "本地提取正文")
    assert "本地提取正文" in core.fetch("https://example.com")["summary"]


def test_fetch_rejects_bad_scheme(config):
    assert "抓取失败" in core.fetch("ftp://example.com/file")["summary"]


def _tavily_payload() -> dict:
    return {
        "results": [
            {
                "title": "Tavily 结果",
                "url": "https://tavily.example",
                "content": "相关片段",
                "published_date": "Tue, 11 Mar 2025 17:00:00 GMT",
                "score": 0.81,
            }
        ]
    }


def test_tavily_result_format(monkeypatch):
    monkeypatch.setattr(core, "_load_config",
                        lambda: {"backend": "tavily", "tavily_api_key": "tvly-test"})
    monkeypatch.setattr(core.requests, "post",
                        lambda *a, **kw: _Resp(200, _tavily_payload()))
    summary = core.search("测试")["summary"]
    assert "Tavily 结果" in summary
    assert "相关片段" in summary
    assert "https://tavily.example" in summary
    assert "[Tue, 11 Mar 2025 17:00:00 GMT]" in summary


def test_auto_prefers_tavily_over_deepseek(monkeypatch):
    monkeypatch.setattr(core, "_load_config", lambda: {
        "backend": "auto",
        "deepseek_search_key": "ds-key",
        "tavily_api_key": "tvly-test",
    })
    monkeypatch.setattr(core.requests, "post",
                        lambda *a, **kw: _Resp(200, _tavily_payload()))
    assert "https://tavily.example" in core.search("测试")["summary"]


def test_fetch_uses_tavily(monkeypatch):
    monkeypatch.setattr(core, "_load_config",
                        lambda: {"backend": "tavily", "tavily_api_key": "tvly-test"})
    monkeypatch.setattr(core.requests, "post", lambda *a, **kw: _Resp(200, {
        "results": [{"url": "https://example.com", "raw_content": "Tavily 正文"}],
        "failed_results": [],
    }))
    assert "Tavily 正文" in core.fetch("https://example.com")["summary"]


def test_connectivity_detects_tavily(monkeypatch):
    monkeypatch.setattr(core, "_load_config",
                        lambda: {"backend": "tavily", "tavily_api_key": "tvly-test"})
    monkeypatch.setattr(core.requests, "post", lambda *a, **kw: _Resp(200, {}))
    assert core.check_connectivity() is True


def test_connectivity_probes_all_configured(monkeypatch):
    """backend 显式指定时仍探测其他已配置的后端。"""
    urls = []

    def _post(*a, **kw):
        urls.append(a[0])
        return _Resp(200, {})

    monkeypatch.setattr(core, "_load_config", lambda: {
        "backend": "bing",
        "tavily_api_key": "tvly-test",
        "deepseek_search_key": "ds-key",
    })
    monkeypatch.setattr(core.requests, "post", _post)
    assert core.check_connectivity() is True
    assert core._TAVILY_SEARCH_ENDPOINT in urls
    assert core._DEEPSEEK_SEARCH_ENDPOINT in urls
