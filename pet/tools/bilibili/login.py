"""B 站扫码登录：生成二维码、轮询状态、提取凭证。

轮询只返回状态与凭证，落盘与界面更新由调用方负责。
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlsplit

import httpx

from pet.tools.bilibili.core import Credential

logger = logging.getLogger(__name__)

_PASSPORT = "https://passport.bilibili.com"
_QR_GENERATE = "/x/passport-login/web/qrcode/generate"
_QR_POLL = "/x/passport-login/web/qrcode/poll"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}

# data.code 到内部状态的映射
_STATES = {
    0: "success",
    86101: "pending",
    86090: "scanning",
    86038: "expired",
}


def _client(timeout: float, transport: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(timeout=timeout, transport=transport, headers=dict(_HEADERS),
                        follow_redirects=True)


def generate(*, timeout: float = 15.0,
             transport: httpx.BaseTransport | None = None) -> dict:
    """返回 {url, key}；接口失败抛 RuntimeError。"""
    with _client(timeout, transport) as client:
        resp = client.get(_PASSPORT + _QR_GENERATE)
    try:
        payload = resp.json()
    except ValueError as exc:
        raise RuntimeError("二维码接口返回的不是 JSON") from exc
    data = payload.get("data") or {}
    url = str(data.get("url") or "")
    key = str(data.get("qrcode_key") or "")
    if not (url and key):
        raise RuntimeError(payload.get("message") or "二维码生成失败")
    return {"url": url, "key": key}


def _credential_from_cookies(resp: httpx.Response, fallback_url: str = "") -> Credential | None:
    jar = resp.cookies
    sessdata = jar.get("SESSDATA") or ""
    bili_jct = jar.get("bili_jct") or ""
    dedeuserid = jar.get("DedeUserID") or ""
    buvid3 = jar.get("buvid3") or ""
    ac_time_value = jar.get("ac_time_value") or ""
    if not (sessdata and bili_jct and dedeuserid) and fallback_url:
        query = parse_qs(urlsplit(fallback_url).query)
        sessdata = sessdata or (query.get("SESSDATA") or [""])[0]
        bili_jct = bili_jct or (query.get("bili_jct") or [""])[0]
        dedeuserid = dedeuserid or (query.get("DedeUserID") or [""])[0]
    if not (sessdata and bili_jct and dedeuserid):
        return None
    return Credential(sessdata=sessdata, bili_jct=bili_jct, dedeuserid=dedeuserid,
                      buvid3=buvid3, ac_time_value=ac_time_value)


def poll(key: str, *, timeout: float = 15.0,
         transport: httpx.BaseTransport | None = None) -> tuple[str, Credential | None]:
    """返回 (状态, 凭证)。状态取值 pending / scanning / success / expired / error。"""
    with _client(timeout, transport) as client:
        resp = client.get(_PASSPORT + _QR_POLL, params={"qrcode_key": key})
    try:
        payload = resp.json()
    except ValueError:
        return "error", None
    data = payload.get("data") or {}
    code = int(data.get("code") or 0)
    state = _STATES.get(code, "error")
    if state != "success":
        return state, None
    credential = _credential_from_cookies(resp, str(data.get("url") or ""))
    if credential is None:
        logger.warning("[bili] 扫码成功但没取到凭证")
        return "error", None
    return "success", credential
