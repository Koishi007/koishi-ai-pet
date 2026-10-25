"""评论与私信内容过滤。

只做拒绝，不改写。拒绝原因是稳定 code，文案由 describe() 提供。
"""

from __future__ import annotations

import re

# 内部术语词表
_INTERNAL_TERMS = (
    "prompt", "token", "配置", "接口", "插件", "判重", "一轮", "上下文",
    "sdk", "api", "缓存", "日志", "设置页", "埋点",
)
# 身份暴露词表
_IDENTITY_TERMS = (
    "作为一个ai", "作为ai", "我是ai", "语言模型", "ai助手", "聊天机器人",
)
# 索取互动词表
_SOLICIT_TERMS = ("加群", "私信我", "关注我", "点赞我", "三连", "求关注")
# 敏感类别词表
_SENSITIVE_TERMS = (
    "政治", "选举", "游行", "示威", "台独", "港独", "分裂",
    "处方", "剂量", "停药", "治愈", "药到病除",
    "买入", "抄底", "梭哈", "稳赚", "理财产品", "股票代码",
    "起诉", "立案", "律师函",
)

_URL_RE = re.compile(r"(https?://|www\.|b23\.tv)", re.IGNORECASE)
_MENTION_RE = re.compile(r"@")
# 盘符前必须有边界
_PATH_RE = re.compile(r"(?<![A-Za-z0-9+.-])([A-Za-z]:[\\/]|/Users/|/home/|~/|\\\\)")
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_QQ_RE = re.compile(r"(?i)(qq\s*[:：]?\s*\d{5,}|qq\s*群)")
_NON_WORD_RE = re.compile(r"[^\w\u4e00-\u9fff]+")
_SPACE_RE = re.compile(r"\s+")

MIN_CHARS = 6
SIMILARITY_LIMIT = 0.8

REASON_TEXT = {
    "too_short": "内容过短",
    "too_long": "内容过长",
    "empty": "无实际内容",
    "url": "含链接",
    "mention": "含提及",
    "privacy": "含隐私信息",
    "identity": "暴露身份",
    "sensitive": "敏感话题",
    "solicit": "索取互动",
    "internal": "暴露内部术语",
    "blocklist": "命中禁用词",
    "duplicate": "内容重复",
}


def describe(code: str) -> str:
    return REASON_TEXT.get(code, code)


def normalize(text: str) -> str:
    return _SPACE_RE.sub("", text or "")


def similarity(a: str, b: str) -> float:
    """字符 2-gram Jaccard 相似度，用于挡住复读。"""
    ga = {a[i:i + 2] for i in range(len(a) - 1)}
    gb = {b[i:i + 2] for i in range(len(b) - 1)}
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def check_text(
    text: str,
    *,
    max_chars: int,
    blocklist: tuple[str, ...] = (),
    recent: tuple[str, ...] = (),
    allow_urls: bool = False,
) -> tuple[bool, str]:
    """返回 (是否通过, 拒绝 code)，code 取值见 REASON_TEXT。

    allow_urls 为真时放行链接，供私信使用。
    """
    body = normalize(text)
    if len(body) < MIN_CHARS:
        return False, "too_short"
    if len(body) > max(1, int(max_chars)):
        return False, "too_long"
    if not _NON_WORD_RE.sub("", body):
        return False, "empty"
    if not allow_urls and _URL_RE.search(body):
        return False, "url"
    if _MENTION_RE.search(body):
        return False, "mention"
    if _PATH_RE.search(body) or _IP_RE.search(body) or _EMAIL_RE.search(body) or _QQ_RE.search(body):
        return False, "privacy"
    lowered = body.lower()
    for term in _IDENTITY_TERMS:
        if term in lowered:
            return False, "identity"
    for term in _SENSITIVE_TERMS:
        if term in lowered:
            return False, "sensitive"
    for term in _SOLICIT_TERMS:
        if term in lowered:
            return False, "solicit"
    for term in _INTERNAL_TERMS:
        if term in lowered:
            return False, "internal"
    for term in blocklist:
        if term and term.lower() in lowered:
            return False, "blocklist"
    for old in recent:
        if old and similarity(body, normalize(old)) >= SIMILARITY_LIMIT:
            return False, "duplicate"
    return True, ""
