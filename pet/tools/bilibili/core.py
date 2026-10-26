"""B 站接口客户端：wbi 签名、请求、解析、写操作。

凭证取浏览器登录态里的四个 Cookie 字段；写操作额外用 bili_jct 作为 csrf。
请求串行执行，相邻两次之间至少间隔 interval 秒。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import random
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import httpx

from pet.settings import app_dir

logger = logging.getLogger(__name__)

_API = "https://api.bilibili.com"
_VC = "https://api.vc.bilibili.com"
_WEB = "https://www.bilibili.com"


def credential_path() -> Path:
    """登录态存到应用数据目录，重装或覆盖源码不会丢失。"""
    return Path(app_dir()) / "bilibili_credential.json"


def invalid_mark_path() -> Path:
    """登录失效标记与凭证同目录。"""
    return Path(app_dir()) / "bilibili_credential.invalid"


def mark_credential_invalid():
    """记录最近一次请求判定登录态失效。"""
    try:
        invalid_mark_path().write_text(time.strftime("%Y-%m-%dT%H:%M:%S"),
                                        encoding="utf-8")
    except OSError as exc:
        logger.warning(f"[bili] 写失效标记失败：{exc}")


def clear_credential_invalid():
    try:
        invalid_mark_path().unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(f"[bili] 清除失效标记失败：{exc}")


def credential_invalid() -> bool:
    """凭证是否在最近一次请求中失效；凭证文件更新后不再算失效。"""
    mark = invalid_mark_path()
    try:
        return mark.is_file() and mark.stat().st_mtime >= credential_path().stat().st_mtime
    except OSError:
        return False


def _harden_perms(path: Path):
    """把凭证限制成只有本人可读；收紧失败抛 OSError，不静默忽略。"""
    if sys.platform == "win32":
        user = os.environ.get("USERNAME") or ""
        if not user:
            raise OSError("无法确定当前用户名，不能收紧凭证权限")
        result = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True, timeout=10)
        if result.returncode != 0:
            detail = result.stderr.decode(errors="ignore").strip()
            raise OSError(f"icacls 收紧凭证权限失败：{detail or result.returncode}")
    else:
        path.chmod(0o600)


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": _WEB + "/",
    "Origin": _WEB,
}


def _video_referer(bvid: str) -> str:
    """互动请求的来源页：B 站校验来源页与目标视频是否一致。"""
    return f"{_WEB}/video/{bvid}/"

# wbi：64 项混入表重排后取前 32 位作为 mixin key
_MIXIN_TAB = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)
# 参数值在签名前剔除的字符，与 B 站前端的过滤规则一致
_STRIP_CHARS = "!'()*"
# 评论接口的 Web 端参数：reply/add 额外要求 wbi 与 dm 风控字段
_DM_ALPHABET = "ABCDEFGHIJK"
_DM_IMG_INTER = '{"ds":[],"wh":[0,0,0],"of":[0,0,0]}'
_COMMENT_WEB_LOCATION = 1550101
_COMMENT_STATISTICS = '{"appId":100,"platform":5}'
_VIEW_TTL_S = 1800.0
# 与 pet/file_intake/image.py 的 MAX_EDGE、file_ops 的 JPEG 质量与像素上限一致
_COVER_EDGE = 1536
_COVER_QUALITY = 95
_COVER_MAX_PIXELS = 40_000_000

_CRED_FIELDS = ("sessdata", "bili_jct", "dedeuserid", "buvid3", "buvid4",
                "ac_time_value")
_AUTH_CODES = (-101, -111)

_BVID_RE = re.compile(r"^BV[0-9A-Za-z]{10}$")
_VIDEO_BV_RE = re.compile(r"/video/(BV[0-9A-Za-z]{10})")


def is_bvid(text: str) -> bool:
    """BV 号形如 BV + 10 位字母数字。"""
    return bool(_BVID_RE.match((text or "").strip()))


class BiliError(RuntimeError):
    """接口失败。kind 取值 timeout / network / auth / risk / api / parse。"""

    def __init__(self, kind: str, message: str, code: int = 0):
        super().__init__(message)
        self.kind = kind
        self.code = code


def _as_int(value, default: int = 0) -> int:
    """接口字段转 int；非数字与 None 一律取默认值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_dict(value) -> dict:
    """接口字段取 dict；其余类型当空。"""
    return value if isinstance(value, dict) else {}


def _error_from_code(code: int, message: str) -> BiliError:
    if code in _AUTH_CODES:
        return BiliError("auth", f"{message or '登录状态失效'}（{code}）", code)
    if code == -412:
        return BiliError("risk", "请求被风控拦截（-412）", code)
    return BiliError("api", f"{message or '接口返回错误'}（{code}）", code)


@dataclass(frozen=True)
class Credential:
    """登录态。字段与 bilibili_api 的 Credential 对齐；bili_jct 同时是写接口的 csrf。"""

    sessdata: str = field(repr=False)
    bili_jct: str = field(repr=False)
    dedeuserid: str
    buvid3: str = ""
    buvid4: str = ""
    ac_time_value: str = field(default="", repr=False)

    @classmethod
    def from_file(cls, path: Path) -> "Credential | None":
        """读取凭证文件；文件缺失、损坏或字段不全时返回 None。"""
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(f"[bili] 凭证文件读不出来：{exc}")
            return None
        if not isinstance(data, dict):
            return None
        values = {k: str(data.get(k) or "") for k in _CRED_FIELDS}
        if not (values["sessdata"] and values["bili_jct"] and values["dedeuserid"]):
            return None
        return cls(**values)

    def save(self, path: Path):
        """写入凭证；权限收紧失败时删掉临时文件并抛出，旧凭证不受影响。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sessdata": self.sessdata,
            "bili_jct": self.bili_jct,
            "dedeuserid": self.dedeuserid,
            "buvid3": self.buvid3,
            "buvid4": self.buvid4,
            "ac_time_value": self.ac_time_value,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        data = json.dumps(payload, ensure_ascii=False, indent=2)
        # 先建 0600 的临时文件，收紧权限后再替换，避免旧凭证在可读状态下被覆盖
        tmp = path.with_name(f".{path.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(data)
            _harden_perms(tmp)
            os.replace(tmp, path)
            clear_credential_invalid()
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def cookie_header(self, buvid3: str = "", buvid4: str = "") -> str:
        pairs = {
            "SESSDATA": self.sessdata,
            "buvid3": buvid3 or self.buvid3,
            "buvid4": buvid4 or self.buvid4,
            "bili_jct": self.bili_jct,
            "DedeUserID": self.dedeuserid,
            "ac_time_value": self.ac_time_value,
        }
        return "; ".join(f"{k}={v}" for k, v in pairs.items() if v)


def mixin_key(img_key: str, sub_key: str) -> str:
    """按混入表重排 img_key 与 sub_key 的拼接串，取前 32 位。"""
    raw = img_key + sub_key
    return "".join(raw[i] for i in _MIXIN_TAB if i < len(raw))[:32]


def _clean_value(value: Any) -> str:
    text = str(value)
    for ch in _STRIP_CHARS:
        text = text.replace(ch, "")
    return text


def sign_params(params: dict, img_key: str, sub_key: str, wts: int) -> dict:
    """返回带 wts 与 w_rid 的新参数字典；原字典不变。"""
    signed = {str(k): str(v) for k, v in params.items()}
    signed["wts"] = str(wts)
    query = "&".join(
        f"{quote(k, safe='')}={quote(_clean_value(v), safe='')}"
        for k, v in sorted(signed.items())
    )
    signed["w_rid"] = hashlib.md5((query + mixin_key(img_key, sub_key)).encode()).hexdigest()
    return signed


def _dm_params() -> dict:
    """评论接口的 dm 风控参数：空鼠标轨迹与两项随机图像指纹。"""
    return {
        "dm_img_list": "[]",
        "dm_img_str": "".join(random.sample(_DM_ALPHABET, 2)),
        "dm_cover_img_str": "".join(random.sample(_DM_ALPHABET, 2)),
        "dm_img_inter": _DM_IMG_INTER,
        "web_location": _COMMENT_WEB_LOCATION,
    }


_buvid_pair: tuple[str, str] | None = None


def _device_buvid(http: httpx.Client) -> tuple[str, str]:
    """取一对匿名 buvid3/buvid4，进程内复用；失败返回空串。"""
    global _buvid_pair
    if _buvid_pair is not None:
        return _buvid_pair
    try:
        resp = http.get(f"{_API}/x/frontend/finger/spi", headers=dict(_HEADERS))
        data = _as_dict(_as_dict(resp.json()).get("data"))
    except (httpx.HTTPError, ValueError) as exc:
        logger.info(f"[bili] 取 buvid 失败：{exc}")
        return "", ""
    pair = (str(data.get("b_3") or ""), str(data.get("b_4") or ""))
    if pair[0] and pair[1]:
        _buvid_pair = pair
    return pair


def _file_stem(url: str) -> str:
    name = (url or "").rsplit("/", 1)[-1]
    return name.split(".", 1)[0]


def absolute_url(url: str) -> str:
    """补全协议相对地址并强制 https；空值原样返回。"""
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


def strip_em(text: str) -> str:
    """去掉搜索结果标题里的高亮标签与常见实体。"""
    out = (text or "").replace('<em class="keyword">', "").replace("</em>", "")
    return out.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")


def parse_search_items(data: dict) -> list[dict]:
    items = _as_dict(data).get("result") or []
    if not isinstance(items, list):
        return []
    videos = []
    for item in items:
        if not isinstance(item, dict):
            continue
        bvid = item.get("bvid") or ""
        if not bvid:
            continue
        videos.append({
            "bvid": bvid,
            "aid": _as_int(item.get("aid")),
            "title": strip_em(str(item.get("title") or "")),
            "author": item.get("author") or "",
            "play": _as_int(item.get("play")),
            "pubdate": _as_int(item.get("pubdate")),
            "duration": item.get("duration") or "",
            "description": strip_em(str(item.get("description") or ""))[:200],
        })
    return videos


def parse_mentions(data: dict, limit: int = 10) -> list[dict]:
    """解析 @ 通知，只保留视频；正文与跳转链接在 ``item.item`` 里。"""
    items = _as_dict(data).get("items") or []
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        detail = _as_dict(item.get("item"))
        match = _VIDEO_BV_RE.search(str(detail.get("uri") or ""))
        if not match:
            continue
        user = _as_dict(item.get("user"))
        result.append({
            "id": _as_int(item.get("id")),
            "uid": _as_int(user.get("mid")),
            "name": str(user.get("nickname") or ""),
            "bvid": match.group(1),
            "rpid": _as_int(detail.get("root_id") or detail.get("source_id")),
            "content": str(detail.get("source_content") or "")[:120],
        })
        if len(result) >= limit:
            break
    return result


def parse_view(data: dict) -> dict:
    data = _as_dict(data)
    owner = _as_dict(data.get("owner"))
    stat = _as_dict(data.get("stat"))
    return {
        "bvid": data.get("bvid") or "",
        "aid": _as_int(data.get("aid")),
        "cid": _as_int(data.get("cid")),
        "title": str(data.get("title") or ""),
        "up": owner.get("name") or "",
        "pubdate": _as_int(data.get("pubdate")),
        "duration": _as_int(data.get("duration")),
        "play": _as_int(stat.get("view")),
        "desc": str(data.get("desc") or "")[:200],
        "pic": absolute_url(str(data.get("pic") or "")),
    }


def pick_subtitle(subtitles: list[dict]) -> dict | None:
    """优先 AI 中文字幕，其次人工中文，最后取第一条。"""
    if not subtitles:
        return None
    for preferred in ("ai-zh", "zh-CN", "zh"):
        for item in subtitles:
            if isinstance(item, dict) and item.get("lan") == preferred:
                return item
    return subtitles[0] if isinstance(subtitles[0], dict) else None


def join_subtitle(body: list[dict]) -> str:
    lines = []
    for item in body or []:
        if not isinstance(item, dict):
            continue
        text = (item.get("content") or "").strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def within_days(pubdate: int, scan_days: int, now: float) -> bool:
    """发布时间晚于 now - scan_days 天；时间戳缺失时保留。"""
    if pubdate <= 0:
        return True
    return now - float(pubdate) <= max(1, int(scan_days)) * 86400


class BiliClient:
    """一个实例对应一次工具调用，用完即关。"""

    def __init__(
        self,
        credential: Credential | None = None,
        *,
        timeout: float = 15.0,
        interval: float = 3.0,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self._cred = credential
        self._clock = clock
        self._interval = max(0.0, float(interval))
        self._lock = threading.Lock()
        self._last = 0.0
        self._finger_done = False
        self._buvid3 = credential.buvid3 if credential else ""
        self._buvid4 = credential.buvid4 if credential else ""
        self._wbi: tuple[str, str] | None = None
        self._wbi_day: date | None = None
        self._view_cache: dict[str, tuple[float, dict]] = {}
        self.requests = 0
        self._http = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
            headers=dict(_HEADERS),
        )

    def __enter__(self) -> "BiliClient":
        return self

    def __exit__(self, *_exc):
        self.close()

    def close(self):
        self._http.close()

    def _wait(self):
        """串行限速：持锁 sleep，保证同一客户端的请求不并发。"""
        with self._lock:
            if self._interval <= 0:
                return
            delta = self._clock() - self._last
            if delta < self._interval:
                time.sleep(self._interval - delta)
            self._last = self._clock()

    def _cookie_header(self) -> str:
        if not self._cred:
            return self._anon_cookie_header()
        return self._cred.cookie_header(self._buvid3, self._buvid4)

    def _anon_cookie_header(self) -> str:
        """匿名视角的 cookie：只带设备 buvid，不含登录态。"""
        pairs = {"buvid3": self._buvid3, "buvid4": self._buvid4}
        return "; ".join(f"{k}={v}" for k, v in pairs.items() if v)

    def _ensure_fingerprint(self):
        """补齐 buvid3/buvid4；进程内复用同一对，失败不阻断请求。"""
        if self._finger_done:
            return
        self._finger_done = True
        if self._buvid3 and self._buvid4:
            return
        pair = _device_buvid(self._http)
        if pair[0]:
            self._buvid3, self._buvid4 = pair

    def _wbi_keys(self) -> tuple[str, str]:
        today = date.today()
        if self._wbi and self._wbi_day == today:
            return self._wbi
        data = self._request("GET", "/x/web-interface/nav", allow_error_codes=True)
        wbi_img = data.get("wbi_img") or {}
        img_key = _file_stem(wbi_img.get("img_url") or "")
        sub_key = _file_stem(wbi_img.get("sub_url") or "")
        if not (img_key and sub_key):
            raise BiliError("api", "wbi 密钥缺失")
        self._wbi = (img_key, sub_key)
        self._wbi_day = today
        return self._wbi

    def _sign(self, params: dict) -> dict:
        img_key, sub_key = self._wbi_keys()
        return sign_params(params, img_key, sub_key, int(self._clock()))

    def _raise_api(self, code: int, message: str):
        """统一抛接口错误；登录失效时留下标记，供账号面板显示。"""
        error = _error_from_code(code, message)
        if error.kind == "auth" and self._cred:
            mark_credential_invalid()
        raise error

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        data: dict | None = None,
        sign: bool = False,
        auth: bool = False,
        base: str = _API,
        allow_error_codes: bool = False,
        referer: str | None = None,
        anonymous: bool = False,
    ) -> dict:
        """统一请求入口：限速、签名、csrf 与错误映射。"""
        self._ensure_fingerprint()
        self._wait()
        if sign:
            params = self._sign(params or {})
        body = dict(data or {})
        if auth:
            if not self._cred:
                raise BiliError("auth", "未登录 B 站")
            body["csrf"] = self._cred.bili_jct
        headers = dict(_HEADERS)
        if referer:
            headers["Referer"] = referer
        cookie = self._anon_cookie_header() if anonymous else self._cookie_header()
        if cookie:
            headers["Cookie"] = cookie
        try:
            resp = self._http.request(method, base + path, params=params, data=body or None,
                                      headers=headers)
        except httpx.TimeoutException as exc:
            raise BiliError("timeout", f"请求超时：{exc}") from exc
        except httpx.HTTPError as exc:
            raise BiliError("network", f"请求失败：{exc}") from exc
        self.requests += 1
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if resp.status_code >= 400:
            code = _as_int(payload.get("code")) if isinstance(payload, dict) else 0
            if code:
                self._raise_api(code, payload.get("message") or "")
            raise BiliError("network", f"HTTP {resp.status_code}")
        if payload is None:
            raise BiliError("parse", "响应不是 JSON")
        if not isinstance(payload, dict):
            raise BiliError("parse", "响应结构不符")
        code = _as_int(payload.get("code"))
        if code != 0:
            if allow_error_codes:
                return payload.get("data") or {}
            self._raise_api(code, payload.get("message") or "")
        return payload.get("data") or {}

    def _get_json(self, url: str) -> dict:
        """CDN 上的字幕 JSON 不计入接口间隔。"""
        self._ensure_fingerprint()
        try:
            resp = self._http.get(url, headers=dict(_HEADERS))
        except httpx.TimeoutException as exc:
            raise BiliError("timeout", f"请求超时：{exc}") from exc
        except httpx.HTTPError as exc:
            raise BiliError("network", f"请求失败：{exc}") from exc
        self.requests += 1
        try:
            payload = resp.json()
        except ValueError as exc:
            raise BiliError("parse", "字幕响应不是 JSON") from exc
        if not isinstance(payload, dict):
            raise BiliError("parse", "字幕响应结构不符")
        return payload

    def _cached_view(self, bvid: str) -> dict:
        hit = self._view_cache.get(bvid)
        if hit and self._clock() - hit[0] < _VIEW_TTL_S:
            return hit[1]
        info = parse_view(self._request("GET", "/x/web-interface/view", params={"bvid": bvid}))
        self._view_cache[bvid] = (self._clock(), info)
        return info

    def whoami(self) -> dict:
        data = self._request("GET", "/x/web-interface/nav")
        return {
            "login": bool(data.get("isLogin")),
            "uid": int(data.get("mid") or 0),
            "name": data.get("uname") or "",
            "level": int((data.get("level_info") or {}).get("current_level") or 0),
            "coins": int(data.get("money") or 0),
            "fans": int(data.get("follower") or 0),
            "mobile_verified": bool(data.get("mobile_verified")),
        }

    def search(self, keyword: str, *, order: str = "pubdate", page: int = 1) -> list[dict]:
        data = self._request(
            "GET", "/x/web-interface/wbi/search/type",
            params={"search_type": "video", "keyword": keyword,
                    "page": max(1, int(page)), "order": order},
            sign=True,
        )
        return parse_search_items(data)

    def view(self, bvid: str) -> dict:
        return self._cached_view(bvid)

    def mentions(self, limit: int = 10) -> list[dict]:
        """最近 @ 我的通知。"""
        data = self._request("GET", "/x/msgfeed/at",
                             params={"platform": "web", "build": 0, "mobi_app": "web"})
        return parse_mentions(data, limit)

    def subtitle(self, bvid: str, cid: int = 0) -> dict | None:
        """返回 {language, text, segments}；无字幕返回 None。"""
        if not cid:
            cid = self._cached_view(bvid)["cid"]
        data = self._request("GET", "/x/player/wbi/v2",
                             params={"bvid": bvid, "cid": cid}, sign=True)
        subtitles = ((data.get("subtitle") or {}).get("subtitles")) or []
        target = pick_subtitle(subtitles)
        if not target:
            return None
        url = absolute_url(target.get("subtitle_url") or "")
        if not url:
            return None
        body = self._get_json(url).get("body") or []
        text = join_subtitle(body)
        if not text:
            return None
        return {"language": target.get("lan_doc") or "", "text": text, "segments": len(body)}

    def fetch_cover(self, url: str, edge: int = _COVER_EDGE) -> str:
        """下载封面并缩放到长边 edge，返回 base64；失败返回空串。CDN 请求不计入接口间隔。"""
        if not url:
            return ""
        try:
            resp = self._http.get(absolute_url(url), headers=dict(_HEADERS))
        except httpx.HTTPError as exc:
            logger.info(f"[bili] 封面下载失败：{exc}")
            return ""
        self.requests += 1
        if resp.status_code >= 400 or not resp.content:
            return ""
        try:
            from PIL import Image

            image = Image.open(io.BytesIO(resp.content))
            if image.size[0] * image.size[1] > _COVER_MAX_PIXELS:
                logger.info(f"[bili] 封面像素超限：{image.size}")
                return ""
            image = image.convert("RGB")
            image.thumbnail((edge, edge), Image.LANCZOS)
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=_COVER_QUALITY)
        except Exception as exc:
            logger.info(f"[bili] 封面解码失败：{exc}")
            return ""
        return base64.b64encode(buf.getvalue()).decode()

    def like(self, bvid: str, *, undo: bool = False) -> dict:
        return self._request("POST", "/x/web-interface/archive/like",
                             data={"bvid": bvid, "like": 0 if undo else 1}, auth=True,
                             referer=_video_referer(bvid))

    def coin(self, bvid: str) -> dict:
        return self._request("POST", "/x/web-interface/coin/add",
                             data={"bvid": bvid, "multiply": 1, "select_like": 0}, auth=True,
                             referer=_video_referer(bvid))

    def comment(self, aid: int, text: str, *, bvid: str = "",
                root: int = 0, parent: int = 0) -> int:
        """发评论；root 非 0 时回复该评论，parent 默认与 root 相同。"""
        payload = {
            "oid": int(aid), "type": 1, "message": text,
            "plat": 1, "from": 0,
            "statistics": _COMMENT_STATISTICS, "gaia_source": "main_web",
        }
        if self._cred:
            payload["csrf_token"] = self._cred.bili_jct
        if root:
            payload["root"] = int(root)
            payload["parent"] = int(parent or root)
        data = self._request("POST", "/x/v2/reply/add", params=_dm_params(),
                             data=payload, sign=True, auth=True,
                             referer=_video_referer(bvid) if bvid else None)
        rpid = int(data.get("rpid") or 0)
        if not rpid:
            raise BiliError("receipt", "评论回执缺少 rpid")
        return rpid

    def dm(self, uid: int, text: str) -> dict:
        if not self._cred:
            raise BiliError("auth", "未登录 B 站")
        content = json.dumps({"content": text}, ensure_ascii=False)
        data = self._request(
            "POST", "/web_im/v1/web_im/send_msg", base=_VC, auth=True,
            data={
                "msg[sender_uid]": self._cred.dedeuserid,
                "msg[receiver_id]": str(int(uid)),
                "msg[receiver_type]": "1",
                "msg[msg_type]": "1",
                "msg[msg_status]": "0",
                "msg[content]": content,
                "msg[timestamp]": str(int(self._clock())),
                "msg[dev_id]": "0",
            },
        )
        if not data.get("msg_key"):
            raise BiliError("receipt", "私信回执缺少 msg_key")
        return data

    def my_replies(self, aid: int, *, bvid: str = "", anonymous: bool = False) -> list[dict]:
        """顶层评论，按时间排序取第一页；anonymous 为真时用未登录视角。"""
        data = self._request("GET", "/x/v2/reply/main",
                             params={"oid": int(aid), "type": 1, "mode": 2, "next": 0},
                             auth=not anonymous,
                             referer=_video_referer(bvid) if bvid else None,
                             anonymous=anonymous)
        return data.get("replies") or []

    def comment_replies(self, aid: int, root: int, *, bvid: str = "",
                        anonymous: bool = False) -> list[dict]:
        """某条评论下的楼中楼；anonymous 为真时用未登录视角。"""
        data = self._request("GET", "/x/v2/reply/reply",
                             params={"oid": int(aid), "type": 1, "root": int(root),
                                     "ps": 20, "pn": 1},
                             auth=not anonymous,
                             referer=_video_referer(bvid) if bvid else None,
                             anonymous=anonymous)
        return data.get("replies") or []
